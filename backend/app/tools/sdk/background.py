"""Background task manager + TaskOutput / TaskStop / TaskList / Monitor / TodoWrite.

Mirrors norma tool/task.go, taskoutput.go, taskstop.go, tasklist.go, monitor.go, todo.go.
Tasks are scoped per ToolContext.task_id (None = scratch scope); ids are ``task_N`` per scope.
Output goes to ``<workdir>/.bg/task_N.output`` (combined stdout+stderr).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.tools.base import Tool, ToolContext, ToolError
from app.tools.sdk.safeenv import child_env

log = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 50 << 20  # a task writing more than this is terminated (norma maxTaskOutputBytes)
DEFAULT_MAX_BYTES = 30000
DEFAULT_WAIT_MS = 30000
TERMINAL = ("completed", "failed", "killed")
TODO_ACK = "Todos have been modified successfully. Keep the todo list current and continue with the tasks."


def short(s: str) -> str:
    s = " ".join(s.strip().split("\n"))
    return s if len(s) <= 60 else s[:57] + "..."


def kill_tree(pid: int, sig: int = signal.SIGKILL) -> None:
    """Kill the whole process group of pid (children are started with start_new_session)."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)


@dataclass
class BgTask:
    id: str
    kind: str
    command: str
    description: str
    output_path: Path
    status: str = "running"
    exit_code: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    oversize: bool = False
    proc: asyncio.subprocess.Process | None = None
    watcher: asyncio.Task | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


class Scope:
    """Per-task-id state: background tasks and interactive shell sessions."""

    def __init__(self, pool: Any, task_id: int | None) -> None:
        self.pool = pool
        self.task_id = task_id
        self.tasks: dict[str, BgTask] = {}
        self.order: list[str] = []
        self.seq = 0
        self.sessions: dict[str, Any] = {}
        self.sess_seq = 0

    async def note(self, tool_name: str, text: str, data: Any = None) -> None:
        """Log a task_events note (notification equivalent). Never raises."""
        if self.task_id is None or self.pool is None:
            return
        try:
            from app.tasks import service as svc
            await svc.add_event(self.pool, self.task_id, "note", tool_name, data, {"text": text})
        except Exception as e:  # noqa: BLE001 - logging must never break a tool
            log.debug("note failed: %s", e)


_scopes: dict[int | None, Scope] = {}
_todos: dict[int | None, list[dict]] = {}


def scope_for(ctx: ToolContext) -> Scope:
    sc = _scopes.get(ctx.task_id)
    if sc is None:
        sc = _scopes[ctx.task_id] = Scope(ctx.pool, ctx.task_id)
    else:
        sc.pool = ctx.pool or sc.pool
    return sc


async def _watch(sc: Scope, t: BgTask) -> None:
    proc = t.proc
    assert proc is not None
    stop = asyncio.Event()

    async def size_dog() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 2.0)
            except TimeoutError:
                with contextlib.suppress(OSError):
                    if t.output_path.stat().st_size > MAX_OUTPUT_BYTES and t.status == "running":
                        t.status, t.oversize = "failed", True
                        kill_tree(proc.pid)
                        return

    dog = asyncio.create_task(size_dog())
    try:
        rc = await proc.wait()
    finally:
        stop.set()
        dog.cancel()
    if t.status == "running":
        t.exit_code = rc
        t.status = "completed" if rc == 0 else "failed"
    elif t.oversize:
        t.exit_code = rc
    t.end_time = time.time()
    if t.status in ("completed", "failed"):
        await sc.note("TaskOutput", f"{t.kind} {t.id} {t.status} (exit {t.exit_code}): {short(t.command)}",
                      {"task_id": t.id, "output_file": str(t.output_path)})


async def _spawn(ctx: ToolContext, command: str, description: str, kind: str) -> BgTask:
    sc = scope_for(ctx)
    d = ctx.workdir() / ".bg"
    d.mkdir(parents=True, exist_ok=True)
    sc.seq += 1
    tid = f"task_{sc.seq}"
    out = d / f"{tid}.output"
    f = open(out, "wb")  # noqa: SIM115, ASYNC230 - handed to the child
    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", command, stdin=asyncio.subprocess.DEVNULL, stdout=f, stderr=f, env=child_env(),
            cwd=str(ctx.workdir()), start_new_session=True)
    except Exception as e:  # noqa: BLE001 - reported as ToolError
        f.close()
        with contextlib.suppress(OSError):
            out.unlink()
        raise ToolError(f"failed to start {kind}: {e}")
    f.close()
    t = BgTask(tid, kind, command, description, out, proc=proc)
    sc.tasks[tid] = t
    sc.order.append(tid)
    t.watcher = asyncio.create_task(_watch(sc, t))
    return t


async def start(ctx: ToolContext, command: str, description: str = "", kind: str = "bash") -> dict:
    """Start ``command`` in the background. Returns {task_id:'task_N', output_file:str}."""
    t = await _spawn(ctx, command, description, kind)
    return {"task_id": t.id, "output_file": str(t.output_path)}


def _get(ctx: ToolContext, task_id: str) -> tuple[Scope, BgTask]:
    sc = scope_for(ctx)
    t = sc.tasks.get(task_id)
    if t is None:
        raise ToolError(f"Error: unknown task {json.dumps(task_id)}")
    return sc, t


def tail_file(path: Path, max_bytes: int) -> str:
    max_bytes = max_bytes if max_bytes > 0 else DEFAULT_MAX_BYTES
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size <= max_bytes:
            return fh.read().decode("utf-8", "replace")
        off = size - max_bytes
        fh.seek(off)
        return (f"[{off // 1024} KB of earlier output omitted — read the output file directly for full output]\n"
                + fh.read().decode("utf-8", "replace"))


async def read(ctx: ToolContext, task_id: str, max_bytes: int = 0, block: bool = False,
               timeout_ms: int = 0) -> str:
    _, t = _get(ctx, task_id)
    if block and not t.terminal:
        end = time.monotonic() + (timeout_ms if timeout_ms > 0 else DEFAULT_WAIT_MS) / 1000
        while not t.terminal and time.monotonic() < end:
            await asyncio.sleep(0.05)
        if t.watcher and t.terminal:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(t.watcher), 1.0)
    try:
        out = tail_file(t.output_path, max_bytes)
    except OSError as e:
        raise ToolError(f"Error: {e}")
    header = f"[task {t.id} | {t.status}"
    if t.status in ("completed", "failed"):
        header += f" | exit {t.exit_code}"
    return header + "]\n" + out


async def stop(ctx: ToolContext, task_id: str = "") -> str:
    sc = scope_for(ctx)
    if not task_id:
        n = 0
        for t in list(sc.tasks.values()):
            if _terminate(t):
                n += 1
        return f"Stopped {n} running task(s)."
    _, t = _get(ctx, task_id)
    _terminate(t)
    return f"Stopped task {task_id}."


def _terminate(t: BgTask) -> bool:
    if t.status != "running":
        return False
    t.status = "killed"
    if t.proc:
        kill_tree(t.proc.pid)
    return True


def list_tasks(ctx: ToolContext) -> str:
    sc = scope_for(ctx)
    if not sc.order:
        return "No background tasks."
    lines = []
    for tid in sc.order:
        t = sc.tasks[tid]
        s = f"{t.id} [{t.kind}] {t.status}"
        if t.status in ("completed", "failed"):
            s += f" exit={t.exit_code}"
        lines.append(f"{s}  {short(t.command)}")
    return "\n".join(lines) + "\n"


async def shutdown() -> None:
    """Kill every background task and interactive session (app shutdown)."""
    for sc in list(_scopes.values()):
        for t in sc.tasks.values():
            _terminate(t)
        for s in list(sc.sessions.values()):
            with contextlib.suppress(Exception):
                s.kill()
        sc.sessions.clear()
    for sc in list(_scopes.values()):
        for t in sc.tasks.values():
            if t.watcher and not t.watcher.done():
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(t.watcher), 1.0)


def reset() -> None:
    """Forget all state (tests)."""
    _scopes.clear()
    _todos.clear()


# ---------------------------------------------------------------- tools

def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


async def _task_output(ctx: ToolContext, a: dict) -> Any:
    tid = a.get("task_id") or ""
    if not tid:
        raise ToolError("Error: task_id is required")
    return await read(ctx, tid, int(a.get("max_bytes") or 0), bool(a.get("block")), int(a.get("timeout_ms") or 0))


async def _task_stop(ctx: ToolContext, a: dict) -> Any:
    return await stop(ctx, a.get("task_id") or "")


async def _task_list(ctx: ToolContext, a: dict) -> Any:
    return list_tasks(ctx)


async def _monitor(ctx: ToolContext, a: dict) -> Any:
    cmd = a.get("command") or ""
    if not cmd.strip():
        raise ToolError("Error: command is required")
    t = await _spawn(ctx, cmd, a.get("description", ""), "monitor")
    return (f"Started monitor {t.id}. Output file: {t.output_path}\n"
            f"Use TaskOutput{{task_id:{json.dumps(t.id)}}} or Read to inspect it; "
            f"TaskStop{{task_id:{json.dumps(t.id)}}} to end it.")


def todos(task_id: int | None) -> list[dict]:
    return list(_todos.get(task_id, []))


def render_todo_reminder(items: list[dict]) -> str:
    return "\n".join(f"{i}. [{t['status']}] {t['content']}" for i, t in enumerate(items, 1))


async def _todo_write(ctx: ToolContext, a: dict) -> Any:
    items = [{"content": str(t["content"]), "status": t["status"]} for t in a["todos"]]
    _todos[ctx.task_id] = items
    await scope_for(ctx).note("TodoWrite", render_todo_reminder(items), {"todos": items})
    return TODO_ACK


_STATUS = ["pending", "in_progress", "completed"]

TOOLS: list[Tool] = [
    Tool("TaskOutput",
         "Reads the output of a background task started by Bash (run_in_background) or Monitor. You can also Read "
         "the task's output-file path directly. Set block=true to wait for the task to finish first.",
         _obj({"task_id": {"type": "string", "description": "The task id (e.g. task_1)."},
               "max_bytes": {"type": "integer", "description": "Max bytes of output to return from the tail (default 30000)."},
               "block": {"type": "boolean", "description": "Wait until the task finishes before returning (default false)."},
               "timeout_ms": {"type": "integer", "description": "Max wait when block=true (default 30000)."}},
              ["task_id"]),
         _task_output, True, {"category": "task"}),
    Tool("TaskStop",
         "Stops a running background task, killing its process tree. Pass a task_id, or omit it to stop all running tasks.",
         _obj({"task_id": {"type": "string", "description": "The id of the task to stop. Empty stops all running tasks."}}),
         _task_stop, False, {"category": "task"}),
    Tool("TaskList",
         "Lists the background tasks started in this session with their status, exit code, and command.",
         _obj({}), _task_list, True, {"category": "task"}),
    Tool("Monitor",
         "Starts a long-running streaming process in the background (e.g. tail -f, watch, a dev server, a polling "
         "loop) and returns immediately with a task id and output-file path. Use Read or TaskOutput to inspect it "
         "and TaskStop to end it.",
         _obj({"command": {"type": "string", "description": "The streaming command to run."},
               "description": {"type": "string", "description": "Short description of what is being monitored."}},
              ["command"]),
         _monitor, False, {"category": "exec"}),
    Tool("TodoWrite",
         "Records and updates a structured todo list for the current task. Use it to plan multi-step work and to "
         "mark progress — replace the whole list each call, marking exactly one item in_progress while working.",
         _obj({"todos": {"type": "array", "items": {
             "type": "object",
             "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": _STATUS}},
             "required": ["content", "status"]}}}, ["todos"]),
         _todo_write, False, {"category": "task"}),
]

"""Bash tool mirroring ARTEX/norma: shell command with timeout, combined output, background support."""
from __future__ import annotations

import asyncio
import os
import re
import signal
from typing import Any

from app.tools.base import Tool, ToolContext, ToolError
from app.tools.sdk.safeenv import child_env

from .fs import capture

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000

# Safety floor from norma (irreversibly destructive commands), not a sandbox.
_CATASTROPHIC = [
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*/(\s|$)"),
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;"),
    re.compile(r"\bmkfs\.\w+\s+/dev/"),
    re.compile(r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|hd)"),
]


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _bash(ctx: ToolContext, a: dict) -> Any:
    command = a.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("Error: command is required")
    for rx in _CATASTROPHIC:
        if rx.search(command):
            raise ToolError("denied: command appears irreversibly destructive and is blocked by the safety floor")
    try:
        tms = int(a.get("timeout_ms") or 0)
    except (TypeError, ValueError):
        tms = 0
    tms = DEFAULT_TIMEOUT_MS if tms <= 0 else min(tms, MAX_TIMEOUT_MS)
    timeout = tms / 1000

    if a.get("run_in_background"):
        try:
            from . import background
        except ImportError:
            raise ToolError("Error: background tasks are not available")
        info = await background.start(ctx, command)
        tid, path = info.get("task_id"), info.get("output_file")
        return (f"Running in background as {tid}. Output file: {path}\n"
                f"Use TaskOutput{{task_id:\"{tid}\"}} or Read to read its output; "
                "you are notified when it completes.")

    proc = await asyncio.create_subprocess_exec(
        "/bin/bash", "-lc", command, cwd=str(ctx.workdir()), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        env=child_env())
    try:
        out_b = await asyncio.wait_for(_drain(proc), timeout)
    except TimeoutError:
        _kill_group(proc)
        try:
            out_b = await asyncio.wait_for(_drain(proc), 5)
        except (TimeoutError, OSError):
            out_b = _PARTIAL[id(proc)].value() if id(proc) in _PARTIAL else b""
        out = capture(out_b.decode("utf-8", errors="replace"))
        raise ToolError(out + f"\n\n[command timed out after {timeout:g}s]")
    except asyncio.CancelledError:
        _kill_group(proc)
        raise
    finally:
        _PARTIAL.pop(id(proc), None)
    out = capture(out_b.decode("utf-8", errors="replace"))
    if proc.returncode != 0:
        if not out:
            out = "(no output)"
        raise ToolError(f"{out}\n\n[exit code {proc.returncode}]")
    return out if out.strip() else "(command produced no output)"


KEEP_BYTES = 4 << 20  # bounded in-memory capture: head + tail are kept, the middle is dropped
class _Buf:
    def __init__(self) -> None:
        self.head, self.tail, self.dropped = bytearray(), bytearray(), 0

    def add(self, chunk: bytes) -> None:
        half = KEEP_BYTES // 2
        if len(self.head) < half:
            take = half - len(self.head)
            self.head += chunk[:take]
            chunk = chunk[take:]
        if chunk:
            self.tail += chunk
            if len(self.tail) > half:
                self.dropped += len(self.tail) - half
                del self.tail[: len(self.tail) - half]

    def value(self) -> bytes:
        marker = f"\n...[{self.dropped} bytes dropped]...\n".encode() if self.dropped else b""
        return bytes(self.head) + marker + bytes(self.tail)


_PARTIAL: dict[int, _Buf] = {}


async def _drain(proc: asyncio.subprocess.Process) -> bytes:
    """Read combined output with bounded memory (resumable after a timeout), then reap the process."""
    buf = _PARTIAL.setdefault(id(proc), _Buf())
    assert proc.stdout is not None
    while True:
        chunk = await proc.stdout.read(65536)
        if not chunk:
            break
        buf.add(chunk)
    await proc.wait()
    return buf.value()


TOOLS: list[Tool] = [
    Tool(
        "Bash",
        "Executes a shell command in the working directory and returns combined stdout and stderr. Use for "
        "running tests, builds, package managers, and git. Prefer the dedicated file tools for file work. "
        "Set run_in_background to return immediately and read the output later. The command runs through the "
        "shell, so you are not limited to one command: chain steps with && or ;, use pipes, and pass multi-line "
        "scripts or heredocs. For anything beyond a simple one-liner — loops, parsing, string/JSON handling, HTTP "
        "logic, computation — prefer writing a short script and executing it. A timeout (default 120s, max 600s) "
        "applies. Avoid commands needing interactive input.",
        {"type": "object", "properties": {
            "command": {"type": "string", "description": "The shell command to execute."},
            "timeout_ms": {"type": "integer", "description": "Timeout in ms (default 120000, max 600000)."},
            "run_in_background": {"type": "boolean", "description": "Run in the background and return "
                                  "immediately; read its output later with TaskOutput or Read."}},
         "required": ["command"]},
        _bash, False, {"category": "exec"}),
]

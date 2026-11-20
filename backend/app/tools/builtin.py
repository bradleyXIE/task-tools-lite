"""Built-in tools: thin wrappers over app.tasks.service."""
from __future__ import annotations

from typing import Any

from app.tasks import service as svc

from .base import Tool, ToolContext, ToolError


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


_ID = {"type": ["integer", "string"], "description": "Task id"}


def _tid(args: dict, ctx: ToolContext, key: str = "task_id") -> int:
    v = args.get(key)
    if v is None:
        v = ctx.task_id
    if v is None:
        raise ToolError(f"{key} is required")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ToolError(f"invalid {key}: {v!r}")


def _need_ctx(ctx: ToolContext) -> int:
    if ctx.task_id is None:
        raise ToolError("this tool can only be used inside a running task")
    return ctx.task_id


async def _list_tasks(ctx: ToolContext, a: dict) -> Any:
    return await svc.list_tasks(ctx.pool, status=a.get("status"))


async def _get_task(ctx: ToolContext, a: dict) -> Any:
    return await svc.get_task(ctx.pool, _tid(a, ctx))


async def _spawn_task(ctx: ToolContext, a: dict) -> Any:
    return await svc.create_task(
        ctx.pool, a.get("name", ""), a.get("description", ""), a["goal"],
        int(a.get("timeout_seconds", 0)), True,
    )


async def _pause_task(ctx: ToolContext, a: dict) -> Any:
    return await svc.pause_task(ctx.pool, _tid(a, ctx), "paused_by_agent")


async def _resume_task(ctx: ToolContext, a: dict) -> Any:
    return await svc.resume_task(ctx.pool, _tid(a, ctx))


async def _add_hint(ctx: ToolContext, a: dict) -> Any:
    return await svc.add_hint(ctx.pool, _tid(a, ctx), a["text"])


async def _set_goals(ctx: ToolContext, a: dict) -> Any:
    return await svc.add_goals(ctx.pool, _need_ctx(ctx), a["goals"])


async def _prove_goal(ctx: ToolContext, a: dict) -> Any:
    return await svc.set_goal_met(ctx.pool, _need_ctx(ctx), int(a["goal_id"]), a["evidence"],
                                  a.get("report") or None, ctx.run_no)


async def _list_goals(ctx: ToolContext, a: dict) -> Any:
    return await svc.list_goals(ctx.pool, _tid(a, ctx))


async def _goal_met(ctx: ToolContext, a: dict) -> Any:
    tid = _need_ctx(ctx)
    won = await svc.finish_task(ctx.pool, tid, "done", a.get("reason", "goal_met"),
                                a.get("report") or None, ctx.run_no)
    return {"finished": bool(won)}


def _t(name, desc, schema, run, ro=False) -> Tool:
    return Tool(name, desc, schema, run, ro)


BUILTIN_TOOLS: dict[str, Tool] = {t.name: t for t in [
    _t("list_tasks", "List tasks, optionally filtered by status.",
       _obj({"status": {"type": "string", "enum": ["created", "running", "paused", "done", "failed", "timeout"]}}),
       _list_tasks, True),
    _t("get_task", "Get one task with goal progress and run time.",
       _obj({"task_id": _ID}, ["task_id"]), _get_task, True),
    _t("spawn_task", "Create and start a new sub-task with its own goal.",
       _obj({"goal": {"type": "string", "minLength": 1}, "name": {"type": "string"},
             "description": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 0}},
            ["goal"]), _spawn_task),
    _t("pause_task", "Pause a running task.", _obj({"task_id": _ID}, ["task_id"]), _pause_task),
    _t("resume_task", "Resume a paused task.", _obj({"task_id": _ID}, ["task_id"]), _resume_task),
    _t("add_task_hint", "Add a hint to a task (defaults to the current task).",
       _obj({"task_id": _ID, "text": {"type": "string", "minLength": 1}}, ["text"]), _add_hint),
    _t("set_goals", "Add sub-goals to the current task; the task is done when all are proven.",
       _obj({"goals": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}}, ["goals"]),
       _set_goals),
    _t("prove_goal", "Mark a goal of the current task as met, with evidence. Optionally attach a Markdown `report` "
       "for this goal: what was done, the requests/commands used, the observed result, and the conclusion.",
       _obj({"goal_id": _ID, "evidence": {"type": "string", "minLength": 1},
             "report": {"type": "string", "description": "Markdown report: what was done, requests/commands, "
                        "observed result, conclusion"}}, ["goal_id", "evidence"]),
       _prove_goal),
    _t("list_goals", "List goals of a task (defaults to the current task).",
       _obj({"task_id": _ID}), _list_goals, True),
    _t("goal_met", "Declare the current task's overall goal achieved and finish it. Optionally attach a Markdown "
       "`report` summarising the whole task (approach, key results, conclusion).",
       _obj({"reason": {"type": "string"},
             "report": {"type": "string", "description": "Markdown summary report of the whole task"}},
            ["reason"]), _goal_met),
]}


# ARTEX-style SDK + domain tools live in app/tools/sdk/*.py; each module exports TOOLS: list[Tool].
import importlib  # noqa: E402

for _m in ("fs", "bash", "background", "shell", "web", "domain"):
    try:
        _mod = importlib.import_module(f"app.tools.sdk.{_m}")
    except ModuleNotFoundError as _e:  # module not written yet
        if _e.name != f"app.tools.sdk.{_m}":
            raise
        continue
    for _t_ in _mod.TOOLS:
        BUILTIN_TOOLS.setdefault(_t_.name, _t_)

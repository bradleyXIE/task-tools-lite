"""Agent loop: drive one task with an LLM until it is no longer running."""
import json
import os
from datetime import datetime, timezone

from app.runner import llm

TERMINAL = {"done", "failed", "timeout"}

RULES = """You are an autonomous task agent. Work toward the task goal using the provided tools.
Rules:
- Use set_goals to break the goal into checkable sub-goals if none exist.
- Call prove_goal(goal_id, evidence, report) when a sub-goal is verifiably satisfied, with concrete evidence.
  `report` is a Markdown write-up of that goal: what you did, the requests/commands used, the observed result and
  your conclusion.
- Call goal_met(reason, report) when the whole task is complete; `report` is a Markdown summary of the whole task
  (approach, key results, conclusion).
- Take small steps, one or a few tool calls at a time; do not repeat failed calls unchanged.
- Hints from the user may appear between steps; follow them.
- You have shell and network access through the Bash tool (curl, nmap, dig, python...) plus WebFetch, file tools
  (Read/Write/Edit/Glob/Grep) and, when needed, shell_open for interactive programs. Prefer these over claiming you are blocked.
- Record confirmed observations with record_fact, vulnerabilities with report_finding, discovered targets with insert_assets."""


async def _system(svc, pool, task: dict, hints: list[dict]) -> str:
    goals = await svc.list_goals(pool, int(task["id"]))
    from app.tools.base import ToolContext
    wd = ToolContext(pool, int(task["id"])).workdir()
    parts = [RULES, f"\nWorking directory: {wd}", f"\nTask #{task['id']}: {task['name']}\nDescription: {task['description']}\nGoal: {task['goal']}"]
    if goals:
        parts.append("Sub-goals:\n" + "\n".join(
            f"- [{'x' if g['met'] else ' '}] (id {g['id']}) {g['text']}" for g in goals))
    if hints:
        parts.append("Hints:\n" + "\n".join(f"- {h['text']}" for h in hints))
    return "\n".join(parts)


def _expired(task: dict) -> bool:
    d = task.get("deadline_at")
    return bool(d) and datetime.fromisoformat(d) <= datetime.now(timezone.utc)


async def run_task(pool, task_id: int) -> None:
    from app.tasks import service as svc
    from app.tools import registry

    task_id = int(task_id)
    run_no = await svc.next_run_no(pool, task_id)
    step = 0
    max_steps = int(os.environ.get("MAX_STEPS", "30"))
    messages: list[dict] = [{"role": "user", "content": "Begin working on the task."}]
    # The system prompt must stay byte-identical for the whole conversation: Anthropic binds
    # thinking-block signatures to it, so changing it between steps yields HTTP 400 "Invalid
    # signature in thinking block". Live state (new hints, goal changes) flows via messages/tools.
    task0 = await svc.get_task(pool, task_id)
    hints0 = await svc.list_hints(pool, task_id)
    system = await _system(svc, pool, task0, hints0)
    last_hint = max((int(h["id"]) for h in hints0), default=0)
    try:
        for _ in range(max_steps):
            task = await svc.get_task(pool, task_id)
            if task["status"] != "running":  # paused / terminal / created
                return
            if _expired(task):
                await svc.finish_task(pool, task_id, "timeout", "deadline", run_no=run_no)
                return
            new = [h for h in await svc.list_hints(pool, task_id) if int(h["id"]) > last_hint]
            if new:
                last_hint = max(int(h["id"]) for h in new)
                messages.append({"role": "user", "content": "New hints:\n" + "\n".join(h["text"] for h in new)})
            tools = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                     for t in await registry.list_tool_defs(pool, enabled_only=True)]
            resp = await llm.call(system, messages, tools)
            step += 1
            content = resp.get("content", [])
            messages.append({"role": "assistant", "content": content})
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            u = resp.get("usage") or {}
            usage = {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0)}
            await svc.add_event(pool, task_id, "llm", output={"text": text, "usage": usage}, run_no=run_no, step=step)
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                messages.append({"role": "user", "content": "Continue: call tools, or goal_met if the task is complete."})
                continue
            results = []
            for u in uses:
                res = await registry.call_tool(pool, u["name"], u.get("input") or {}, task_id, run_no, step)
                results.append({"type": "tool_result", "tool_use_id": u["id"],
                                "content": json.dumps(res, ensure_ascii=False, default=str),
                                "is_error": not res.get("ok", False)})
            messages.append({"role": "user", "content": results})
        task = await svc.get_task(pool, task_id)
        if task["status"] == "running":
            await svc.finish_task(pool, task_id, "failed", "max_steps", run_no=run_no)
    except Exception as e:  # noqa: BLE001 - includes LLMError
        try:
            await svc.add_event(pool, task_id, "error", output={"error": str(e)}, run_no=run_no, step=step or None)
            await svc.finish_task(pool, task_id, "failed", f"error: {e}"[:500], run_no=run_no)
        except Exception:
            pass

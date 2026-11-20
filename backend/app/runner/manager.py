"""Owns one asyncio task per running task; wires into app.tasks.hooks."""
import asyncio
import inspect
import os

_running: dict[int, asyncio.Task] = {}


def enabled() -> bool:
    return bool(os.environ.get("LLM_API_KEY"))


async def _pool():
    from app.db.pool import get_pool
    p = get_pool()
    return await p if inspect.isawaitable(p) else p


async def _run(task_id: int) -> None:
    from app.runner import loop
    await loop.run_task(await _pool(), task_id)


async def on_task_running(task_id) -> None:
    tid = int(task_id)
    if not enabled():
        return
    t = _running.get(tid)
    if t and not t.done():
        return
    t = asyncio.create_task(_run(tid), name=f"runner-{tid}")
    _running[tid] = t
    t.add_done_callback(lambda d, tid=tid: _running.pop(tid, None) if _running.get(tid) is d else None)


async def on_task_stopped(task_id) -> None:
    tid = int(task_id)
    t = _running.get(tid)
    # never cancel ourselves: goal_met -> finish_task -> hook runs inside the loop task
    if t and not t.done() and t is not asyncio.current_task():
        t.cancel()
        # drop it now so an immediate resume starts a fresh runner instead of
        # seeing a cancelling-but-not-yet-done task and skipping
        _running.pop(tid, None)


async def resume_running() -> None:
    """Restart loops for tasks left status='running' (not paused)."""
    if not enabled():
        return
    from app.tasks import service as svc
    for t in await svc.list_tasks(await _pool(), status="running"):
        if not t.get("paused"):
            await on_task_running(t["id"])


def register() -> None:
    from app.tasks import hooks
    if on_task_running not in hooks.running_hooks:
        hooks.running_hooks.append(on_task_running)
    if on_task_stopped not in hooks.stopped_hooks:
        hooks.stopped_hooks.append(on_task_stopped)
    try:
        asyncio.get_running_loop().create_task(resume_running())
    except RuntimeError:
        pass


async def shutdown() -> None:
    ts = list(_running.values())
    for t in ts:
        t.cancel()
    await asyncio.gather(*ts, return_exceptions=True)
    _running.clear()

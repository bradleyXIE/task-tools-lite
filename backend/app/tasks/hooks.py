"""Lifecycle hooks so the service never imports the runner."""
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)
Hook = Callable[[int], Awaitable[None]]

running_hooks: list[Hook] = []
stopped_hooks: list[Hook] = []


async def _fire(hooks: list[Hook], task_id: int) -> None:
    for h in list(hooks):
        try:
            await h(task_id)
        except Exception:  # a broken hook must not break the API
            log.exception("task hook failed for %s", task_id)


async def fire_running(task_id: int) -> None:
    await _fire(running_hooks, task_id)


async def fire_stopped(task_id: int) -> None:
    await _fire(stopped_hooks, task_id)

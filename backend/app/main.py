import importlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db.pool import close_db, init_db
from app.tasks import router as tasks_router
from app.tasks.service import TaskError

log = logging.getLogger("app")


def _optional(module: str):
    """Import a module owned by another agent; None if it does not exist yet."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name and module.startswith(e.name) or e.name == module:
            log.warning("optional module %s missing", module)
            return None
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    manager = _optional("app.runner.manager")
    if manager:
        manager.register()
    try:
        yield
    finally:
        if manager and hasattr(manager, "shutdown"):
            await manager.shutdown()
        try:  # kill background tasks / PTY sessions of the SDK tools
            from app.tools.sdk import background as _bg
            await _bg.shutdown()
        except Exception:
            log.exception("sdk background shutdown failed")
        await close_db()


app = FastAPI(title="task-tools-lite", lifespan=lifespan)
app.include_router(tasks_router.router)
_tools = _optional("app.tools.router")
if _tools:
    app.include_router(_tools.router)


@app.exception_handler(TaskError)
async def task_error(_: Request, exc: TaskError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.get("/api/health")
async def health() -> dict:
    manager = _optional("app.runner.manager")
    return {"ok": True, "runner": bool(manager and manager.enabled())}

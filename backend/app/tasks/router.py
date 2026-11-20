from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.db.pool import get_pool
from app.tasks import service as svc
from app.tasks import views

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskIn(BaseModel):
    name: str = ""
    description: str = ""
    goal: str
    timeout_seconds: int = Field(0, ge=0)
    start: bool = True
    goals: list[str] = []


class TaskPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class Control(BaseModel):
    action: Literal["pause", "resume"]


class GoalsIn(BaseModel):
    text: str | None = None
    texts: list[str] = []


class GoalPatch(BaseModel):
    met: bool
    evidence: str = ""
    report: str | None = None


class HintIn(BaseModel):
    text: str


@router.get("")
async def list_tasks(status: str | None = None) -> dict:
    return {"tasks": await svc.list_tasks(await get_pool(), status or None)}


@router.post("", status_code=201)
async def create_task(body: TaskIn) -> dict:
    return await svc.create_task(await get_pool(), body.name, body.description, body.goal,
                                 body.timeout_seconds, body.start, body.goals)


@router.get("/{task_id}")
async def get_task(task_id: int) -> dict:
    return await svc.get_task(await get_pool(), task_id)


@router.patch("/{task_id}")
async def patch_task(task_id: int, body: TaskPatch) -> dict:
    return await svc.update_task(await get_pool(), task_id, body.name, body.description)


@router.delete("/{task_id}")
async def delete_task(task_id: int) -> dict:
    await svc.delete_task(await get_pool(), task_id)
    return {"ok": True}


@router.post("/{task_id}/control")
async def control(task_id: int, body: Control) -> dict:
    pool = await get_pool()
    if body.action == "pause":
        return await svc.pause_task(pool, task_id)
    return await svc.resume_task(pool, task_id)


@router.get("/{task_id}/goals")
async def list_goals(task_id: int) -> dict:
    return {"goals": await svc.list_goals(await get_pool(), task_id)}


@router.post("/{task_id}/goals")
async def add_goals(task_id: int, body: GoalsIn) -> dict:
    texts = body.texts + ([body.text] if body.text else [])
    return {"goals": await svc.add_goals(await get_pool(), task_id, texts)}


@router.patch("/{task_id}/goals/{goal_id}")
async def patch_goal(task_id: int, goal_id: int, body: GoalPatch) -> dict:
    if not body.met:
        raise HTTPException(400, "only met=true is supported")
    return await svc.set_goal_met(await get_pool(), task_id, goal_id, body.evidence, body.report)


@router.get("/{task_id}/hints")
async def list_hints(task_id: int) -> dict:
    return {"hints": await svc.list_hints(await get_pool(), task_id)}


@router.post("/{task_id}/hints")
async def add_hint(task_id: int, body: HintIn) -> dict:
    return await svc.add_hint(await get_pool(), task_id, body.text)


@router.get("/{task_id}/events")
async def list_events(task_id: int, after_id: int = 0) -> dict:
    return {"events": await svc.list_events(await get_pool(), task_id, after_id)}


@router.get("/{task_id}/sessions")
async def sessions(task_id: int) -> dict:
    return await views.sessions(await get_pool(), task_id)


@router.get("/{task_id}/findings")
async def findings(task_id: int) -> dict:
    return {"findings": await views.findings(await get_pool(), task_id)}


@router.get("/{task_id}/facts")
async def facts(task_id: int) -> dict:
    return {"facts": await views.facts(await get_pool(), task_id)}


@router.get("/{task_id}/assets")
async def assets(task_id: int) -> dict:
    return {"assets": await views.assets(await get_pool(), task_id)}


@router.get("/{task_id}/graph")
async def graph(task_id: int) -> dict:
    return await views.graph(await get_pool(), task_id)


@router.get("/{task_id}/report")
async def report(task_id: int, download: int = 0):
    r = await views.report(await get_pool(), task_id)
    if download:
        return Response(r["markdown"], media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="task-{task_id}-report.md"'})
    return r

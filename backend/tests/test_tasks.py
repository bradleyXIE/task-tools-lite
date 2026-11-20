import asyncio

import pytest

from app.tasks import hooks
from app.tasks import service as svc


@pytest.fixture
def fired():
    calls: list[tuple[str, int]] = []

    async def on_run(tid: int) -> None:
        calls.append(("running", tid))

    async def on_stop(tid: int) -> None:
        calls.append(("stopped", tid))

    hooks.running_hooks.append(on_run)
    hooks.stopped_hooks.append(on_stop)
    yield calls
    hooks.running_hooks.remove(on_run)
    hooks.stopped_hooks.remove(on_stop)


async def status_of(pool, tid):
    return (await svc.get_task(pool, tid))["status"]


async def test_lifecycle(pool, fired):
    t = await svc.create_task(pool, "n", "d", "do it", timeout_seconds=60)
    tid = int(t["id"])
    assert t["status"] == "running" and t["deadline_at"] and isinstance(t["id"], str)
    assert ("running", tid) in fired

    p = await svc.pause_task(pool, tid)
    assert p["status"] == "paused" and p["paused"] and p["pause_reason"] == "paused_by_user"
    with pytest.raises(svc.TaskError) as e:
        await svc.pause_task(pool, tid)
    assert e.value.status_code == 409

    r = await svc.resume_task(pool, tid)
    assert r["status"] == "running" and not r["paused"] and r["pause_reason"] == ""

    assert await svc.finish_task(pool, tid, "done", "ok") is True
    assert await svc.finish_task(pool, tid, "failed", "late") is False  # first writer wins
    t = await svc.get_task(pool, tid)
    assert t["status"] == "done" and t["stop_reason"] == "ok" and t["completed_at"]
    for fn in (svc.resume_task, svc.pause_task):
        with pytest.raises(svc.TaskError) as e:
            await fn(pool, tid)
        assert e.value.status_code == 409
    evs = [x["output"]["status"] for x in await svc.list_events(pool, tid) if x["kind"] == "status"]
    assert evs == ["created", "running", "paused", "running", "done"]
    assert ("stopped", tid) in fired


async def test_created_not_started_and_missing(pool):
    t = await svc.create_task(pool, "", "", "g", start=False)
    assert t["status"] == "created"
    with pytest.raises(svc.TaskError) as e:
        await svc.pause_task(pool, int(t["id"]))
    assert e.value.status_code == 409
    with pytest.raises(svc.TaskError) as e:
        await svc.get_task(pool, 987654321)
    assert e.value.status_code == 404
    with pytest.raises(svc.TaskError) as e:
        await svc.create_task(pool, "", "", "  ")
    assert e.value.status_code == 400


async def test_goals_auto_complete(pool):
    t = await svc.create_task(pool, "", "", "g")
    tid = int(t["id"])
    goals = await svc.add_goals(pool, tid, ["a", "b"])
    assert (await svc.get_task(pool, tid))["goals_total"] == 2
    await svc.set_goal_met(pool, tid, int(goals[0]["id"]), "e1")
    t = await svc.get_task(pool, tid)
    assert t["status"] == "running" and t["goals_met"] == 1
    g = await svc.set_goal_met(pool, tid, int(goals[1]["id"]), "e2")
    assert g["met"] and g["evidence"] == "e2" and g["met_at"]
    t = await svc.get_task(pool, tid)
    assert t["status"] == "done" and t["stop_reason"] == "all_goals_met"
    with pytest.raises(svc.TaskError) as e:
        await svc.set_goal_met(pool, tid, 99999, "x")
    assert e.value.status_code == 404


async def test_soft_delete(pool):
    t = await svc.create_task(pool, "", "", "g")
    tid = int(t["id"])
    with pytest.raises(svc.TaskError) as e:
        await svc.delete_task(pool, tid)
    assert e.value.status_code == 409
    await svc.pause_task(pool, tid)
    await svc.delete_task(pool, tid)
    with pytest.raises(svc.TaskError) as e:
        await svc.get_task(pool, tid)
    assert e.value.status_code == 404
    assert tid not in [int(x["id"]) for x in await svc.list_tasks(pool)]
    assert await pool.fetchval("SELECT count(*) FROM tasks WHERE id=$1", tid) == 1


async def test_run_seconds_accounting(pool):
    t = await svc.create_task(pool, "", "", "g")
    tid = int(t["id"])
    # backdate the open segment by 100s
    await pool.execute("UPDATE tasks SET run_segment_started_at = now() - interval '100 seconds' WHERE id=$1", tid)
    assert 100 <= (await svc.get_task(pool, tid))["run_seconds"] <= 103
    await svc.pause_task(pool, tid)
    frozen = (await svc.get_task(pool, tid))["run_seconds"]
    assert 100 <= frozen <= 103
    await asyncio.sleep(1.1)
    assert (await svc.get_task(pool, tid))["run_seconds"] == frozen  # paused: no growth
    await svc.resume_task(pool, tid)
    await pool.execute("UPDATE tasks SET run_segment_started_at = now() - interval '50 seconds' WHERE id=$1", tid)
    assert frozen + 50 <= (await svc.get_task(pool, tid))["run_seconds"] <= frozen + 53
    await svc.finish_task(pool, tid, "failed", "x")
    done = (await svc.get_task(pool, tid))["run_seconds"]
    assert frozen + 50 <= done <= frozen + 54
    await asyncio.sleep(1.1)
    assert (await svc.get_task(pool, tid))["run_seconds"] == done


async def test_hints_events_filter(pool):
    t = await svc.create_task(pool, "", "", "g")
    tid = int(t["id"])
    await svc.add_hint(pool, tid, "try x")
    assert [h["text"] for h in await svc.list_hints(pool, tid)] == ["try x"]
    ev = await svc.add_event(pool, tid, "tool_call", "echo", {"a": 1}, {"ok": True})
    assert ev["input"] == {"a": 1} and ev["output"] == {"ok": True}
    assert [e["id"] for e in await svc.list_events(pool, tid, after_id=int(ev["id"]))] == []
    await svc.pause_task(pool, tid)
    assert tid in [int(x["id"]) for x in await svc.list_tasks(pool, "paused")]
    assert tid not in [int(x["id"]) for x in await svc.list_tasks(pool, "running")]

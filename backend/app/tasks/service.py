"""Task lifecycle on raw asyncpg. All functions take the pool first."""
import json
from datetime import UTC, datetime
from typing import Any

from app.tasks import hooks

TERMINAL = ("done", "failed", "timeout")
# seconds of the open run segment, folded in SQL so clocks never disagree
_FOLD = "active_run_seconds + CASE WHEN run_segment_started_at IS NULL THEN 0 ELSE floor(extract(epoch FROM now() - run_segment_started_at))::bigint END"
_COLS = """t.*, (SELECT count(*) FROM task_goals g WHERE g.task_id = t.id) AS goals_total,
  (SELECT count(*) FROM task_goals g WHERE g.task_id = t.id AND g.met) AS goals_met"""


class TaskError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def run_seconds(row: Any) -> int:
    total = int(row["active_run_seconds"])
    start = row["run_segment_started_at"]
    if start and row["status"] == "running" and not row["paused"]:
        total += max(0, int((datetime.now(UTC) - start).total_seconds()))
    return total


def _task(row: Any) -> dict:
    return {
        "id": str(row["id"]), "name": row["name"], "description": row["description"], "goal": row["goal"],
        "status": "paused" if row["paused"] else row["status"], "paused": row["paused"],
        "pause_reason": row["pause_reason"], "stop_reason": row["stop_reason"],
        "timeout_seconds": row["timeout_seconds"], "run_seconds": run_seconds(row),
        "deadline_at": _iso(row["deadline_at"]), "completed_at": _iso(row["completed_at"]),
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        "goals_total": row["goals_total"], "goals_met": row["goals_met"],
        "summary_report": row["summary_report"],
    }


def _goal(r: Any) -> dict:
    return {"id": str(r["id"]), "task_id": str(r["task_id"]), "text": r["text"], "met": r["met"],
            "evidence": r["evidence"], "report": r["report"], "met_at": _iso(r["met_at"]), "created_at": _iso(r["created_at"])}


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _event(r: Any) -> dict:
    return {"id": str(r["id"]), "task_id": str(r["task_id"]) if r["task_id"] is not None else None,
            "kind": r["kind"], "tool_name": r["tool_name"], "input": _j(r["input"]),
            "output": _j(r["output"]), "run_no": r["run_no"], "step": r["step"],
            "created_at": _iso(r["created_at"])}


async def _status_event(conn: Any, task_id: int, text: str, run_no: int | None = None) -> None:
    await conn.execute(
        "INSERT INTO task_events (task_id, kind, output, run_no) VALUES ($1, 'status', $2::jsonb, $3)",
        int(task_id), json.dumps({"status": text}), run_no,
    )


async def _fetch(conn: Any, task_id: int, lock: bool = False) -> Any:
    sql = f"SELECT {_COLS} FROM tasks t WHERE t.id = $1 AND t.deleted_at IS NULL"
    if lock:
        sql = "SELECT * FROM tasks WHERE id = $1 AND deleted_at IS NULL FOR UPDATE"
    row = await conn.fetchrow(sql, int(task_id))
    if row is None:
        raise TaskError(404, "task not found")
    return row


async def get_task(pool: Any, task_id: int) -> dict:
    async with pool.acquire() as conn:
        return _task(await _fetch(conn, task_id))


async def create_task(pool: Any, name: str, description: str, goal: str,
                      timeout_seconds: int = 0, start: bool = True, goals: list[str] | None = None) -> dict:
    if not goal or not goal.strip():
        raise TaskError(400, "goal is required")
    if timeout_seconds < 0:
        raise TaskError(400, "timeout_seconds must be >= 0")
    async with pool.acquire() as conn, conn.transaction():
        tid = await conn.fetchval(
            "INSERT INTO tasks (name, description, goal, timeout_seconds) VALUES ($1,$2,$3,$4) RETURNING id",
            name or "", description or "", goal, timeout_seconds)
        await _status_event(conn, tid, "created")
        for text in goals or []:
            if text.strip():
                await conn.execute("INSERT INTO task_goals (task_id, text) VALUES ($1,$2)", tid, text.strip())
        if start:
            await conn.execute(
                """UPDATE tasks SET status='running', run_segment_started_at=now(),
                   deadline_at = CASE WHEN timeout_seconds > 0 THEN now() + make_interval(secs => timeout_seconds) END
                   WHERE id=$1""", tid)
            await _status_event(conn, tid, "running")
        task = _task(await _fetch(conn, tid))
    if start:
        await hooks.fire_running(tid)
    return task


async def list_tasks(pool: Any, status: str | None = None, limit: int = 200) -> list[dict]:
    where = "t.deleted_at IS NULL"
    args: list[Any] = []
    if status == "paused":
        where += " AND t.paused"
    elif status:
        where += " AND NOT t.paused AND t.status = $1"
        args.append(status)
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {_COLS} FROM tasks t WHERE {where} ORDER BY t.id DESC LIMIT {int(limit)}", *args)
    return [_task(r) for r in rows]


async def update_task(pool: Any, task_id: int, name: str | None = None, description: str | None = None) -> dict:
    async with pool.acquire() as conn:
        await _fetch(conn, task_id)
        await conn.execute("UPDATE tasks SET name=COALESCE($2,name), description=COALESCE($3,description) WHERE id=$1",
                           int(task_id), name, description)
        return _task(await _fetch(conn, task_id))


async def delete_task(pool: Any, task_id: int) -> None:
    async with pool.acquire() as conn, conn.transaction():
        row = await _fetch(conn, task_id, lock=True)
        if row["status"] == "running" and not row["paused"]:
            raise TaskError(409, "task is running; pause it first")
        await conn.execute("UPDATE tasks SET deleted_at=now() WHERE id=$1", int(task_id))
    await hooks.fire_stopped(int(task_id))


async def pause_task(pool: Any, task_id: int, reason: str = "paused_by_user") -> dict:
    async with pool.acquire() as conn, conn.transaction():
        row = await _fetch(conn, task_id, lock=True)
        if row["status"] in TERMINAL:
            raise TaskError(409, f"task already {row['status']}")
        if row["paused"]:
            raise TaskError(409, "task already paused")
        if row["status"] != "running":
            raise TaskError(409, "task is not running")
        await conn.execute(
            f"""UPDATE tasks SET active_run_seconds={_FOLD}, run_segment_started_at=NULL,
                paused=true, status='paused', pause_reason=$2 WHERE id=$1""", int(task_id), reason)
        await _status_event(conn, task_id, "paused")
        task = _task(await _fetch(conn, task_id))
    await hooks.fire_stopped(int(task_id))
    return task


async def resume_task(pool: Any, task_id: int) -> dict:
    async with pool.acquire() as conn, conn.transaction():
        row = await _fetch(conn, task_id, lock=True)
        if row["status"] in TERMINAL:
            raise TaskError(409, f"task already {row['status']}")
        if not row["paused"]:
            raise TaskError(409, "task is not paused")
        await conn.execute(
            "UPDATE tasks SET status='running', paused=false, pause_reason='', run_segment_started_at=now() WHERE id=$1",
            int(task_id))
        await _status_event(conn, task_id, "running")
        task = _task(await _fetch(conn, task_id))
    await hooks.fire_running(int(task_id))
    return task


async def finish_task(pool: Any, task_id: int, status: str, stop_reason: str = "",
                      summary_report: str | None = None, run_no: int | None = None) -> bool:
    if status not in TERMINAL:
        raise TaskError(400, f"invalid final status {status}")
    async with pool.acquire() as conn, conn.transaction():
        won = await conn.fetchval(
            f"""UPDATE tasks SET active_run_seconds={_FOLD}, run_segment_started_at=NULL, status=$2,
                paused=false, stop_reason=$3, completed_at=now(),
                summary_report=COALESCE($4, summary_report)
                WHERE id=$1 AND deleted_at IS NULL AND status NOT IN ('done','failed','timeout') RETURNING id""",
            int(task_id), status, stop_reason, summary_report)
        if won is None:
            return False
        await _status_event(conn, task_id, status, run_no)
    await hooks.fire_stopped(int(task_id))
    return True


async def add_goals(pool: Any, task_id: int, texts: list[str]) -> list[dict]:
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts:
        raise TaskError(400, "no goal text given")
    async with pool.acquire() as conn, conn.transaction():
        await _fetch(conn, task_id)
        rows = [await conn.fetchrow("INSERT INTO task_goals (task_id, text) VALUES ($1,$2) RETURNING *", int(task_id), t)
                for t in texts]
    return [_goal(r) for r in rows]


async def list_goals(pool: Any, task_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        await _fetch(conn, task_id)
        rows = await conn.fetch("SELECT * FROM task_goals WHERE task_id=$1 ORDER BY id", int(task_id))
    return [_goal(r) for r in rows]


async def set_goal_met(pool: Any, task_id: int, goal_id: int, evidence: str = "",
                       report: str | None = None, run_no: int | None = None) -> dict:
    async with pool.acquire() as conn:
        await _fetch(conn, task_id)
        row = await conn.fetchrow(
            "UPDATE task_goals SET met=true, evidence=$3, report=COALESCE($4, report), "
            "met_at=COALESCE(met_at, now()) WHERE id=$1 AND task_id=$2 RETURNING *",
            int(goal_id), int(task_id), evidence or "", report)
        if row is None:
            raise TaskError(404, "goal not found")
        pending = await conn.fetchval("SELECT count(*) FROM task_goals WHERE task_id=$1 AND NOT met", int(task_id))
    if pending == 0:
        await finish_task(pool, task_id, "done", "all_goals_met", run_no=run_no)
    return _goal(row)


async def add_hint(pool: Any, task_id: int, text: str) -> dict:
    if not text or not text.strip():
        raise TaskError(400, "hint text is required")
    async with pool.acquire() as conn:
        await _fetch(conn, task_id)
        r = await conn.fetchrow("INSERT INTO task_hints (task_id, text) VALUES ($1,$2) RETURNING *", int(task_id), text)
    return {"id": str(r["id"]), "task_id": str(r["task_id"]), "text": r["text"], "created_at": _iso(r["created_at"])}


async def list_hints(pool: Any, task_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        await _fetch(conn, task_id)
        rows = await conn.fetch("SELECT * FROM task_hints WHERE task_id=$1 ORDER BY id", int(task_id))
    return [{"id": str(r["id"]), "task_id": str(r["task_id"]), "text": r["text"], "created_at": _iso(r["created_at"])}
            for r in rows]


async def add_event(pool: Any, task_id: int | None, kind: str, tool_name: str = "",
                    input: Any = None, output: Any = None,
                    run_no: int | None = None, step: int | None = None) -> dict:
    enc = lambda v: None if v is None else json.dumps(v, default=str)
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "INSERT INTO task_events (task_id, kind, tool_name, input, output, run_no, step) "
            "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7) RETURNING *",
            None if task_id is None else int(task_id), kind, tool_name, enc(input), enc(output), run_no, step)
    return _event(r)


async def list_events(pool: Any, task_id: int, after_id: int = 0, limit: int = 500) -> list[dict]:
    async with pool.acquire() as conn:
        await _fetch(conn, task_id)
        rows = await conn.fetch(
            "SELECT * FROM task_events WHERE task_id=$1 AND id>$2 ORDER BY id LIMIT $3", int(task_id), int(after_id), int(limit))
    return [_event(r) for r in rows]


async def next_run_no(pool: Any, task_id: int) -> int:
    return int(await pool.fetchval("SELECT COALESCE(max(run_no), 0) + 1 FROM task_events WHERE task_id=$1",
                                   int(task_id)))

"""Tool registry: merges builtin defaults with DB rows, validates, executes, logs."""
from __future__ import annotations

import json
from typing import Any

import jsonschema

from app.tasks import service as svc

from .base import ToolContext, ToolError, env_int
from .builtin import BUILTIN_TOOLS
from .custom import run_http, run_script


def _j(v: Any, default: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return default if v is None else v


def _dto(row: Any) -> dict:
    d = {
        "id": str(row["id"]), "name": row["name"], "kind": row["kind"],
        "description": row["description"], "input_schema": _j(row["input_schema"], {}),
        "config": _j(row["config"], {}), "read_only": row["read_only"],
        "enabled": row["enabled"],
    }
    b = BUILTIN_TOOLS.get(row["name"]) if row["kind"] == "builtin" else None
    if b:
        d["builtin_default_description"] = b.description
        if not d["description"]:
            d["description"] = b.description
        if not d["input_schema"]:
            d["input_schema"] = b.input_schema
        d["read_only"] = b.read_only
    return d


def _builtin_default(t: Any) -> dict:
    return {"id": None, "name": t.name, "kind": "builtin", "description": t.description,
            "input_schema": t.input_schema, "config": {}, "read_only": t.read_only,
            "enabled": True, "builtin_default_description": t.description}


async def _all(pool: Any) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM tools ORDER BY id")
    defs = [_dto(r) for r in rows]
    have = {d["name"] for d in defs}
    defs += [_builtin_default(t) for n, t in BUILTIN_TOOLS.items() if n not in have]
    return defs


async def list_tool_defs(pool: Any, enabled_only: bool = False) -> list[dict]:
    defs = await _all(pool)
    return [d for d in defs if d["enabled"]] if enabled_only else defs


async def get_tool_def(pool: Any, name: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM tools WHERE name=$1", name)
    if row:
        return _dto(row)
    t = BUILTIN_TOOLS.get(name)
    return _builtin_default(t) if t else None


_OLD_DESCRIPTIONS = {
    "prove_goal": ["Mark a goal of the current task as met, with evidence."],
    "goal_met": ["Declare the current task's overall goal achieved and finish it."],
}


async def seed_builtin_tools(pool: Any) -> None:
    for t in BUILTIN_TOOLS.values():
        await pool.execute(
            "INSERT INTO tools (name, kind, description, input_schema, config, read_only) "
            "VALUES ($1,'builtin',$2,$3::jsonb,'{}'::jsonb,$4) ON CONFLICT (name) DO NOTHING",
            t.name, t.description, json.dumps(t.input_schema), t.read_only)
        # keep schemas current with the code; refresh descriptions only if still an untouched old default
        await pool.execute(
            "UPDATE tools SET input_schema=$2::jsonb, "
            "description=CASE WHEN description = ANY($3::text[]) THEN $4 ELSE description END "
            "WHERE name=$1 AND kind='builtin'",
            t.name, json.dumps(t.input_schema), _OLD_DESCRIPTIONS.get(t.name, []), t.description)


def _cap(output: Any, cap: int) -> Any:
    if isinstance(output, str):
        return output[:cap]
    try:
        s = json.dumps(output, default=str)
    except (TypeError, ValueError):
        s = str(output)
    return output if len(s) <= cap else s[:cap]


async def _execute(ctx: ToolContext, d: dict, args: dict) -> Any:
    if d["kind"] == "builtin":
        return await BUILTIN_TOOLS[d["name"]].run(ctx, args)
    if d["kind"] == "http":
        return await run_http(d["config"], args)
    return await run_script(d["config"], args)


async def _call(pool: Any, name: str, args: dict, task_id: int | None,
                run_no: int | None = None, step: int | None = None) -> dict:
    d = await get_tool_def(pool, name)
    if d is None:
        return {"ok": False, "output": None, "error": f"unknown tool: {name}"}
    if not d["enabled"]:
        return {"ok": False, "output": None, "error": f"tool '{name}' is disabled"}
    try:
        jsonschema.validate(args, d["input_schema"])
    except jsonschema.ValidationError as e:
        return {"ok": False, "output": None, "error": f"invalid arguments: {e.message}"}
    except jsonschema.SchemaError as e:
        return {"ok": False, "output": None, "error": f"tool schema invalid: {e.message}"}
    try:
        out = await _execute(ToolContext(pool, task_id, run_no, step), d, args)
        return {"ok": True, "output": _cap(out, env_int("TOOL_OUTPUT_CAP", 20000))}
    except svc.TaskError as e:
        return {"ok": False, "output": None, "error": e.message}
    except ToolError as e:
        return {"ok": False, "output": None, "error": str(e)}
    except (KeyError, TypeError, ValueError) as e:
        return {"ok": False, "output": None, "error": f"{type(e).__name__}: {e}"}
    except Exception as e:  # tool bugs must not crash the runner
        return {"ok": False, "output": None, "error": f"{type(e).__name__}: {e}"}


async def call_tool(pool: Any, name: str, args: dict, task_id: int | None = None,
                    run_no: int | None = None, step: int | None = None) -> dict:
    args = args or {}
    res = await _call(pool, name, args, task_id, run_no, step)
    if task_id is not None:
        try:
            await svc.add_event(pool, int(task_id), "tool_call", name, args, res, run_no, step)
        except Exception:
            pass  # logging must never break the call
    return res

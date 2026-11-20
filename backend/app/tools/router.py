"""REST routes for tools, mounted under /api by main.py (prefix /api/tools here)."""
from __future__ import annotations

import json
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.pool import get_pool

from .registry import _dto, call_tool, list_tool_defs

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["http", "script"]
    description: str = ""
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    config: dict = Field(default_factory=dict)
    read_only: bool = False
    enabled: bool = True


class ToolPatch(BaseModel):
    description: str | None = None
    input_schema: dict | None = None
    enabled: bool | None = None
    config: dict | None = None
    read_only: bool | None = None
    name: str | None = None


class CallBody(BaseModel):
    args: dict = Field(default_factory=dict)
    task_id: str | int | None = None


def _check_schema(schema: dict) -> None:
    import jsonschema
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as e:
        raise HTTPException(400, f"invalid input_schema: {e.message}")


def _check_config(kind: str, config: dict) -> None:
    if kind == "http" and not str(config.get("url", "")).lower().startswith(("http://", "https://", "{{")):
        raise HTTPException(400, "http tool config.url must be http(s)")
    if kind == "script" and not str(config.get("code", "")).strip():
        raise HTTPException(400, "script tool config.code is required")


async def _find(ref: str) -> Any:
    pool = await get_pool()
    if ref.isdigit():
        row = await pool.fetchrow("SELECT * FROM tools WHERE id=$1", int(ref))
    else:
        row = await pool.fetchrow("SELECT * FROM tools WHERE name=$1", ref)
    if not row:
        raise HTTPException(404, "tool not found")
    return row


@router.get("")
async def list_tools() -> dict:
    return {"tools": await list_tool_defs(await get_pool())}


@router.get("/schema")
async def tools_schema() -> dict:
    defs = await list_tool_defs(await get_pool(), enabled_only=True)
    return {"tools": [{"name": d["name"], "description": d["description"],
                       "input_schema": d["input_schema"]} for d in defs]}


@router.post("", status_code=201)
async def create_tool(body: ToolCreate) -> dict:
    _check_schema(body.input_schema)
    _check_config(body.kind, body.config)
    try:
        row = await (await get_pool()).fetchrow(
            "INSERT INTO tools (name,kind,description,input_schema,config,read_only,enabled) "
            "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7) RETURNING *",
            body.name, body.kind, body.description, json.dumps(body.input_schema),
            json.dumps(body.config), body.read_only, body.enabled)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "tool name already exists")
    return _dto(row)


@router.get("/{ref}")
async def get_tool(ref: str) -> dict:
    return _dto(await _find(ref))


@router.patch("/{ref}")
async def patch_tool(ref: str, body: ToolPatch) -> dict:
    row = await _find(ref)
    data = body.model_dump(exclude_unset=True)
    if row["kind"] == "builtin":
        data = {k: v for k, v in data.items() if k in ("description", "input_schema", "enabled")}
    elif "config" in data:
        _check_config(row["kind"], data["config"])
    if data.get("input_schema") is not None:
        _check_schema(data["input_schema"])
    data = {k: v for k, v in data.items() if v is not None}
    if not data:
        return _dto(row)
    sets, vals = [], []
    for i, (k, v) in enumerate(data.items(), 1):
        cast = "::jsonb" if k in ("input_schema", "config") else ""
        sets.append(f"{k}=${i}{cast}")
        vals.append(json.dumps(v) if cast else v)
    vals.append(row["id"])
    try:
        row = await (await get_pool()).fetchrow(
            f"UPDATE tools SET {', '.join(sets)} WHERE id=${len(vals)} RETURNING *", *vals)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "tool name already exists")
    return _dto(row)


@router.delete("/{ref}")
async def delete_tool(ref: str) -> dict:
    row = await _find(ref)
    if row["kind"] == "builtin":
        raise HTTPException(409, "builtin tools cannot be deleted")
    await (await get_pool()).execute("DELETE FROM tools WHERE id=$1", row["id"])
    return {"ok": True}


@router.post("/{name}/call")
async def call(name: str, body: CallBody) -> dict:
    try:
        tid = int(body.task_id) if body.task_id not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "task_id must be an integer")
    res = await call_tool(await get_pool(), name, body.args, tid)
    if not res["ok"] and res["error"].startswith("unknown tool"):
        raise HTTPException(404, res["error"])
    return res

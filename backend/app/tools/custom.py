"""Custom tool executors: http and script."""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
from typing import Any

import httpx

from .base import ToolError, env_int

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

_WRAPPER = r"""
import sys, json
args = json.load(sys.stdin)
ns = {"args": args, "__name__": "__tool__"}
exec(compile(sys.argv[1], "<tool>", "exec"), ns)
sys.stdout.write("\n__TOOL_RESULT__" + json.dumps(ns.get("result"), default=str))
"""
_MARK = "\n__TOOL_RESULT__"


def _fmt(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v)


def substitute(obj: Any, args: dict) -> Any:
    """Replace {{arg}} in all string values (recursively)."""
    if isinstance(obj, str):
        return _PLACEHOLDER.sub(lambda m: _fmt(args.get(m.group(1), "")), obj)
    if isinstance(obj, dict):
        return {k: substitute(v, args) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, args) for v in obj]
    return obj


async def run_http(config: dict, args: dict) -> dict:
    cap = env_int("TOOL_OUTPUT_CAP", 20000)
    timeout = float(config.get("timeout") or env_int("HTTP_TOOL_TIMEOUT", 15))
    url = substitute(config.get("url", ""), args)
    if not re.match(r"^https?://", url, re.I):
        raise ToolError("only http/https URLs are allowed")
    method = str(config.get("method", "GET")).upper()
    headers = substitute(config.get("headers") or {}, args)
    body = config.get("body_template")
    kw: dict = {}
    if body is not None:
        kw["json"] = substitute(body, args)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            async with c.stream(method, url, headers=headers, **kw) as r:
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > cap * 4:  # bytes bound; text is trimmed below
                        break
                status = r.status_code
    except httpx.HTTPError as e:
        raise ToolError(f"http error: {type(e).__name__}: {e}")
    return {"status": status, "body": buf.decode("utf-8", "replace")[:cap]}


async def run_script(config: dict, args: dict) -> Any:
    code = config.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ToolError("script tool has no code")
    timeout = env_int("SCRIPT_TIMEOUT", 10)
    cap = env_int("TOOL_OUTPUT_CAP", 20000)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", _WRAPPER, code,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True,
        env={"PATH": os.environ.get("PATH", "")},
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(args).encode()), timeout)
    except BaseException as e:  # timeout OR cancellation (task paused): never leak the subprocess
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await asyncio.shield(proc.wait())
        if isinstance(e, asyncio.TimeoutError):
            raise ToolError(f"script timed out after {timeout}s")
        raise
    text = out.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise ToolError(f"script failed: {err.decode('utf-8', 'replace')[-cap:]}")
    head, sep, tail = text.rpartition(_MARK)
    if not sep:
        raise ToolError("script produced no result")
    try:
        return json.loads(tail)
    except ValueError:
        raise ToolError("script result is not valid JSON")

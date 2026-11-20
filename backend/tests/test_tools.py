import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import asyncpg
import pytest

from app.tasks import service as svc
from app.tools.registry import call_tool, list_tool_defs

_CUSTOM: list[str] = []  # custom tool names created by the current test; removed by name on teardown


@pytest.fixture
async def pool(dsn):
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    _CUSTOM.clear()
    try:
        yield p
    finally:
        if _CUSTOM:
            await p.execute("DELETE FROM tools WHERE kind<>'builtin' AND name = ANY($1::text[])", _CUSTOM)
        await p.close()


async def add_tool(pool, name, kind, config, schema=None, enabled=True):
    await pool.execute("DELETE FROM tools WHERE kind<>'builtin' AND name=$1", name)  # leftover from a crashed run
    _CUSTOM.append(name)
    await pool.execute(
        "INSERT INTO tools (name,kind,description,input_schema,config,enabled) VALUES ($1,$2,'t',$3::jsonb,$4::jsonb,$5)",
        name, kind, json.dumps(schema or {"type": "object", "properties": {}}), json.dumps(config), enabled)


async def test_schema_validation_failure(pool):
    await add_tool(pool, "echo", "script", {"code": "result = args"},
                   {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]})
    r = await call_tool(pool, "echo", {"n": "x"})
    assert not r["ok"] and "invalid arguments" in r["error"]
    r = await call_tool(pool, "echo", {})
    assert not r["ok"]


async def test_disabled_tool_refused(pool):
    await add_tool(pool, "off", "script", {"code": "result = 1"}, enabled=False)
    r = await call_tool(pool, "off", {})
    assert not r["ok"] and "disabled" in r["error"]
    assert "off" not in [d["name"] for d in await list_tool_defs(pool, enabled_only=True)]


async def test_script_result(pool):
    await add_tool(pool, "add", "script", {"code": "print('noise')\nresult = {'sum': args['a'] + args['b']}"})
    r = await call_tool(pool, "add", {"a": 2, "b": 3})
    assert r == {"ok": True, "output": {"sum": 5}}


async def test_script_timeout_and_cap(pool, monkeypatch):
    monkeypatch.setenv("SCRIPT_TIMEOUT", "1")
    monkeypatch.setenv("TOOL_OUTPUT_CAP", "100")
    await add_tool(pool, "slow", "script", {"code": "import time\ntime.sleep(30)\nresult = 1"})
    await add_tool(pool, "big", "script", {"code": "result = 'x' * 5000"})
    r = await call_tool(pool, "slow", {})
    assert not r["ok"] and "timed out" in r["error"]
    r = await call_tool(pool, "big", {})
    assert r["ok"] and len(r["output"]) == 100


class _H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        out = json.dumps({"path": self.path, "body": body, "h": self.headers.get("X-Who")}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    s = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


async def test_http_tool(pool, server):
    await add_tool(pool, "hit", "http", {
        "method": "POST", "url": server + "/p/{{id}}", "headers": {"X-Who": "{{who}}"},
        "body_template": {"msg": "hello {{who}}"}})
    r = await call_tool(pool, "hit", {"id": 7, "who": "bob"})
    assert r["ok"] and r["output"]["status"] == 200
    body = json.loads(r["output"]["body"])
    assert body["path"] == "/p/7" and body["h"] == "bob"
    assert json.loads(body["body"]) == {"msg": "hello bob"}


async def test_http_rejects_non_http(pool):
    await add_tool(pool, "f", "http", {"method": "GET", "url": "file:///etc/passwd"})
    r = await call_tool(pool, "f", {})
    assert not r["ok"] and "http" in r["error"]


async def test_builtin_task_tools(pool):
    r = await call_tool(pool, "spawn_task", {"goal": "g1", "name": "n"})
    assert r["ok"], r
    tid = int(r["output"]["id"])
    assert r["output"]["status"] == "running"
    r = await call_tool(pool, "pause_task", {"task_id": tid})
    assert r["ok"] and r["output"]["status"] == "paused"
    r = await call_tool(pool, "pause_task", {"task_id": tid})
    assert not r["ok"]  # 409 conflict surfaced as error
    r = await call_tool(pool, "list_tasks", {"status": "paused"})
    assert r["ok"] and any(str(t["id"]) == str(tid) for t in r["output"])
    r = await call_tool(pool, "goal_met", {"reason": "x"})
    assert not r["ok"]  # needs task context
    r = await call_tool(pool, "set_goals", {"goals": ["a"]}, task_id=tid)
    assert r["ok"]
    evs = await svc.list_events(pool, tid)
    assert any(e["kind"] == "tool_call" and e["tool_name"] == "set_goals" for e in evs)
    await svc.finish_task(pool, tid, "failed", "cleanup")


async def test_builtin_override_and_disable(pool):
    old = await pool.fetchrow("SELECT enabled, description FROM tools WHERE name='list_tasks'")
    try:
        await pool.execute("UPDATE tools SET enabled=FALSE, description='custom' WHERE name='list_tasks'")
        d = {x["name"]: x for x in await list_tool_defs(pool)}["list_tasks"]
        assert d["description"] == "custom" and d["builtin_default_description"] != "custom"
        r = await call_tool(pool, "list_tasks", {})
        assert not r["ok"] and "disabled" in r["error"]
    finally:  # restore exactly what was there
        await pool.execute("UPDATE tools SET enabled=$1, description=$2 WHERE name='list_tasks'",
                           old["enabled"], old["description"])

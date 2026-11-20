"""Contract 3: reports, run_no/step/usage on events, sessions, graph, markdown report."""
import asyncpg
import httpx
import pytest

from app.db import pool as dbpool
from app.main import app
from app.runner import llm, loop
from app.tasks import service as svc
from app.tools import registry


@pytest.fixture
async def pool(dsn, monkeypatch):
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    await registry.seed_builtin_tools(p)
    monkeypatch.setattr(dbpool, "_pool", p)
    yield p
    await p.close()


@pytest.fixture
async def client(pool):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


def tu(i, tool, text="", usage=(10, 5), **inp):
    content = [{"type": "text", "text": text}] if text else []
    content.append({"type": "tool_use", "id": f"t{i}", "name": tool, "input": inp})
    return {"content": content, "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}}


async def scripted_run(pool, monkeypatch):
    t = await svc.create_task(pool, "Scan demo", "", "check host", start=True)
    tid = int(t["id"])
    g1, _g2 = await svc.add_goals(pool, tid, ["find the banner", "second goal"])
    steps = iter([
        tu(1, "record_fact", "Looking around. More text.", summary="host is up"),
        tu(2, "insert_assets", assets=[{"type": "subdomain", "domain": "a.example.com"}]),
        tu(3, "report_finding", "Found a bug.", vulnclass="xss", name="Reflected XSS", severity="high",
           summary="reflects input", evidence="GET /?q=<x>", ),
        tu(4, "Read", file_path="/definitely/not/here"),
        tu(5, "prove_goal", "Proving.", goal_id=int(g1["id"]), evidence="banner seen",
           report="# Banner\nCurl returned `hello`.\n## Detail\nmore"),
        tu(6, "goal_met", reason="enough", report="# Wrap-up\nAll good."),
    ])

    async def fake(system, messages, tools, **kw):
        return next(steps)
    monkeypatch.setattr(llm, "call", fake)
    await loop.run_task(pool, tid)
    return tid, g1


async def test_runner_records_reports_and_event_meta(pool, monkeypatch):
    tid, _g1 = await scripted_run(pool, monkeypatch)
    t = await svc.get_task(pool, tid)
    assert t["status"] == "done" and t["stop_reason"] == "enough"
    assert t["summary_report"].startswith("# Wrap-up")
    goals = await svc.list_goals(pool, tid)
    assert goals[0]["report"].startswith("# Banner") and goals[1]["report"] == ""
    ev = await svc.list_events(pool, tid)
    llm_ev = [e for e in ev if e["kind"] == "llm"]
    assert [e["step"] for e in llm_ev] == [1, 2, 3, 4, 5, 6]
    assert all(e["run_no"] == 1 for e in llm_ev)
    assert llm_ev[0]["output"]["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert llm_ev[1]["output"]["text"] == ""  # one llm event per call even with empty text
    calls = [e for e in ev if e["kind"] == "tool_call"]
    assert [(c["tool_name"], c["run_no"], c["step"]) for c in calls][:2] == [("record_fact", 1, 1), ("insert_assets", 1, 2)]
    done = [e for e in ev if e["kind"] == "status" and e["output"]["status"] == "done"]
    assert done and done[0]["run_no"] == 1  # terminal status written by a runner tool call carries run_no
    assert ev[0]["run_no"] is None


async def test_second_run_gets_next_run_no(pool, monkeypatch):
    t = await svc.create_task(pool, "r", "", "g", start=True)
    tid = int(t["id"])
    for expect in (1, 2):
        await pool.execute("UPDATE tasks SET status='running', run_segment_started_at=now() WHERE id=$1", tid)
        n = iter([tu(1, "list_goals"), tu(2, "goal_met", reason="x")] if expect == 2 else [tu(1, "list_goals"), {"content": []}])

        async def fake(system, messages, tools, _n=n, **kw):
            return next(_n)
        monkeypatch.setattr(llm, "call", fake)
        monkeypatch.setenv("MAX_STEPS", "2")
        await loop.run_task(pool, tid)
        if expect == 1:
            await pool.execute("UPDATE tasks SET status='running', completed_at=NULL WHERE id=$1", tid)
    ev = await svc.list_events(pool, tid)
    assert {e["run_no"] for e in ev if e["kind"] == "llm"} == {1, 2}


async def test_patch_goal_report(pool, client):
    t = await svc.create_task(pool, "p", "", "g", start=False)
    tid = int(t["id"])
    g = (await svc.add_goals(pool, tid, ["a", "b"]))[0]
    r = await client.patch(f"/api/tasks/{tid}/goals/{g['id']}", json={"met": True, "evidence": "e", "report": "# R"})
    assert r.status_code == 200 and r.json()["report"] == "# R" and r.json()["evidence"] == "e"
    assert (await client.get(f"/api/tasks/{tid}")).json()["summary_report"] == ""


async def test_sessions_graph_report(pool, client, monkeypatch):
    tid, g1 = await scripted_run(pool, monkeypatch)
    # a manual call (no run_no) forms the final session
    await registry.call_tool(pool, "list_goals", {}, tid)

    s = (await client.get(f"/api/tasks/{tid}/sessions")).json()
    assert [x["label"] for x in s["sessions"]] == ["Run 1", "Manual calls"]
    run1, manual = s["sessions"]
    assert run1["run_no"] == 1 and run1["steps"] == 6 and run1["tool_calls"] == 6 and run1["errors"] == 1
    assert run1["input_tokens"] == 60 and run1["output_tokens"] == 30 and run1["ended_at"]
    assert manual["run_no"] is None and manual["tool_calls"] == 1 and manual["steps"] == 0
    assert s["totals"] == {"steps": 6, "tool_calls": 7, "errors": 1, "input_tokens": 60, "output_tokens": 30}

    f = (await client.get(f"/api/tasks/{tid}/findings")).json()["findings"]
    assert len(f) == 1 and f[0]["severity"] == "high" and f[0]["id"].isdigit()
    assert (await client.get(f"/api/tasks/{tid}/facts")).json()["facts"][0]["summary"] == "host is up"
    a = (await client.get(f"/api/tasks/{tid}/assets")).json()["assets"]
    assert a[0]["type"] == "subdomain" and a[0]["tested"] is False

    gr = (await client.get(f"/api/tasks/{tid}/graph")).json()
    nodes = {n["id"]: n for n in gr["nodes"]}
    edges = {(e["source"], e["target"], e["kind"]) for e in gr["edges"]}
    assert nodes["task"]["type"] == "task"
    assert nodes[f"goal:{g1['id']}"]["status"] == "met"
    assert ("task", f"goal:{g1['id']}", "plan") in edges
    assert ("task", "step:1:1", "flow") in edges and ("step:1:1", "step:1:2", "flow") in edges
    assert nodes["step:1:1"]["label"] == "Looking around."
    assert nodes["step:1:2"]["label"] == "insert_assets"
    assert nodes["step:1:4"]["status"] == "error" and nodes["step:1:3"]["status"] == "ok"
    assert nodes["step:1:1"]["data"]["input_tokens"] == 10
    assert nodes["step:1:1"]["data"]["tools"][0]["name"] == "record_fact"
    assert ("step:1:5", f"goal:{g1['id']}", "proves") in edges
    fid, finid, aid = f"fact:{(await client.get(f'/api/tasks/{tid}/facts')).json()['facts'][0]['id']}", f"finding:{f[0]['id']}", f"asset:{a[0]['id']}"
    assert ("step:1:1", fid, "creates") in edges
    assert ("step:1:2", aid, "creates") in edges
    assert ("step:1:3", finid, "creates") in edges
    assert nodes[finid]["type"] == "finding" and nodes[aid]["type"] == "asset"
    assert "step:m:1" in nodes and nodes["step:m:1"]["run_no"] is None
    assert all(e["source"] in nodes and e["target"] in nodes for e in gr["edges"])
    assert "truncated" not in gr

    r = await client.get(f"/api/tasks/{tid}/report")
    md = r.json()["markdown"]
    assert r.json()["generated_at"]
    assert md.startswith("# Scan demo — Report")
    heads = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert heads == ["## Summary", "## Goals", "## Findings", "## Facts", "## Assets", "## Process"]
    assert "# Wrap-up\nAll good." in md  # summary_report verbatim
    assert "### ✓ find the banner" in md and "### ○ second goal" in md
    assert "**Evidence:** banner seen" in md
    assert "#### Banner" in md and "##### Detail" in md  # goal report headings demoted to H4+
    assert "### [HIGH] Reflected XSS" in md
    assert "| subdomain | a.example.com | no |" in md
    assert "**Run 1**: 6 steps" in md

    d = await client.get(f"/api/tasks/{tid}/report?download=1")
    assert d.headers["content-type"].startswith("text/markdown; charset=utf-8")
    assert d.headers["content-disposition"] == f'attachment; filename="task-{tid}-report.md"'
    assert d.text.startswith("# Scan demo")


async def test_empty_task_views(pool, client):
    t = await svc.create_task(pool, "", "", "g", start=False)
    tid = t["id"]
    s = (await client.get(f"/api/tasks/{tid}/sessions")).json()
    assert s["sessions"] == [] and s["totals"]["steps"] == 0
    gr = (await client.get(f"/api/tasks/{tid}/graph")).json()
    assert [n["id"] for n in gr["nodes"]] == ["task"]
    md = (await client.get(f"/api/tasks/{tid}/report")).json()["markdown"]
    assert md.startswith(f"# Task {tid} — Report") and "_No summary yet._" in md and "## Goals" not in md
    assert (await client.get("/api/tasks/99999999/graph")).status_code == 404


async def test_openai_usage_mapping(monkeypatch):
    async def fake_post(url, headers, body):
        return {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setenv("LLM_FORMAT", "openai")
    r = await llm.call("s", [{"role": "user", "content": "x"}], [])
    assert r["usage"] == {"input_tokens": 7, "output_tokens": 3}

    async def anth(url, headers, body):
        return {"content": [], "usage": {"input_tokens": 4, "output_tokens": 2, "cache_read_input_tokens": 9}}
    monkeypatch.setattr(llm, "_post", anth)
    monkeypatch.setenv("LLM_FORMAT", "anthropic")
    assert (await llm.call("s", [], []))["usage"] == {"input_tokens": 4, "output_tokens": 2}

    async def none(url, headers, body):
        return {"content": []}
    monkeypatch.setattr(llm, "_post", none)
    assert (await llm.call("s", [], []))["usage"] == {"input_tokens": 0, "output_tokens": 0}

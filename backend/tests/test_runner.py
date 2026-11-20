from app.runner import llm, loop


def tu(i, name, **inp):
    return {"content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}


def test_happy_path(run, monkeypatch):
    async def go(pool):
        from app.tasks import service as svc
        t = await svc.create_task(pool, "t", "", "do it", start=False)
        tid = int(t["id"])
        g = (await svc.add_goals(pool, tid, ["g1", "g2"]))[0]
        await pool.execute("UPDATE tasks SET status='running', run_segment_started_at=now() WHERE id=$1", tid)
        steps = [
            {"content": [{"type": "text", "text": "hinting"}, {"type": "tool_use", "id": "a", "name": "add_task_hint", "input": {"text": "be quick"}}]},
            tu(2, "prove_goal", goal_id=int(g["id"]), evidence="ok"),
            tu(3, "goal_met", reason="all done"),
        ]
        it = iter(steps)

        async def fake(system, messages, tools, **kw):
            assert any(x["name"] == "goal_met" for x in tools)
            return next(it)
        monkeypatch.setattr(llm, "call", fake)
        await loop.run_task(pool, tid)
        t = await svc.get_task(pool, tid)
        assert t["status"] == "done"
        ev = await svc.list_events(pool, tid)
        kinds = {e["kind"] for e in ev}
        assert {"llm", "tool_call"} <= kinds
        assert {e["tool_name"] for e in ev if e["kind"] == "tool_call"} >= {"add_task_hint", "prove_goal", "goal_met"}
        assert (await svc.list_hints(pool, tid))[0]["text"] == "be quick"
    run(go)


def test_max_steps(run, monkeypatch):
    async def go(pool):
        from app.tasks import service as svc
        monkeypatch.setenv("MAX_STEPS", "3")
        t = await svc.create_task(pool, "t", "", "never", start=True)
        n = 0

        async def fake(system, messages, tools, **kw):
            nonlocal n
            n += 1
            return tu(n, "list_tasks")
        monkeypatch.setattr(llm, "call", fake)
        await loop.run_task(pool, int(t["id"]))
        t = await svc.get_task(pool, int(t["id"]))
        assert (t["status"], t["stop_reason"]) == ("failed", "max_steps") and n == 3
    run(go)


def test_pause_stops_loop(run, monkeypatch):
    async def go(pool):
        from app.tasks import service as svc
        t = await svc.create_task(pool, "t", "", "x", start=True)
        tid = int(t["id"])
        n = 0

        async def fake(system, messages, tools, **kw):
            nonlocal n
            n += 1
            if n == 2:
                await svc.pause_task(pool, tid)
            return tu(n, "list_tasks")
        monkeypatch.setattr(llm, "call", fake)
        await loop.run_task(pool, tid)
        t = await svc.get_task(pool, tid)
        assert t["status"] == "paused" and n == 2
    run(go)


def test_system_prompt_stable_across_steps(run, monkeypatch):
    """Regression: a changing system prompt invalidates thinking-block signatures (HTTP 400)."""
    async def go(pool):
        from app.tasks import service as svc
        t = await svc.create_task(pool, "t", "", "stable", start=True)
        tid = int(t["id"])
        steps = iter([
            tu(1, "set_goals", goals=["a", "b"]),
            tu(2, "add_task_hint", text="x"),
            tu(3, "goal_met", reason="done"),
        ])
        systems = []

        async def fake(system, messages, tools, **kw):
            systems.append(system)
            return next(steps)
        monkeypatch.setattr(llm, "call", fake)
        await loop.run_task(pool, tid)
        assert len(systems) == 3 and len(set(systems)) == 1
    run(go)


def test_bash_curl_stub_then_goal_met(run, monkeypatch):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"stub-says-hello")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    async def go(pool):
        from app.tasks import service as svc
        t = await svc.create_task(pool, "t", "", "curl stub", start=False)
        tid = int(t["id"])
        await pool.execute("UPDATE tasks SET status='running', run_segment_started_at=now() WHERE id=$1", tid)
        seen: list[str] = []
        steps = iter([tu(1, "Bash", command=f"curl -s http://127.0.0.1:{port}/"), tu(2, "goal_met", reason="fetched")])

        async def fake(system, messages, tools, **kw):
            if len(messages) > 1:
                seen.append(str(messages[-1]["content"]))
            return next(steps)
        monkeypatch.setattr(llm, "call", fake)
        await loop.run_task(pool, tid)
        assert (await svc.get_task(pool, tid))["status"] == "done"
        assert any("stub-says-hello" in s for s in seen)
    try:
        run(go)
    finally:
        srv.shutdown()
        srv.server_close()

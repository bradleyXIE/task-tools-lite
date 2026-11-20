import asyncio
import os
import tempfile

import asyncpg
import pytest

from app.tools.base import ToolContext, ToolError
from app.tools.sdk import background as bg
from app.tools.sdk import shell as sh

@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setenv("DATA_DIR", tempfile.mkdtemp())
    bg.reset()
    yield
    await bg.shutdown()
    bg.reset()


def T(name):
    return next(t for t in bg.TOOLS + sh.TOOLS if t.name == name)


async def call(name, ctx, **a):
    return await T(name).run(ctx, a)


async def test_start_output_block_list():
    ctx = ToolContext(None, None)
    r = await bg.start(ctx, "echo hi; echo err >&2; exit 3", "d")
    assert r["task_id"] == "task_1" and "/.bg/" in r["output_file"]
    out = await call("TaskOutput", ctx, task_id="task_1", block=True, timeout_ms=5000)
    assert out.startswith("[task task_1 | failed | exit 3]") and "hi" in out and "err" in out
    lst = await call("TaskList", ctx)
    assert "task_1 [bash] failed exit=3" in lst
    with pytest.raises(ToolError, match="unknown task"):
        await call("TaskOutput", ctx, task_id="nope")


async def test_monitor_and_stop_kills_tree():
    ctx = ToolContext(None, None)
    msg = await call("Monitor", ctx, command="(sleep 300 &) ; while true; do echo tick; sleep 0.1; done")
    assert msg.startswith("Started monitor task_1.")
    await asyncio.sleep(0.6)
    assert "tick" in await call("TaskOutput", ctx, task_id="task_1")
    assert await call("TaskStop", ctx, task_id="task_1") == "Stopped task task_1."
    out = await call("TaskOutput", ctx, task_id="task_1", block=True, timeout_ms=3000)
    assert "| killed]" in out
    assert "[monitor] killed" in await call("TaskList", ctx)
    assert await call("TaskStop", ctx) == "Stopped 0 running task(s)."


async def test_stop_all_and_scopes():
    a, b = ToolContext(None, None), ToolContext(None, 7)
    await bg.start(a, "sleep 60")
    await bg.start(a, "sleep 60")
    await bg.start(b, "sleep 60")
    assert await call("TaskStop", a) == "Stopped 2 running task(s)."
    assert "task_1 [bash] running" in await call("TaskList", b)
    with pytest.raises(ToolError):
        await call("TaskOutput", ToolContext(None, 99), task_id="task_1")


async def test_tail_truncation():
    ctx = ToolContext(None, None)
    await bg.start(ctx, "head -c 5000 /dev/zero | tr '\\0' x")
    out = await call("TaskOutput", ctx, task_id="task_1", block=True, max_bytes=100)
    assert "earlier output omitted" in out


async def test_todowrite_logs_event(dsn):
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        from app.tasks import service as svc
        t = await svc.create_task(pool, "t", "", "g", 0, False)
        ctx = ToolContext(pool, int(t["id"]))
        r = await call("TodoWrite", ctx, todos=[{"content": "a", "status": "in_progress"}])
        assert r == bg.TODO_ACK and bg.todos(ctx.task_id)[0]["content"] == "a"
        await call("TodoWrite", ctx, todos=[{"content": "b", "status": "pending"}])
        assert [x["content"] for x in bg.todos(ctx.task_id)] == ["b"]
        evs = await svc.list_events(pool, int(t["id"]))
        assert any(e["kind"] == "note" and e["tool_name"] == "TodoWrite" for e in evs)
    finally:
        await pool.close()


async def test_shell_session_flow():
    ctx = ToolContext(None, None)
    o = await call("shell_open", ctx, command="python3 -q -i", mode="interactive", prompt_regex=r">>> $",
                   quiet_ms=3000)
    assert o["done_by"] == "prompt" and o["running"]
    sid = o["session_id"]
    r = await call("shell_send", ctx, session_id=sid, text="print(6*7)", submit=True, prompt_regex=r">>> $")
    assert "42" in r["output"] and r["done_by"] == "prompt"
    lst = await call("shell_list", ctx)
    assert lst["sessions"][0]["session_id"] == sid
    scr = await call("shell_read", ctx, session_id=sid, view="screen")
    assert "42" in scr["screen"]
    r = await call("shell_send", ctx, session_id=sid, keys=["ctrl+d"])
    assert r["done_by"] == "exited" and not r["running"]
    rd = await call("shell_read", ctx, session_id=sid, since_cursor=0, drain=True)
    assert "42" in rd["output"] and rd["exit_code"] == 0
    with pytest.raises(ToolError, match="session ended"):
        await call("shell_send", ctx, session_id=sid, text="x")
    c = await call("shell_close", ctx, session_id=sid)
    assert c["closed"] == sid
    with pytest.raises(ToolError, match="not found"):
        await call("shell_close", ctx, session_id=sid)


async def test_shell_hands_free_and_validation():
    ctx = ToolContext(None, None)
    with pytest.raises(ToolError, match="watch"):
        await call("shell_open", ctx, command="cat", mode="monitor")
    with pytest.raises(ToolError, match="mode"):
        await call("shell_open", ctx, command="cat", mode="x")
    o = await call("shell_open", ctx, command="cat")
    sid = o["session_id"]
    with pytest.raises(ToolError, match="unknown key"):
        await call("shell_send", ctx, session_id=sid, keys=["bogus"])
    with pytest.raises(ToolError, match="nothing to send"):
        await call("shell_send", ctx, session_id=sid)
    await call("shell_send", ctx, session_id=sid, text="hello", submit=True, wait=False)
    await asyncio.sleep(0.4)
    rd = await call("shell_read", ctx, session_id=sid)
    assert "hello" in rd["output"]
    assert (await call("shell_read", ctx, session_id=sid))["output"] == ""  # cursor continues
    await call("shell_close", ctx, session_id=sid)


async def test_shell_monitor_notifies(dsn):
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        from app.tasks import service as svc
        t = await svc.create_task(pool, "t", "", "g", 0, False)
        tid = int(t["id"])
        ctx = ToolContext(pool, tid)
        o = await call("shell_open", ctx, command="sleep 0.3; echo FOUND_IT; sleep 30", mode="monitor",
                       watch=["FOUND_IT"])
        assert o["monitoring"] == 1
        for _ in range(40):
            evs = await svc.list_events(pool, tid)
            if any("matched" in str(e["output"]) for e in evs):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("no monitor note")
        await call("shell_close", ctx, session_id=o["session_id"])
    finally:
        await pool.close()


def test_screen_emulator():
    s = sh.Screen(4, 10)
    s.feed(b"hello\r\nab\x1b[1;1HX\x1b[31mred\x1b[0m\x1b[3;2Hz\x1b[K")
    assert s.render().splitlines()[0] == "Xredo"
    assert s.render().splitlines()[2] == " z"
    assert os.name == "posix"

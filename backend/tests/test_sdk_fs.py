import sys
import time
import types

import pytest

from app.tools.base import ToolContext, ToolError
from app.tools.builtin import BUILTIN_TOOLS
from app.tools.registry import call_tool


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return ToolContext(None, 987654)


async def run(ctx, name, **args):
    try:
        return True, await BUILTIN_TOOLS[name].run(ctx, args)
    except ToolError as e:
        return False, str(e)


def test_registered():
    for n in ["Read", "Write", "Edit", "MultiEdit", "LS", "Glob", "Grep", "sleep", "Bash"]:
        assert n in BUILTIN_TOOLS


async def test_write_read_edit(ctx):
    ok, r = await run(ctx, "Write", file_path="a/b.txt", content="one\ntwo\ntwo\n")
    assert ok and r.startswith("Created a/b.txt (12 bytes)")
    assert (await run(ctx, "Write", file_path="a/b.txt", content="x"))[1].startswith("Overwrote")
    await run(ctx, "Write", file_path="a/b.txt", content="one\ntwo\ntwo\n")
    ok, r = await run(ctx, "Read", file_path="a/b.txt", offset=2, limit=1)
    assert r == "     2\ttwo\n"
    ok, r = await run(ctx, "Edit", file_path="a/b.txt", old_string="two", new_string="2")
    assert not ok and "not unique in a/b.txt (2 matches)" in r
    ok, r = await run(ctx, "Edit", file_path="a/b.txt", old_string="two", new_string="2", replace_all=True)
    assert r == "Edited a/b.txt (2 replacement(s))"
    ok, r = await run(ctx, "Edit", file_path="a/b.txt", old_string="zzz", new_string="y")
    assert not ok and "not found" in r
    ok, r = await run(ctx, "MultiEdit", file_path="a/b.txt", edits=[
        {"old_string": "one", "new_string": "1"}, {"old_string": "nope", "new_string": "x"}])
    assert not ok and "edit 2" in r
    assert "one" in (await run(ctx, "Read", file_path="a/b.txt"))[1]  # unchanged
    ok, r = await run(ctx, "MultiEdit", file_path="a/b.txt", edits=[
        {"old_string": "one", "new_string": "1"}, {"old_string": "1", "new_string": "uno"}])
    assert r == "Applied 2 edits to a/b.txt (2 replacement(s))" or r.startswith("Applied 2 edits")


async def test_read_misc(ctx):
    await run(ctx, "Write", file_path="e.txt", content="")
    assert (await run(ctx, "Read", file_path="e.txt"))[1] == "(file is empty)"
    ok, r = await run(ctx, "Read", file_path=".")
    assert not ok and "directory" in r
    (ctx.workdir() / "bin").write_bytes(b"a\0b")
    assert "binary file" in (await run(ctx, "Read", file_path="bin"))[1]
    ok, r = await run(ctx, "Read", file_path="missing")
    assert not ok


async def test_ls_glob_grep(ctx):
    await run(ctx, "Write", file_path="src/a.py", content="def foo():\n    return 1\n")
    await run(ctx, "Write", file_path="src/sub/b.py", content="# Foo\nx = 2\n")
    await run(ctx, "Write", file_path="node_modules/c.py", content="foo")
    await run(ctx, "Write", file_path="n.txt", content="foo bar\n")
    ls = (await run(ctx, "LS"))[1]
    assert ls.splitlines()[0] == "node_modules/" and "n.txt\t8 bytes" in ls
    assert (await run(ctx, "LS", path="src/sub"))[1].startswith("b.py")
    g = (await run(ctx, "Glob", pattern="**/*.py"))[1].splitlines()
    assert len(g) == 2 and all("node_modules" not in x for x in g)
    assert (await run(ctx, "Glob", pattern="*.zzz"))[1] == "No files found"
    assert (await run(ctx, "Grep", pattern="foo"))[1].splitlines() == ["n.txt", "src/a.py"]
    assert (await run(ctx, "Grep", pattern="foo", **{"-i": True, "output_mode": "count"}))[1].splitlines() == [
        "n.txt: 1", "src/a.py: 1", "src/sub/b.py: 1"]
    r = (await run(ctx, "Grep", pattern="foo", output_mode="content", glob="*.py"))[1]
    assert r == "src/a.py:1:def foo():\n"
    r = (await run(ctx, "Grep", pattern="foo", output_mode="content", type="py", **{"-A": 1}))[1]
    assert r == "src/a.py:1:def foo():\nsrc/a.py-2-    return 1\n"
    assert (await run(ctx, "Grep", pattern="nomatch"))[1] == "No matches found"
    ok, r = await run(ctx, "Grep", pattern="(")
    assert not ok and "invalid regex" in r
    r = (await run(ctx, "Grep", pattern="foo", head_limit=1, **{"-i": True}))[1]
    assert "more result(s)" in r
    r = (await run(ctx, "Grep", pattern=r"def foo\(\):\n\s+return", multiline=True, output_mode="count"))[1]
    # ripgrep (installed in the container) counts 1 multiline match; the pure-Python fallback counts 2 lines.
    assert r in ("src/a.py: 1\n", "src/a.py: 2\n")


async def test_sleep_and_bash(ctx):
    t = time.time()
    assert (await run(ctx, "sleep", seconds=1))[1] == "slept 1s"
    assert time.time() - t >= 0.9
    assert (await run(ctx, "Bash", command="echo hi; echo err >&2"))[1].split() == ["hi", "err"]
    assert (await run(ctx, "Bash", command="true"))[1] == "(command produced no output)"
    ok, r = await run(ctx, "Bash", command="echo x; exit 3")
    assert not ok and r.endswith("[exit code 3]") and r.startswith("x")
    assert (await run(ctx, "Bash", command="pwd"))[1].strip().endswith("tasks/987654")
    t = time.time()
    ok, r = await run(ctx, "Bash", command="sleep 30 & sleep 30", timeout_ms=500)
    assert not ok and "timed out after 0.5s" in r and time.time() - t < 5
    ok, r = await run(ctx, "Bash", command="rm -rf /")
    assert not ok and "safety floor" in r


async def test_bash_background(ctx, monkeypatch):
    calls = []

    async def start(c, command, description=""):
        calls.append(command)
        return {"task_id": "task_1", "output_file": "/tmp/x"}

    fake = types.ModuleType("app.tools.sdk.background")
    fake.start = start
    from app.tools import sdk
    monkeypatch.setitem(sys.modules, "app.tools.sdk.background", fake)
    monkeypatch.setattr(sdk, "background", fake, raising=False)
    r = (await run(ctx, "Bash", command="echo z", run_in_background=True))[1]
    assert "Running in background as task_1" in r and calls == ["echo z"]


async def test_via_registry_validation():
    class P:  # pool unused for builtin without task_id
        async def fetchrow(self, *a):
            return None
    r = await call_tool(P(), "Read", {})
    assert not r["ok"] and "invalid arguments" in r["error"]

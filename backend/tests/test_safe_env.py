"""Agent-spawned processes must not inherit the API process's secrets."""
import asyncio

import pytest

from app.tools.base import ToolContext
from app.tools.builtin import BUILTIN_TOOLS
from app.tools.sdk.safeenv import child_env, is_sensitive

SECRETS = {
    "LLM_API_KEY": "sk-test-SECRET-llm",
    "DATABASE_URL": "postgresql://u:SECRET-db@h/d",
    "TAVILY_API_KEY": "SECRET-tavily",
    "MY_SERVICE_TOKEN": "SECRET-token",
    "ANTHROPIC_AUTH_TOKEN": "SECRET-anthropic",
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TOOL_ENV_PASSTHROUGH", raising=False)
    return ToolContext(None, 987655)


def test_sensitive_names():
    for n in ("LLM_API_KEY", "DATABASE_URL", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "PGPASSWORD", "BRAVE_API_KEY"):
        assert is_sensitive(n), n
    for n in ("PATH", "HOME", "LANG", "HTTP_PROXY", "DATA_DIR", "TERM"):
        assert not is_sensitive(n), n


def test_child_env_filters_and_passthrough(ctx, monkeypatch):
    env = child_env({"TERM": "xterm"})
    assert "PATH" in env and env["TERM"] == "xterm"
    assert not any(k in env for k in SECRETS)
    monkeypatch.setenv("TOOL_ENV_PASSTHROUGH", "MY_SERVICE_TOKEN")
    assert child_env()["MY_SERVICE_TOKEN"] == SECRETS["MY_SERVICE_TOKEN"]
    assert "LLM_API_KEY" not in child_env()


async def test_bash_cannot_read_secrets(ctx):
    out = await BUILTIN_TOOLS["Bash"].run(ctx, {"command": "env; echo \"[$LLM_API_KEY]\"; printenv DATABASE_URL || true"})
    assert "SECRET" not in out and "LLM_API_KEY=" not in out
    assert "[]" in out
    assert "PATH=" in out


async def test_background_cannot_read_secrets(ctx):
    r = await BUILTIN_TOOLS["Bash"].run(ctx, {"command": "env", "run_in_background": True})
    tid = str(r).split("task_")[1].split(".")[0].split()[0]
    for _ in range(40):
        out = await BUILTIN_TOOLS["TaskOutput"].run(ctx, {"task_id": f"task_{tid}", "block": True, "timeout_ms": 3000})
        if "PATH=" in str(out):
            break
        await asyncio.sleep(0.1)
    assert "PATH=" in str(out) and "SECRET" not in str(out)

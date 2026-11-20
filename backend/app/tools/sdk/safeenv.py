"""Environment for processes spawned by agent tools (Bash, background tasks, shell sessions).

The API process holds secrets (LLM_API_KEY, DATABASE_URL, search keys). A model-driven shell must never be able
to read them with `env` or `echo $LLM_API_KEY`, so child processes get a filtered copy of the environment.
Set TOOL_ENV_PASSTHROUGH=NAME1,NAME2 to deliberately forward specific variables.
"""
from __future__ import annotations

import os
import re

_SENSITIVE_NAME = re.compile(
    r"(^LLM_|^DATABASE_URL$|^PG|^POSTGRES_|^ANTHROPIC_|^OPENAI_|^TAVILY_|^BRAVE_"
    r"|API_?KEY|TOKEN|SECRET|PASSW(OR)?D|CREDENTIAL|PRIVATE_?KEY|^AWS_|^GITHUB_|^GH_)",
    re.IGNORECASE,
)


def is_sensitive(name: str) -> bool:
    return bool(_SENSITIVE_NAME.search(name))


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """os.environ without secrets (plus `extra`). Names listed in TOOL_ENV_PASSTHROUGH are kept."""
    allow = {n.strip() for n in os.environ.get("TOOL_ENV_PASSTHROUGH", "").split(",") if n.strip()}
    env = {k: v for k, v in os.environ.items() if k in allow or not is_sensitive(k)}
    env.update(extra or {})
    return env

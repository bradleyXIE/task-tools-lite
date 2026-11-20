"""Core tool types shared by builtin and custom tools."""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def data_dir() -> Path:
    """Root for runtime files (task workdirs, background output). Override with DATA_DIR."""
    d = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[3] / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class ToolContext:
    pool: Any
    task_id: int | None = None
    run_no: int | None = None
    step: int | None = None

    def workdir(self) -> Path:
        """Per-task working directory (data/tasks/<id>), or data/scratch without a task. Created on demand."""
        p = data_dir() / ("tasks/%d" % self.task_id if self.task_id is not None else "scratch")
        p.mkdir(parents=True, exist_ok=True)
        return p


class ToolError(Exception):
    """Raised by a tool handler to report a failure to the caller."""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable[[ToolContext, dict], Awaitable[Any]]
    read_only: bool = False
    # Extra fields are not part of the contract; kept minimal.
    meta: dict = field(default_factory=dict)

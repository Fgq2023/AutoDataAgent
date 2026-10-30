"""Run configuration for AutoDataAgent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines) — no external dependency."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class AgentConfig:
    """Everything a run needs to know about its environment."""

    data_root: str = "data"          # where data sources live
    outputs_root: str = "outputs"    # where run directories are created
    mode: str = "mock"               # mock | llm  (planner flavour)
    inject_failure: str | None = None   # "s3" (transient) | "s3:permanent"
    exec_timeout_s: float = 120.0    # sandbox subprocess timeout
    max_attempts: int = 3            # hard cap on attempts per step
    max_replans: int = 3             # hard cap for the entire run
    task_contracts: list[dict] = field(default_factory=list)

    def __post_init__(self):
        from agent.verification.task import validate_contracts
        validate_contracts(self.task_contracts)
        if self.max_attempts < 1 or self.max_replans < 0:
            raise ValueError("max_attempts must be positive; max_replans must be nonnegative")


def load_config(**overrides) -> AgentConfig:
    """Build an :class:`AgentConfig`, applying ``.env`` then CLI overrides.

    ``mode`` defaults to ``mock`` unless an ``OPENAI_API_KEY`` is present in
    the environment, in which case ``llm`` is the default.
    """
    _load_dotenv()
    defaults: dict = {
        "mode": "llm" if os.environ.get("OPENAI_API_KEY") else "mock",
    }
    merged = {**defaults, **{k: v for k, v in overrides.items() if v is not None}}
    return AgentConfig(**merged)

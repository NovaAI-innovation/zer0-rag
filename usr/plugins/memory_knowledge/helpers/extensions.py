from __future__ import annotations

from typing import Any


def extension_agent(extension: Any) -> Any | None:
    return getattr(extension, "agent", None)


def remember_error(agent: Any | None, exc: Exception) -> None:
    if agent is None:
        return
    try:
        setattr(agent, "memory_knowledge_error", str(exc))
    except Exception:
        pass

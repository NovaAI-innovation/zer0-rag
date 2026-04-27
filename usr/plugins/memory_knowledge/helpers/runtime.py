from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from usr.plugins.memory_knowledge.helpers import db


PLUGIN_NAME = "memory_knowledge"


@dataclass
class MemoryRuntime:
    settings: db.Settings
    enabled: bool = True
    run_id: str | None = None
    run_key: str | None = None
    thread_key: str | None = None
    turn_index: int = 0
    step_index: int = 0
    thought_index: int = 0
    response_chunks: list[str] = field(default_factory=list)
    pending_response_thoughts: list[str] = field(default_factory=list)
    pending_response_envelope: str = ""
    recorded_thought_keys: set[str] = field(default_factory=set)
    last_message_ids: dict[str, str] = field(default_factory=dict)
    injected_memory_ids: list[str] = field(default_factory=list)
    injected_memory_block: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def settings_for_agent(agent: Any | None = None, fallback: Mapping[str, Any] | None = None) -> db.Settings:
    config: Mapping[str, Any] | None = fallback
    if agent is not None:
        try:
            from helpers.plugins import get_plugin_config

            config = get_plugin_config(PLUGIN_NAME, agent=agent) or fallback
        except Exception:
            config = fallback
    return db.load_settings(config)


def load_memory_runtime(agent: Any | None = None) -> MemoryRuntime:
    settings = settings_for_agent(agent)
    runtime = MemoryRuntime(settings=settings, enabled=settings.lifecycle_enabled)
    if agent is not None:
        setattr(agent, "memory_knowledge", runtime)
    return runtime


def ensure_runtime(agent: Any | None = None) -> MemoryRuntime | None:
    runtime = getattr(agent, "memory_knowledge", None) if agent is not None else None
    if runtime is not None:
        return runtime
    try:
        settings = settings_for_agent(agent)
    except Exception:
        return None
    runtime = MemoryRuntime(settings=settings, enabled=settings.lifecycle_enabled)
    if agent is not None:
        setattr(agent, "memory_knowledge", runtime)
    return runtime

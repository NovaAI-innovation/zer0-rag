from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from usr.plugins.memory_knowledge.helpers import db


@dataclass
class MemoryRuntime:
    settings: db.MemorySettings
    context: dict[str, Any] | None
    enabled: bool = True
    can_read: bool = False
    can_write: bool = False
    can_promote: bool = False
    allowed_kinds: list[str] | None = None
    denied_tags: list[str] = field(default_factory=list)
    max_context_items: int = 10
    trace_run_id: str | None = None
    external_thread_id: str | None = None
    response_chunks: list[str] = field(default_factory=list)
    injected_memory_block: str = ""


def _plugin_config(agent: Any) -> dict[str, Any]:
    try:
        from helpers.plugins import get_plugin_config
    except Exception:
        return {}
    try:
        return get_plugin_config("memory_knowledge", agent=agent) or {}
    except TypeError:
        return get_plugin_config("memory_knowledge") or {}
    except Exception:
        return {}


async def load_memory_runtime(agent: Any) -> MemoryRuntime:
    settings = db.load_settings(_plugin_config(agent))
    try:
        context = db.load_context(settings)
    except Exception as exc:
        runtime = MemoryRuntime(settings=settings, context=None, enabled=False)
        try:
            db.log_diagnostic(settings, "warning", "memory", "context_load_failed", str(exc), "Check database URL and memory seed rows.")
        except Exception:
            pass
        return runtime

    runtime = MemoryRuntime(settings=settings, context=context)
    if not context:
        runtime.enabled = False
        return runtime

    runtime.can_read = bool(context.get("can_read")) and settings.retrieval_enabled
    runtime.can_write = bool(context.get("can_write")) and settings.writes_enabled
    runtime.can_promote = bool(context.get("can_promote")) and settings.auto_promote
    runtime.allowed_kinds = list(context.get("allowed_memory_kinds") or [])
    runtime.denied_tags = list(context.get("denied_tags") or [])
    runtime.max_context_items = min(int(context.get("max_context_items") or settings.default_limit), settings.default_limit)
    return runtime


def ensure_runtime(agent: Any) -> MemoryRuntime | None:
    return getattr(agent, "memory_knowledge", None)

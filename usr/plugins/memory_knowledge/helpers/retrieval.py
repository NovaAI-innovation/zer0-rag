from __future__ import annotations

from typing import Any, Mapping

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.recorder import latest_user_text
from usr.plugins.memory_knowledge.helpers.runtime import MemoryRuntime


def retrieve_for_turn(agent: Any, runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> list[dict[str, Any]]:
    query = latest_user_text(agent, kwargs)
    if not query:
        return []
    rows = db.search_memory(runtime.settings, query, limit=runtime.settings.default_limit, include_inactive=False)
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("id") or row.get("memory_item_id"))
        deduped.setdefault(key, row)
    result = list(deduped.values())[: runtime.settings.default_limit]
    if runtime.settings.auto_reinforce_memories:
        try:
            db.record_memory_access(
                runtime.settings,
                [str(row.get("id") or row.get("memory_item_id")) for row in result if row.get("id") or row.get("memory_item_id")],
                reason="retrieved_for_turn",
            )
        except Exception:
            pass
    return result


def format_memory_block(rows: list[dict[str, Any]], max_chars: int) -> str:
    if not rows:
        return ""
    lines = ["Relevant memory:"]
    for row in rows:
        memory_id = str(row.get("id") or row.get("memory_item_id") or "")
        kind = row.get("kind") or "memory"
        summary = str(row.get("summary") or row.get("title") or "").replace("\n", " ").strip()
        if summary:
            lines.append(f"- [{kind} {memory_id[:8]}] {summary}")
    block = "\n".join(lines)
    if len(block) > max_chars:
        return block[: max(0, max_chars - 3)].rstrip() + "..."
    return block


def inject_memory_block(agent: Any, runtime: MemoryRuntime, kwargs: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    if not runtime.settings.inject_context:
        return ""
    block = format_memory_block(rows, runtime.settings.max_context_chars)
    runtime.injected_memory_block = block
    runtime.injected_memory_ids = [str(row.get("id") or row.get("memory_item_id")) for row in rows if row.get("id") or row.get("memory_item_id")]
    if not block:
        return ""
    for key in ("system_prompt", "prompt", "message"):
        value = kwargs.get(key)
        if isinstance(value, str):
            kwargs[key] = f"{value}\n\n{block}"
            return block
    prompts = kwargs.get("prompts")
    if isinstance(prompts, list):
        prompts.append(block)
        return block
    setattr(agent, "memory_context", f"{getattr(agent, 'memory_context', '')}\n\n{block}".strip())
    return block

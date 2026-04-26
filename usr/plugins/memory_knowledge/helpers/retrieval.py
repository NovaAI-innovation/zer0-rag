from __future__ import annotations

from typing import Any

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import MemoryRuntime


def latest_text(agent: Any, kwargs: dict[str, Any]) -> str:
    for key in ("message", "prompt", "input", "user_message", "content"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    history = getattr(agent, "history", None) or getattr(agent, "messages", None)
    if isinstance(history, list):
        for item in reversed(history):
            if isinstance(item, dict) and item.get("role") == "user" and item.get("content"):
                return str(item["content"]).strip()
    return ""


async def retrieve_for_turn(agent: Any, runtime: MemoryRuntime, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    query = latest_text(agent, kwargs)
    if not query:
        return []

    limit = runtime.max_context_items or runtime.settings.default_limit
    kinds = runtime.allowed_kinds
    results: list[dict[str, Any]] = []

    embedding = kwargs.get("embedding") or kwargs.get("query_embedding")
    if embedding:
        try:
            results.extend(db.search_vector(runtime.settings, embedding, limit, runtime.settings.similarity_threshold, kinds))
        except Exception as exc:
            db.log_diagnostic(runtime.settings, "warning", "retrieval", "vector_search_failed", str(exc))

    try:
        results.extend(db.search_text(runtime.settings, query, limit, kinds))
    except Exception as exc:
        db.log_diagnostic(runtime.settings, "warning", "retrieval", "text_search_failed", str(exc))

    deduped: dict[str, dict[str, Any]] = {}
    for row in results:
        memory_id = str(row.get("memory_item_id"))
        if memory_id not in deduped:
            deduped[memory_id] = row
    return list(deduped.values())[:limit]


def format_memory_block(memories: list[dict[str, Any]], max_chars: int) -> str:
    if not memories:
        return ""
    lines = ["Relevant memory:"]
    for memory in memories:
        kind = memory.get("kind", "memory")
        confidence = memory.get("confidence")
        suffix = f", confidence {float(confidence):.2f}" if confidence is not None else ""
        summary = str(memory.get("summary") or memory.get("title") or "").replace("\n", " ").strip()
        if not summary:
            continue
        lines.append(f"- [{kind}{suffix}] {summary}")
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 3].rstrip() + "..."
    return block


def inject_memory_block(agent: Any, runtime: MemoryRuntime, kwargs: dict[str, Any], memories: list[dict[str, Any]]) -> str:
    block = format_memory_block(memories, runtime.settings.max_summary_chars)
    runtime.injected_memory_block = block
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
    elif hasattr(agent, "memory_context"):
        agent.memory_context = f"{getattr(agent, 'memory_context')}\n\n{block}".strip()
    else:
        setattr(agent, "memory_context", block)
    return block

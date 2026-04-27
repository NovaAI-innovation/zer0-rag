from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.enrichment import EnrichmentField, enrich_fields, plain_text
from usr.plugins.memory_knowledge.helpers.recorder import assistant_text, latest_user_text
from usr.plugins.memory_knowledge.helpers.runtime import MemoryRuntime


PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\|/a0/|usr/|supabase/|\.?[A-Za-z0-9_-]+/)[A-Za-z0-9_./\\-]+")
URL_PATTERN = re.compile(r"https?://[^\s)>\"]+")
PROJECT_PATTERN = re.compile(r"\b(?:project|repo|repository|plugin|schema|table|tool)\s+[`'\"]?([A-Za-z0-9_.:-]{3,80})", re.IGNORECASE)


def _stable_key(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _iter_text_fragments(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, Mapping):
        for key in ("text", "content", "stdout", "stderr", "result", "output", "message"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                yield nested.strip()
        for nested in value.values():
            yield from _iter_text_fragments(nested)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text_fragments(item)
        return
    text = str(value).strip()
    if text:
        yield text


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    fragments: list[str] = []
    seen: set[str] = set()
    for item in _iter_text_fragments(value):
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        fragments.append(cleaned)
    if fragments:
        return "\n".join(fragments).strip()
    if isinstance(value, Mapping):
        return json.dumps(value, default=str, sort_keys=True)
    return str(value).strip()


def _chunks(text: str, max_chars: int = 3000) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    cleaned = text.strip()
    for index, start in enumerate(range(0, len(cleaned), max_chars)):
        part = cleaned[start : start + max_chars].strip()
        if part:
            chunks.append({"chunk_index": index, "content_text": part, "metadata": {"char_start": start}})
    return chunks


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_]{3,}", text or "")}


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def promote_candidate_memory(runtime: MemoryRuntime, memory: Mapping[str, Any] | None, reason: str) -> dict[str, Any] | None:
    if not memory or not runtime.settings.auto_promote_memories:
        return None
    if str(memory.get("status") or "") != "candidate":
        return None
    confidence = float(memory.get("confidence") or runtime.settings.min_promotion_confidence)
    if confidence < runtime.settings.min_promotion_confidence:
        return None
    memory_id = memory.get("id")
    if not memory_id:
        return None
    return db.promote_memory(runtime.settings, str(memory_id), reason=reason)


def reinforce_similar_memory(runtime: MemoryRuntime, memory: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not memory or not runtime.settings.auto_reinforce_memories:
        return None
    memory_id = str(memory.get("id") or "")
    summary = str(memory.get("summary") or memory.get("title") or "").strip()
    kind = str(memory.get("kind") or "")
    if not memory_id or not summary:
        return None
    try:
        rows = db.find_similar_memories(
            runtime.settings,
            summary,
            limit=8,
            kinds=[kind] if kind else None,
            exclude_id=memory_id,
            min_similarity=0.35,
        )
    except Exception:
        return None
    best: Mapping[str, Any] | None = None
    best_score = 0.0
    for row in rows:
        if row.get("status") not in {"candidate", "active"}:
            continue
        score = float(row.get("similarity") or _similarity(summary, str(row.get("summary") or row.get("title") or "")))
        if score > best_score:
            best = row
            best_score = score
    if not best or best_score < 0.72:
        return None
    existing_id = str(best.get("id") or "")
    if not existing_id:
        return None
    try:
        db.add_memory_evidence(
            runtime.settings,
            {
                "memory_item_id": existing_id,
                "run_id": runtime.run_id,
                "run_message_id": runtime.last_message_ids.get("user") or runtime.last_message_ids.get("assistant"),
                "external_ref": runtime.run_key,
                "quote": enrich_fields(
                    runtime.settings,
                    [EnrichmentField("quote", "memory_evidence.quote", summary)],
                ).get("quote", plain_text(summary)),
                "support_score": min(1.0, best_score),
                "metadata": {"source": "auto_reinforce", "duplicate_memory_id": memory_id},
            },
        )
    except Exception:
        pass
    adjusted = db.adjust_memory_confidence(
        runtime.settings,
        existing_id,
        runtime.settings.memory_repeat_confidence_delta,
        reason="repeated_similar_observation",
        support_score=best_score,
    )
    if str(memory.get("status") or "") == "candidate":
        try:
            db.set_memory_status(runtime.settings, memory_id, "superseded", reason=f"duplicate_of:{existing_id}")
        except Exception:
            pass
    return adjusted


def upsert_subjects_from_turn(agent: Any, runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not runtime.settings.auto_subjects:
        return []
    text = " ".join(part for part in (latest_user_text(agent, kwargs), assistant_text(runtime, kwargs)) if part)
    if not text:
        return []
    candidates: dict[str, dict[str, Any]] = {}
    for match in PATH_PATTERN.findall(text):
        key = match.strip("`'\".,")
        candidates[f"path:{key.lower()}"] = {
            "subject_key": f"path:{key}",
            "subject_type": "path",
            "display_name": key,
            "attributes": {"source": "message_loop_end", "run_key": runtime.run_key},
        }
    for match in URL_PATTERN.findall(text):
        key = match.strip("`'\".,")
        candidates[f"url:{key.lower()}"] = {
            "subject_key": f"url:{key}",
            "subject_type": "url",
            "display_name": key,
            "attributes": {"source": "message_loop_end", "run_key": runtime.run_key},
        }
    for match in PROJECT_PATTERN.findall(text):
        key = match.strip("`'\".,")
        candidates[f"entity:{key.lower()}"] = {
            "subject_key": f"entity:{key.lower()}",
            "subject_type": "entity",
            "display_name": key,
            "attributes": {"source": "message_loop_end", "run_key": runtime.run_key},
        }
    rows: list[dict[str, Any]] = []
    for payload in list(candidates.values())[:25]:
        try:
            rows.append(db.upsert_subject(runtime.settings, payload))
        except Exception:
            continue
    return rows


def create_episodic_memory(agent: Any, runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    if not runtime.settings.auto_episodic_memories or not runtime.run_id:
        return None
    user_text = latest_user_text(agent, kwargs)
    response_text = assistant_text(runtime, kwargs)
    if not user_text and not response_text:
        return None
    raw_summary = user_text or response_text
    enriched = enrich_fields(
        runtime.settings,
        [
            EnrichmentField("title", "memory_items.title", raw_summary, "Return a short descriptive title."),
            EnrichmentField("summary", "memory_items.summary", raw_summary, "Return a compact factual episode summary."),
            EnrichmentField("body", "memory_items.body", "\n\n".join(part for part in (user_text, response_text) if part)),
            EnrichmentField("outcome", "episodic.outcome", response_text),
            EnrichmentField("quote", "memory_evidence.quote", user_text or response_text),
        ],
    )
    summary = enriched.get("summary", plain_text(raw_summary))
    title = enriched.get("title", summary)
    body = enriched.get("body", plain_text("\n\n".join(part for part in (user_text, response_text) if part)))
    outcome = enriched.get("outcome", plain_text(response_text)) if response_text else None
    quote = enriched.get("quote", plain_text(user_text or response_text))
    payload = {
        "kind": "episodic",
        "status": "candidate",
        "title": title,
        "summary": summary,
        "body": body,
        "facts": {
            "turn_index": runtime.turn_index,
            "thread_key": runtime.thread_key,
            "run_key": runtime.run_key,
        },
        "tags": ["agent-zero", "episodic", "lifecycle"],
        "importance": 0.45,
        "confidence": 0.75,
        "source_ref": runtime.run_key,
        "metadata": {
            "run_id": runtime.run_id,
            "run_key": runtime.run_key,
            "automation": "episodic_turn",
        },
        "episodic": {
            "conversation_ref": runtime.run_key,
            "outcome": outcome,
            "emotion": {},
        },
        "evidence": [
            {
                "run_id": runtime.run_id,
                "run_message_id": runtime.last_message_ids.get("user") or runtime.last_message_ids.get("assistant"),
                "external_ref": runtime.run_key,
                "quote": quote,
                "support_score": 1,
                "metadata": {"source": "message_loop_end", "automation": "episodic_turn"},
            }
        ],
    }
    return db.create_memory(runtime.settings, payload)


def upsert_knowledge_from_tool(runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    if not runtime.settings.auto_knowledge_from_tools:
        return None
    status = str(kwargs.get("status") or "succeeded").lower()
    if status not in {"succeeded", "success", "ok"}:
        return None
    tool_name = kwargs.get("tool_name") or kwargs.get("name") or kwargs.get("tool")
    if not tool_name:
        return None
    raw_output = (
        kwargs.get("output")
        or kwargs.get("result")
        or kwargs.get("stdout")
        or kwargs.get("response")
        or kwargs.get("message")
    )
    text = _plain_text(raw_output)
    min_chars = int(kwargs.get("knowledge_min_chars") or runtime.settings.min_tool_response_chars)
    if len(text) < min_chars:
        return None
    enriched = enrich_fields(
        runtime.settings,
        [
            EnrichmentField("title", "knowledge_documents.title", f"Tool output: {tool_name}", "Return a short document title."),
            EnrichmentField("content", "knowledge_documents.content_text", text, "Convert tool output to complete plain text."),
        ],
    )
    title = enriched.get("title", f"Tool output: {tool_name}")
    content = enriched.get("content", plain_text(text))
    key = f"tool:{tool_name}:{_stable_key(runtime.run_key, text[:1000])}"
    return db.upsert_knowledge_document(
        runtime.settings,
        {
            "document_key": key,
            "title": title,
            "content_text": content,
            "content_json": raw_output,
            "tags": ["agent-zero", "tool-output", str(tool_name)],
            "source": {
                "source_key": f"tool:{tool_name}",
                "source_type": "tool",
                "trust_level": 60,
                "metadata": {"run_id": runtime.run_id, "run_key": runtime.run_key},
            },
            "metadata": {
                "run_id": runtime.run_id,
                "run_key": runtime.run_key,
                "tool_name": str(tool_name),
                "automation": "tool_output_knowledge",
                "trigger_min_chars": min_chars,
                "captured_chars": len(content),
            },
            "chunks": _chunks(content),
        },
    )

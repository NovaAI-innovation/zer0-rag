from __future__ import annotations

from typing import Any, Mapping

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.enrichment import EnrichmentField, enrich_fields, plain_text
from usr.plugins.memory_knowledge.helpers.recorder import assistant_text, latest_user_text
from usr.plugins.memory_knowledge.helpers.runtime import MemoryRuntime


DEFAULT_CUES = (
    "remember",
    "prefer",
    "preference",
    "always",
    "never",
    "this project",
    "this repo",
    "workflow",
    "procedure",
    "convention",
)


def _classify(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("prefer", "preference", "like to")):
        return "preference"
    if any(word in lowered for word in ("workflow", "procedure", "step", "process")):
        return "procedural"
    if any(word in lowered for word in ("this project", "this repo", "codebase")):
        return "semantic"
    return "semantic"


def extract_candidate(agent: Any, runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    if not runtime.settings.extract_memories:
        return None
    user_text = latest_user_text(agent, kwargs)
    response_text = assistant_text(runtime, kwargs)
    combined = " ".join(part for part in (user_text, response_text) if part).strip()
    if not combined:
        return None
    cues = runtime.settings.memory_cues or DEFAULT_CUES
    lowered = combined.lower()
    if not any(cue in lowered for cue in cues):
        return None
    raw_summary = combined.strip()
    enriched = enrich_fields(
        runtime.settings,
        [
            EnrichmentField("title", "memory_items.title", raw_summary, "Return a short descriptive title."),
            EnrichmentField("summary", "memory_items.summary", raw_summary, "Return a compact factual memory summary."),
            EnrichmentField("body", "memory_items.body", combined, "Return detailed plain text without JSON syntax."),
            EnrichmentField("quote", "memory_evidence.quote", user_text or raw_summary, "Return the most relevant supporting quote or observation."),
        ],
    )
    summary = enriched.get("summary", plain_text(raw_summary))
    title = enriched.get("title", summary)
    body = enriched.get("body", plain_text(combined))
    quote = enriched.get("quote", plain_text(user_text or summary))
    confidence = max(0.0, min(1.0, runtime.settings.min_memory_confidence))
    return {
        "kind": _classify(summary),
        "status": "candidate",
        "title": title,
        "summary": summary,
        "body": body,
        "facts": {"deterministic_extraction": True},
        "tags": ["agent-zero", "lifecycle", "deterministic"],
        "importance": 0.6,
        "confidence": confidence,
        "source_ref": runtime.run_key,
        "metadata": {
            "run_id": runtime.run_id,
            "run_key": runtime.run_key,
            "thread_key": runtime.thread_key,
            "extractor": "cue_match",
        },
        "semantic": {"concept_key": "agent-zero.lifecycle", "statement": summary},
        "evidence": [
            {
                "run_id": runtime.run_id,
                "run_message_id": runtime.last_message_ids.get("user") or runtime.last_message_ids.get("assistant"),
                "external_ref": runtime.run_key,
                "quote": quote,
                "support_score": 1,
                "metadata": {"source": "message_loop_end"},
            }
        ],
    }


def write_candidate(agent: Any, runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = extract_candidate(agent, runtime, kwargs)
    if not payload:
        return None
    return db.create_memory(runtime.settings, payload)

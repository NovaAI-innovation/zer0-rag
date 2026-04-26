from __future__ import annotations

from typing import Any

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import MemoryRuntime


MEMORY_CUES = (
    "remember",
    "preference",
    "prefer",
    "always",
    "never",
    "the project",
    "we use",
    "this repo",
    "workflow",
    "procedure",
)


def _candidate_summary(user_text: str, assistant_text: str) -> str | None:
    combined = " ".join(part for part in (user_text, assistant_text) if part).strip()
    lowered = combined.lower()
    if not combined or not any(cue in lowered for cue in MEMORY_CUES):
        return None
    return combined[:500]


async def extract_candidates(agent: Any, runtime: MemoryRuntime, conversation: dict[str, Any] | None, kwargs: dict[str, Any]) -> list[str]:
    if not runtime.settings.extract_candidates or not conversation:
        return []

    user_text = str(kwargs.get("message") or kwargs.get("user_message") or kwargs.get("prompt") or "")
    assistant_text = "".join(runtime.response_chunks)
    summary = _candidate_summary(user_text, assistant_text)
    if not summary:
        return []

    payload = {
        "kind": "semantic",
        "status": "candidate",
        "visibility": "tenant",
        "title": summary[:80],
        "summary": summary,
        "facts": {"source": "agent_zero_loop"},
        "tags": ["agent-zero", "autonomous"],
        "importance": 0.6,
        "confidence": 0.7,
        "metadata": {
            "plugin": "memory_knowledge",
            "conversation_id": conversation.get("conversation_id"),
            "autonomous": True,
        },
        "details": {
            "concept_key": "agent-zero.autonomous-memory",
            "statement": summary,
        },
        "evidence": [
            {
                "evidence_kind": "conversation",
                "external_ref": conversation.get("conversation_id"),
                "quote": user_text[:500] or summary[:500],
                "support_score": 1,
            }
        ],
    }
    try:
        return [db.create_memory_item(runtime.settings, payload)]
    except Exception as exc:
        db.log_diagnostic(runtime.settings, "error", "memory", "candidate_extract_failed", str(exc))
        return []

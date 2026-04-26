from __future__ import annotations

from typing import Any

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import MemoryRuntime


def begin_turn(agent: Any, runtime: MemoryRuntime, kwargs: dict[str, Any]) -> None:
    runtime.response_chunks.clear()
    runtime.external_thread_id = (
        str(kwargs.get("conversation_id") or kwargs.get("thread_id") or kwargs.get("chat_id") or getattr(agent, "id", "agent-zero"))
    )
    try:
        runtime.trace_run_id = db.start_trace(
            runtime.settings,
            "conversation.turn",
            str(runtime.context.get("agent_profile_id")) if runtime.context else None,
        )
    except Exception:
        runtime.trace_run_id = None


def capture_response_chunk(runtime: MemoryRuntime, kwargs: dict[str, Any]) -> None:
    for key in ("chunk", "content", "delta", "text"):
        value = kwargs.get(key)
        if isinstance(value, str) and value:
            runtime.response_chunks.append(value)
            return


def _text_from_kwargs(kwargs: dict[str, Any]) -> str:
    for key in ("message", "user_message", "prompt", "input", "content"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _assistant_text(runtime: MemoryRuntime, kwargs: dict[str, Any]) -> str:
    for key in ("response", "assistant_message", "output", "final_response"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "".join(runtime.response_chunks).strip()


async def record_turn(agent: Any, runtime: MemoryRuntime, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    user_text = _text_from_kwargs(kwargs)
    assistant_text = _assistant_text(runtime, kwargs)
    if not user_text and not assistant_text:
        return None

    messages: list[dict[str, Any]] = []
    if user_text:
        messages.append({"role": "user", "ordinal": int(kwargs.get("user_ordinal") or 1), "content_text": user_text})
    if assistant_text:
        messages.append({"role": "assistant", "ordinal": int(kwargs.get("assistant_ordinal") or 2), "content_text": assistant_text})

    try:
        result = db.record_conversation(
            runtime.settings,
            str(runtime.context.get("agent_profile_id")) if runtime.context else None,
            runtime.external_thread_id or str(getattr(agent, "id", "agent-zero")),
            messages,
            title=str(kwargs.get("title")) if kwargs.get("title") else None,
        )
        db.upsert_working_memory(
            runtime.settings,
            str(runtime.context.get("agent_profile_id")) if runtime.context else None,
            "last_turn",
            {"user": user_text, "assistant": assistant_text[:1000]},
            priority=50,
            conversation_id=result.get("conversation_id"),
        )
        return result
    except Exception as exc:
        db.log_diagnostic(runtime.settings, "error", "conversation", "record_turn_failed", str(exc))
        return None
    finally:
        if runtime.trace_run_id:
            try:
                db.finish_trace(runtime.settings, runtime.trace_run_id, "succeeded", "Turn processing finished")
            except Exception:
                pass

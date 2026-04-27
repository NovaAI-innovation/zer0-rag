from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.enrichment import EnrichmentField, enrich_fields, enrich_value, plain_text
from usr.plugins.memory_knowledge.helpers.runtime import MemoryRuntime


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _content_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        decoded = _decode_json_text(value)
        return decoded if decoded is not None else None
    if isinstance(value, Mapping):
        return _jsonable(value)
    if isinstance(value, (list, tuple)):
        return _jsonable(value)
    return None


def _decode_json_text(value: str) -> Any:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _looks_like_response_envelope(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith('{"thoughts"') or stripped.startswith("{\n    \"thoughts\"") or stripped.startswith("{\r\n    \"thoughts\"")


def _partial_response_envelope(value: Any) -> str:
    if isinstance(value, str) and _looks_like_response_envelope(value) and _decode_json_text(value) is None:
        return value
    if isinstance(value, Mapping):
        for key in ("chunk", "content", "delta", "text", "response"):
            nested = _partial_response_envelope(value.get(key))
            if nested:
                return nested
    return ""


def _quoted_strings(value: str) -> list[str]:
    strings: list[str] = []
    for match in re.finditer(r'"((?:\\.|[^"\\])*)"', value, flags=re.DOTALL):
        raw = match.group(0)
        try:
            strings.append(json.loads(raw))
        except Exception:
            continue
    return strings


def _dedupe_prefixes(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        text = _plain_text(value)
        if len(text) < 12 or text in {"thoughts", "headline", "tool_name", "tool_args", "text", "response"}:
            continue
        if _looks_like_response_envelope(text):
            continue
        cleaned.append(text)
    result = []
    for text in cleaned:
        if any(other != text and other.startswith(text) for other in cleaned):
            continue
        if text not in result:
            result.append(text)
    return result[:20]


def _partial_envelope_thoughts(value: str) -> list[str]:
    if not _looks_like_response_envelope(value):
        return []
    before_tool_args = value.split('"tool_args"', 1)[0]
    return _dedupe_prefixes(_quoted_strings(before_tool_args))


def _tool_args_text(value: Mapping[str, Any]) -> str:
    tool_args = value.get("tool_args") or value.get("args") or value.get("arguments")
    if isinstance(tool_args, Mapping):
        for key in ("text", "content", "message", "response", "output"):
            text = _content_text(tool_args.get(key))
            if text:
                return text
    return ""


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return _content_text(value).strip()
    text = value.strip()
    if not text:
        return ""
    decoded = _decode_json_text(text)
    if decoded is not None:
        content = _content_text(decoded).strip()
        if content:
            return content
        thoughts = _thought_texts(decoded)
        if thoughts:
            return "\n".join(_plain_text(item) for item in thoughts if _plain_text(item)).strip()
        return "" if _looks_like_response_envelope(text) else text
    if _looks_like_response_envelope(text):
        return ""
    return text


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        decoded = _decode_json_text(value)
        if decoded is not None:
            text = _content_text(decoded)
            if text:
                return text
            if _thought_texts(decoded):
                return ""
            if _looks_like_response_envelope(value):
                return ""
            return value.strip()
        if _looks_like_response_envelope(value):
            return ""
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("content", "text", "message", "response", "output", "final_response"):
            if key in value:
                text = _content_text(value.get(key))
                if text:
                    return text
        text = _tool_args_text(value)
        if text:
            return text
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_content_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    return str(value).strip()


def _message_role(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    role = value.get("role") or value.get("type") or value.get("author")
    if isinstance(role, Mapping):
        role = role.get("role") or role.get("name")
    return str(role or "").lower()


def _history_items(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("messages", "history", "items"):
            items = _history_items(value.get(key))
            if items:
                return items
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _iter_histories(agent: Any, kwargs: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    histories: list[Mapping[str, Any]] = []
    for key in ("history", "messages", "conversation", "chat_history"):
        histories.extend(_history_items(kwargs.get(key)))
    for attr in ("history", "messages", "conversation", "chat_history"):
        histories.extend(_history_items(getattr(agent, attr, None)))
    return histories


def _thought_texts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        decoded = _decode_json_text(value)
        if decoded is not None:
            return _thought_texts(decoded)
        if _looks_like_response_envelope(value):
            return _partial_envelope_thoughts(value)
        return []
    if isinstance(value, Mapping):
        texts: list[str] = []
        for key in ("thought", "thoughts", "reasoning", "reasoning_summary", "monologue"):
            if key not in value:
                continue
            thought_value = value.get(key)
            if isinstance(thought_value, (list, tuple)):
                for item in thought_value:
                    nested = _thought_texts(item)
                    if nested:
                        texts.extend(nested)
                    else:
                        text = _content_text(item)
                        if text:
                            texts.append(text)
            else:
                text = _content_text(thought_value)
                if text:
                    texts.append(text)
        return texts
    if isinstance(value, (list, tuple)):
        texts = []
        for item in value:
            texts.extend(_thought_texts(item))
        return texts
    return []


def _response_parts(value: Any) -> tuple[str, list[str]]:
    thoughts = _thought_texts(value)
    text = _content_text(value)
    if text and thoughts and text in thoughts:
        text = ""
    return _plain_text(text), [_plain_text(thought) for thought in thoughts if _plain_text(thought)]


def _record_response_thoughts(runtime: MemoryRuntime, thoughts: list[str]) -> None:
    if not runtime.settings.record_thoughts:
        return
    for text in thoughts:
        cleaned = _plain_text(text)
        if not cleaned:
            continue
        cleaned = enrich_value(runtime.settings, "response_thought", "run_thoughts.content_text", cleaned)
        thought_key = _stable_key("response", cleaned)
        if thought_key in runtime.recorded_thought_keys:
            continue
        if not runtime.run_id:
            runtime.pending_response_thoughts.append(cleaned)
            runtime.recorded_thought_keys.add(thought_key)
            continue
        runtime.recorded_thought_keys.add(thought_key)
        runtime.thought_index += 1
        db.log_run_thought(
            runtime.settings,
            {
                "run_id": runtime.run_id,
                "sequence_number": runtime.thought_index,
                "thought_type": "response",
                "content_text": cleaned,
                "visibility": "internal",
                "metadata": {"source": "response_stream"},
            },
        )


def flush_response_thoughts(runtime: MemoryRuntime) -> None:
    if runtime.pending_response_envelope:
        _record_response_thoughts(runtime, _thought_texts(runtime.pending_response_envelope))
        runtime.pending_response_envelope = ""
    if not runtime.run_id or not runtime.settings.record_thoughts:
        return
    pending = runtime.pending_response_thoughts
    runtime.pending_response_thoughts = []
    for text in pending:
        cleaned = _plain_text(text)
        if not cleaned:
            continue
        cleaned = enrich_value(runtime.settings, "pending_response_thought", "run_thoughts.content_text", cleaned)
        runtime.thought_index += 1
        db.log_run_thought(
            runtime.settings,
            {
                "run_id": runtime.run_id,
                "sequence_number": runtime.thought_index,
                "thought_type": "response",
                "content_text": cleaned,
                "visibility": "internal",
                "metadata": {"source": "response_stream"},
            },
        )


def _stable_key(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _agent_name(agent: Any) -> str:
    for attr in ("name", "agent_name", "profile", "profile_name"):
        value = getattr(agent, attr, None)
        if value:
            return str(value)
    return "Agent Zero"


def _model_name(agent: Any, kwargs: Mapping[str, Any]) -> str | None:
    for key in ("model", "model_name"):
        if kwargs.get(key):
            return str(kwargs[key])
    for attr in ("model", "model_name"):
        value = getattr(agent, attr, None)
        if value:
            return str(value)
    return None


def text_from_kwargs(kwargs: Mapping[str, Any], *extra_keys: str) -> str:
    for key in (*extra_keys, "message", "user_message", "prompt", "input", "content", "text"):
        value = kwargs.get(key)
        text = _content_text(value)
        if text:
            return text
    return ""


def latest_user_text(agent: Any, kwargs: Mapping[str, Any]) -> str:
    for key in ("user_message", "user_input", "user_prompt", "prompt", "input"):
        text = _content_text(kwargs.get(key))
        if text:
            return text
    message = kwargs.get("message")
    if isinstance(message, Mapping) and _message_role(message) == "user":
        text = _content_text(message)
        if text:
            return text
    for item in reversed(_iter_histories(agent, kwargs)):
        if _message_role(item) == "user":
            text = _content_text(item)
            if text:
                return text
    if not any(key in kwargs for key in ("response", "assistant_message", "output", "final_response")):
        direct = text_from_kwargs(kwargs)
        if direct:
            return direct
    return ""


def assistant_text(runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> str:
    for key in ("response", "assistant_message", "output", "final_response", "message"):
        if key in kwargs:
            if key == "message" and isinstance(kwargs.get(key), Mapping) and _message_role(kwargs.get(key)) not in {"assistant", ""}:
                continue
            text, thoughts = _response_parts(kwargs.get(key))
            _record_response_thoughts(runtime, thoughts)
            if text:
                return text
    for item in reversed(_history_items(kwargs.get("messages"))):
        if _message_role(item) == "assistant":
            text, thoughts = _response_parts(item)
            _record_response_thoughts(runtime, thoughts)
            if text:
                return text
    direct = text_from_kwargs(kwargs, "response", "assistant_message", "output", "final_response")
    return direct or "".join(runtime.response_chunks).strip()


def _assistant_content_json(kwargs: Mapping[str, Any]) -> Any:
    for key in ("response", "assistant_message", "output", "final_response", "message"):
        value = kwargs.get(key)
        if key == "message" and isinstance(value, Mapping) and _message_role(value) not in {"assistant", ""}:
            continue
        payload = _content_json(value)
        if payload is not None:
            return payload
    return None


def _user_content_json(kwargs: Mapping[str, Any]) -> Any:
    for key in ("user_message", "user_input", "user_prompt", "prompt", "input"):
        payload = _content_json(kwargs.get(key))
        if payload is not None:
            return payload
    message = kwargs.get("message")
    if isinstance(message, Mapping) and _message_role(message) == "user":
        payload = _content_json(message)
        if payload is not None:
            return payload
    return None


def begin_turn(agent: Any, runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> None:
    runtime.turn_index += 1
    runtime.started_at = datetime.now(timezone.utc)
    runtime.response_chunks.clear()
    if not runtime.pending_response_thoughts:
        runtime.recorded_thought_keys.clear()
    runtime.last_message_ids.clear()
    runtime.injected_memory_ids.clear()
    runtime.injected_memory_block = ""
    runtime.step_index = 0
    runtime.thought_index = 0
    runtime.thread_key = str(
        kwargs.get("thread_id")
        or kwargs.get("conversation_id")
        or kwargs.get("chat_id")
        or getattr(agent, "context_id", None)
        or getattr(agent, "id", None)
        or "agent-zero"
    )
    user_text = latest_user_text(agent, kwargs)
    runtime.run_key = str(kwargs.get("run_key") or f"{runtime.thread_key}:{runtime.turn_index}:{_stable_key(user_text)}")
    if not runtime.settings.record_run_history:
        return
    result = db.log_run(
        runtime.settings,
        {
            "run_key": runtime.run_key,
            "agent_name": _agent_name(agent),
            "model": _model_name(agent, kwargs),
            "operation": "message_loop.turn",
            "status": "running",
            "input_text": user_text or None,
            "input_payload": {
                "thread_key": runtime.thread_key,
                "turn_index": runtime.turn_index,
                "hook": "message_loop_start",
            },
            "metadata": {"deterministic": True, "source": "memory_knowledge"},
        },
    )
    runtime.run_id = str(result["id"])
    flush_response_thoughts(runtime)


def capture_response_chunk(runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> None:
    for key in ("chunk", "content", "delta", "text", "response"):
        value = kwargs.get(key)
        partial = _partial_response_envelope(value)
        if partial:
            runtime.pending_response_envelope = partial
            return
        text, thoughts = _response_parts(value)
        _record_response_thoughts(runtime, thoughts)
        if text:
            runtime.response_chunks.append(text)
            return


def record_step(runtime: MemoryRuntime, name: str, step_type: str, kwargs: Mapping[str, Any], status: str = "succeeded") -> None:
    if not runtime.run_id or not runtime.settings.record_steps:
        return
    runtime.step_index += 1
    sequence = int(kwargs.get("sequence_number") or kwargs.get("step_index") or runtime.step_index)
    db.log_run_step(
        runtime.settings,
        {
            "run_id": runtime.run_id,
            "sequence_number": sequence,
            "step_type": step_type,
            "name": name,
            "status": status,
            "input_payload": _jsonable(dict(kwargs)),
            "metadata": {"deterministic": True, "source": "memory_knowledge"},
        },
    )


def record_tool_call(runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> None:
    if not runtime.run_id or not runtime.settings.record_tool_calls:
        return
    tool_name = kwargs.get("tool_name") or kwargs.get("name") or kwargs.get("tool")
    if not tool_name:
        return
    output_value = kwargs.get("output") or kwargs.get("result")
    enriched = enrich_fields(
        runtime.settings,
        [
            EnrichmentField("stdout", "tool_executions.stdout_text", kwargs.get("stdout") or output_value),
            EnrichmentField("stderr", "tool_executions.stderr_text", kwargs.get("stderr")),
        ],
    )
    db.log_tool_execution(
        runtime.settings,
        {
            "run_id": runtime.run_id,
            "tool_call_id": kwargs.get("tool_call_id") or kwargs.get("call_id"),
            "tool_name": str(tool_name),
            "input_payload": kwargs.get("input") or kwargs.get("args") or kwargs.get("arguments") or {},
            "output_payload": output_value,
            "stdout_text": enriched.get("stdout"),
            "stderr_text": enriched.get("stderr"),
            "status": str(kwargs.get("status") or "succeeded"),
            "duration_ms": kwargs.get("duration_ms"),
            "error_code": kwargs.get("error_code"),
            "error_message": kwargs.get("error") or kwargs.get("error_message"),
            "metadata": {"deterministic": True, "hook": "tool"},
        },
    )


def record_thought(runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> None:
    if not runtime.run_id or not runtime.settings.record_thoughts:
        return
    thought_texts = _thought_texts(kwargs)
    content = "\n".join(_plain_text(text) for text in thought_texts if _plain_text(text)).strip()
    content = content or _plain_text(text_from_kwargs(kwargs, "thought", "thoughts", "monologue", "reasoning", "summary"))
    if not content:
        return
    content = enrich_value(runtime.settings, "thought", "run_thoughts.content_text", content)
    thought_key = _stable_key("monologue", content)
    if thought_key in runtime.recorded_thought_keys:
        return
    runtime.recorded_thought_keys.add(thought_key)
    runtime.thought_index += 1
    db.log_run_thought(
        runtime.settings,
        {
            "run_id": runtime.run_id,
            "sequence_number": int(kwargs.get("sequence_number") or kwargs.get("thought_index") or runtime.thought_index),
            "thought_type": str(kwargs.get("thought_type") or "summary"),
            "content_text": content,
            "content_json": _content_json(
                kwargs.get("thought")
                or kwargs.get("thoughts")
                or kwargs.get("monologue")
                or kwargs.get("reasoning")
                or kwargs.get("summary")
            ),
            "visibility": str(kwargs.get("visibility") or "internal"),
        },
    )


def finish_turn(agent: Any, runtime: MemoryRuntime, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    user_text = _plain_text(latest_user_text(agent, kwargs))
    response_text = _plain_text(assistant_text(runtime, kwargs))
    user_content_json = _user_content_json(kwargs)
    assistant_content_json = _assistant_content_json(kwargs)
    enriched = enrich_fields(
        runtime.settings,
        [
            EnrichmentField("user", "run_messages.content_text", user_text, "Keep the user's request in first-person plain text."),
            EnrichmentField("assistant", "run_messages.content_text", response_text, "Keep the assistant result as concise plain text."),
            EnrichmentField("memory_block", "run_messages.content_text", runtime.injected_memory_block),
        ],
    )
    user_text = enriched.get("user", plain_text(user_text))
    response_text = enriched.get("assistant", plain_text(response_text))
    injected_memory_text = enriched.get("memory_block", plain_text(runtime.injected_memory_block))
    flush_response_thoughts(runtime)
    result: dict[str, Any] = {"run_id": runtime.run_id, "messages": 0}
    runtime.last_message_ids.clear()
    if runtime.run_id and runtime.settings.record_messages:
        ordinal = 1
        if user_text:
            row = db.log_run_message(
                runtime.settings,
                {
                    "run_id": runtime.run_id,
                    "role": "user",
                    "ordinal": ordinal,
                    "content_text": user_text,
                    "content_json": user_content_json,
                    "metadata": {"deterministic": True},
                },
            )
            if row.get("id"):
                runtime.last_message_ids["user"] = str(row["id"])
            result["messages"] += 1
            ordinal += 1
        if runtime.injected_memory_block:
            row = db.log_run_message(
                runtime.settings,
                {
                    "run_id": runtime.run_id,
                    "role": "system",
                    "ordinal": ordinal,
                    "content_text": injected_memory_text,
                    "metadata": {"type": "injected_memory", "memory_ids": runtime.injected_memory_ids},
                },
            )
            if row.get("id"):
                runtime.last_message_ids["system"] = str(row["id"])
            result["messages"] += 1
            ordinal += 1
        if response_text:
            row = db.log_run_message(
                runtime.settings,
                {
                    "run_id": runtime.run_id,
                    "role": "assistant",
                    "ordinal": ordinal,
                    "content_text": response_text,
                    "content_json": assistant_content_json,
                    "metadata": {"deterministic": True},
                },
            )
            if row.get("id"):
                runtime.last_message_ids["assistant"] = str(row["id"])
            result["messages"] += 1
    if runtime.run_key and runtime.settings.record_run_history:
        elapsed = int((datetime.now(timezone.utc) - runtime.started_at).total_seconds() * 1000)
        updated = db.log_run(
            runtime.settings,
            {
                "run_key": runtime.run_key,
                "operation": "message_loop.turn",
                "status": "succeeded",
                "response_text": response_text or None,
                "duration_ms": elapsed,
                "metadata": {
                    "deterministic": True,
                    "source": "memory_knowledge",
                    "messages_recorded": result["messages"],
                },
            },
        )
        runtime.run_id = str(updated["id"])
        result["run"] = updated
    return result

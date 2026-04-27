from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from usr.plugins.memory_knowledge.helpers import db


JSON_OUTPUT_FIELD_NAMES = {
    "facts",
    "metadata",
    "source",
    "episodic",
    "semantic",
    "procedural",
    "input_payload",
    "output_payload",
    "response_payload",
    "response_output",
    "thoughts_payload",
    "content_json",
}


@dataclass(frozen=True)
class EnrichmentField:
    key: str
    field: str
    value: Any
    instruction: str = ""


def plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            decoded = json.loads(stripped)
        except Exception:
            return stripped
        extracted = plain_text(decoded)
        return extracted or stripped
    if isinstance(value, Mapping):
        for key in ("content", "text", "message", "response", "output", "final_response", "summary", "title"):
            if key in value:
                text = plain_text(value.get(key))
                if text:
                    return text
        lines = []
        for key, item in value.items():
            text = plain_text(item)
            if text:
                lines.append(f"{key}: {text}")
        return "\n".join(lines).strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(part for part in (plain_text(item) for item in value) if part).strip()
    return str(value).strip()


def complete_json(value: Any) -> Any:
    return value


def _enabled(settings: db.Settings) -> bool:
    return (
        settings.llm_enrichment_enabled
        and settings.llm_enrichment_provider.lower() == "xai"
        and bool(settings.llm_enrichment_api_key)
    )


def _chunks(items: list[EnrichmentField], size: int) -> Iterable[list[EnrichmentField]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def enrich_fields(settings: db.Settings, fields: Iterable[EnrichmentField]) -> dict[str, str]:
    items = [item for item in fields if plain_text(item.value)]
    if not items:
        return {}
    fallback = {item.key: plain_text(item.value) for item in items}
    if not _enabled(settings):
        return fallback

    enriched: dict[str, str] = {}
    for batch in _chunks(items, settings.llm_enrichment_batch_size):
        enriched.update(_enrich_batch(settings, batch))
    return {**fallback, **{key: value for key, value in enriched.items() if value.strip()}}


def enrich_value(settings: db.Settings, key: str, field: str, value: Any, instruction: str = "") -> str:
    return enrich_fields(settings, [EnrichmentField(key, field, value, instruction)]).get(key, plain_text(value))


def _enrich_batch(settings: db.Settings, fields: list[EnrichmentField]) -> dict[str, str]:
    payload = {
        "model": settings.llm_enrichment_model,
        "stream": False,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Rewrite database field values into concise, accurate plain text for retrieval and auditing. "
                    "Preserve all concrete facts, IDs, file paths, commands, errors, and outcomes. "
                    "Do not invent details. Return only JSON with an 'items' object. "
                    "Each key in 'items' must match the input key and each value must be a string."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "fields": [
                            {
                                "key": item.key,
                                "field": item.field,
                                "instruction": item.instruction,
                                "value": plain_text(item.value),
                            }
                            for item in fields
                        ]
                    },
                    ensure_ascii=True,
                    default=str,
                ),
            },
        ],
    }
    request = Request(
        f"{settings.llm_enrichment_base_url}/chat/completions",
        data=json.dumps(payload, default=str).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.llm_enrichment_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.llm_enrichment_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {}

    content = (
        ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
        if isinstance(body, Mapping)
        else None
    )
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}
    items = parsed.get("items") if isinstance(parsed, Mapping) else None
    if not isinstance(items, Mapping):
        return {}
    return {str(key): plain_text(value) for key, value in items.items()}

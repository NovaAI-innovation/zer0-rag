from __future__ import annotations

import json
import os
import re
import math
import hashlib
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, unquote, urlparse, urlunparse
from urllib.request import Request, urlopen
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row


MEMORY_KINDS = {
    "semantic",
    "episodic",
    "procedural",
    "preference",
    "profile",
    "working",
    "knowledge",
}
RECORD_STATUSES = {"candidate", "active", "superseded", "rejected", "archived"}
RUN_STATUSES = {"running", "succeeded", "failed", "cancelled"}
MESSAGE_ROLES = {"system", "developer", "user", "assistant", "tool"}

READ_TABLES = {
    "tenants": "memory.tenants",
    "actors": "memory.actors",
    "subjects": "memory.subjects",
    "memory_items": "memory.memory_items",
    "semantic_memories": "memory.semantic_memories",
    "episodic_memories": "memory.episodic_memories",
    "knowledge_sources": "memory.knowledge_sources",
    "knowledge_documents": "memory.knowledge_documents",
    "knowledge_chunks": "memory.knowledge_chunks",
    "run_history": "memory.run_history",
    "run_messages": "memory.run_messages",
    "run_steps": "memory.run_steps",
    "run_thoughts": "memory.run_thoughts",
    "tool_executions": "memory.tool_executions",
    "memory_evidence": "memory.memory_evidence",
}

TENANT_TABLES = {
    name
    for name in READ_TABLES
    if name not in {"tenants"}
}

TABLE_COLUMNS = {
    "tenants": {"id", "slug", "display_name", "created_at", "updated_at", "archived_at"},
    "actors": {"id", "tenant_id", "kind", "external_ref", "display_name", "created_at", "updated_at"},
    "subjects": {"id", "tenant_id", "subject_key", "subject_type", "display_name", "created_at", "updated_at"},
    "memory_items": {"id", "tenant_id", "subject_id", "kind", "status", "title", "summary", "tags", "importance", "confidence", "updated_at", "created_at"},
    "semantic_memories": {"memory_item_id", "tenant_id", "concept_key", "statement", "created_at"},
    "episodic_memories": {"memory_item_id", "tenant_id", "happened_at", "conversation_ref", "location", "outcome", "created_at"},
    "knowledge_sources": {"id", "tenant_id", "source_key", "source_type", "uri", "trust_level", "created_at", "updated_at"},
    "knowledge_documents": {"id", "tenant_id", "source_id", "document_key", "title", "uri", "status", "created_at", "updated_at"},
    "knowledge_chunks": {"id", "tenant_id", "document_id", "chunk_index", "heading", "content_text", "tags", "created_at", "updated_at"},
    "run_history": {"id", "tenant_id", "run_key", "agent_name", "model", "operation", "status", "started_at", "ended_at", "duration_ms"},
    "run_messages": {"id", "tenant_id", "run_id", "role", "ordinal", "content_text", "created_at"},
    "run_steps": {"id", "tenant_id", "run_id", "sequence_number", "step_type", "name", "status", "created_at"},
    "run_thoughts": {"id", "tenant_id", "run_id", "step_id", "sequence_number", "thought_type", "visibility", "created_at"},
    "tool_executions": {"id", "tenant_id", "run_id", "tool_name", "status", "started_at", "ended_at", "duration_ms"},
    "memory_evidence": {"id", "tenant_id", "memory_item_id", "run_id", "run_message_id", "tool_execution_id", "knowledge_chunk_id", "external_ref", "support_score", "created_at"},
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    database_url: str | None
    supabase_url: str | None
    service_role_key: str | None
    tenant_id: str | None
    tenant_slug: str
    tenant_display_name: str
    agent_key: str
    agent_display_name: str
    writes_enabled: bool
    auto_create_tenant: bool
    auto_create_subjects: bool
    default_status: str
    default_limit: int
    max_limit: int
    include_inactive: bool
    max_body_chars: int
    max_document_chars: int
    lifecycle_enabled: bool
    record_run_history: bool
    record_messages: bool
    record_steps: bool
    record_thoughts: bool
    record_tool_calls: bool
    inject_context: bool
    extract_memories: bool
    auto_episodic_memories: bool
    auto_promote_memories: bool
    auto_reinforce_memories: bool
    auto_subjects: bool
    auto_knowledge_from_tools: bool
    max_context_chars: int
    min_memory_confidence: float
    min_promotion_confidence: float
    memory_access_confidence_delta: float
    memory_evidence_confidence_delta: float
    memory_repeat_confidence_delta: float
    memory_correction_confidence_delta: float
    memory_access_min_interval_minutes: int
    min_knowledge_chars: int
    min_tool_response_chars: int
    memory_cues: tuple[str, ...]
    llm_enrichment_enabled: bool
    llm_enrichment_provider: str
    llm_enrichment_base_url: str
    llm_enrichment_api_key: str | None
    llm_enrichment_model: str
    llm_enrichment_timeout_seconds: int
    llm_enrichment_batch_size: int
    llm_subject_enrichment_enabled: bool
    llm_subject_enrichment_model: str
    llm_subject_enrichment_timeout_seconds: int
    llm_subject_enrichment_max_tags: int
    embeddings_enabled: bool
    embeddings_model: str
    embeddings_timeout_seconds: int
    embeddings_dimensions: int
    embeddings_hash_fallback: bool


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _omit_none(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _cfg(config: Mapping[str, Any] | None, *path: str, default: Any = None) -> Any:
    current: Any = config or {}
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _env(config: Mapping[str, Any] | None, *path: str, default_name: str) -> str | None:
    env_name = _cfg(config, *path)
    return os.environ.get(str(env_name or default_name))


def load_settings(config: Mapping[str, Any] | None = None) -> Settings:
    _load_dotenv()
    default_auth_mode = "data_api" if os.environ.get("SUPABASE_SERVICE_ROLE_KEY") else "postgres"
    auth_mode = str(_cfg(config, "auth", "mode", default=_cfg(config, "database", "auth_mode", default=default_auth_mode))).lower()
    project_ref = str(_cfg(config, "database", "project_ref", default=os.environ.get("SUPABASE_PROJECT_REF") or "ufgbtyrdwngayrrutfdz"))
    supabase_url = (
        _cfg(config, "supabase", "url")
        or _cfg(config, "data_api", "url")
        or os.environ.get(str(_cfg(config, "supabase", "url_env", default="SUPABASE_URL")))
        or (f"https://{project_ref}.supabase.co" if auth_mode in {"rest", "data_api", "postgrest", "supabase"} else None)
    )
    service_role_key = (
        _cfg(config, "supabase", "service_role_key")
        or _cfg(config, "data_api", "service_role_key")
        or os.environ.get(str(_cfg(config, "supabase", "service_role_key_env", default="SUPABASE_SERVICE_ROLE_KEY")))
    )
    if auth_mode in {"rest", "data_api", "postgrest", "supabase"}:
        auth_mode = "data_api"
    elif auth_mode not in {"postgres", "data_api"}:
        raise ConfigError("auth.mode must be 'postgres' or 'data_api'.")

    direct_url = _cfg(config, "database", "url")
    db_env = str(_cfg(config, "database", "url_env", default="MEMORY_DATABASE_URL"))
    fallback_envs = _cfg(config, "database", "fallback_url_env", default=["SUPABASE_DB_URL", "DATABASE_URL"])
    database_url = str(direct_url).strip() if direct_url else None
    if not database_url and db_env.startswith(("postgresql://", "postgres://")):
        database_url = db_env
    database_url = database_url or os.environ.get(db_env)
    for env_name in fallback_envs or []:
        database_url = database_url or os.environ.get(str(env_name))
    if not database_url and auth_mode == "postgres":
        raise ConfigError("Set MEMORY_DATABASE_URL, SUPABASE_DB_URL, or DATABASE_URL.")
    if auth_mode == "data_api" and (not supabase_url or not service_role_key):
        raise ConfigError("Set supabase.url and SUPABASE_SERVICE_ROLE_KEY for Data API mode.")
    database_url = _normalize_database_url(str(database_url), config) if database_url else None

    tenant_id = _cfg(config, "memory", "tenant_id") or _env(config, "memory", "tenant_id_env", default_name="MEMORY_TENANT_ID")
    tenant_slug = (
        _cfg(config, "memory", "tenant_slug")
        or _env(config, "memory", "tenant_slug_env", default_name="MEMORY_TENANT_SLUG")
        or "agent-zero"
    )
    agent_key = (
        _cfg(config, "memory", "agent_key")
        or _env(config, "memory", "agent_key_env", default_name="MEMORY_AGENT_KEY")
        or "agent0:zero"
    )
    default_status = str(_cfg(config, "writes", "default_status", default="active"))
    if default_status not in RECORD_STATUSES:
        raise ConfigError(f"Invalid writes.default_status: {default_status}")

    return Settings(
        database_url=database_url,
        auth_mode=auth_mode,
        supabase_url=str(supabase_url).rstrip("/") if supabase_url else None,
        service_role_key=str(service_role_key) if service_role_key else None,
        tenant_id=str(tenant_id) if tenant_id else None,
        tenant_slug=str(tenant_slug),
        tenant_display_name=str(_cfg(config, "memory", "tenant_display_name", default="Agent Zero")),
        agent_key=str(agent_key),
        agent_display_name=str(_cfg(config, "memory", "agent_display_name", default="Agent Zero")),
        writes_enabled=bool(_cfg(config, "writes", "enabled", default=True)),
        auto_create_tenant=bool(_cfg(config, "writes", "auto_create_tenant", default=True)),
        auto_create_subjects=bool(_cfg(config, "writes", "auto_create_subjects", default=True)),
        default_status=default_status,
        default_limit=int(_cfg(config, "query", "default_limit", default=10)),
        max_limit=int(_cfg(config, "query", "max_limit", default=50)),
        include_inactive=bool(_cfg(config, "query", "include_inactive", default=False)),
        max_body_chars=int(_cfg(config, "writes", "max_body_chars", default=20000)),
        max_document_chars=int(_cfg(config, "writes", "max_document_chars", default=100000)),
        lifecycle_enabled=bool(_cfg(config, "lifecycle", "enabled", default=True)),
        record_run_history=bool(_cfg(config, "lifecycle", "record_run_history", default=True)),
        record_messages=bool(_cfg(config, "lifecycle", "record_messages", default=True)),
        record_steps=bool(_cfg(config, "lifecycle", "record_steps", default=True)),
        record_thoughts=bool(_cfg(config, "lifecycle", "record_thoughts", default=True)),
        record_tool_calls=bool(_cfg(config, "lifecycle", "record_tool_calls", default=True)),
        inject_context=bool(_cfg(config, "lifecycle", "inject_context", default=True)),
        extract_memories=bool(_cfg(config, "lifecycle", "extract_memories", default=True)),
        auto_episodic_memories=bool(_cfg(config, "lifecycle", "auto_episodic_memories", default=True)),
        auto_promote_memories=bool(_cfg(config, "lifecycle", "auto_promote_memories", default=True)),
        auto_reinforce_memories=bool(_cfg(config, "lifecycle", "auto_reinforce_memories", default=True)),
        auto_subjects=bool(_cfg(config, "lifecycle", "auto_subjects", default=True)),
        auto_knowledge_from_tools=bool(_cfg(config, "lifecycle", "auto_knowledge_from_tools", default=True)),
        max_context_chars=int(_cfg(config, "lifecycle", "max_context_chars", default=1800)),
        min_memory_confidence=float(_cfg(config, "lifecycle", "min_memory_confidence", default=0.65)),
        min_promotion_confidence=float(_cfg(config, "lifecycle", "min_promotion_confidence", default=0.65)),
        memory_access_confidence_delta=float(_cfg(config, "lifecycle", "memory_access_confidence_delta", default=0.01)),
        memory_evidence_confidence_delta=float(_cfg(config, "lifecycle", "memory_evidence_confidence_delta", default=0.08)),
        memory_repeat_confidence_delta=float(_cfg(config, "lifecycle", "memory_repeat_confidence_delta", default=0.10)),
        memory_correction_confidence_delta=float(_cfg(config, "lifecycle", "memory_correction_confidence_delta", default=-0.20)),
        memory_access_min_interval_minutes=int(_cfg(config, "lifecycle", "memory_access_min_interval_minutes", default=60)),
        min_knowledge_chars=int(_cfg(config, "lifecycle", "min_knowledge_chars", default=1200)),
        min_tool_response_chars=int(
            _cfg(
                config,
                "lifecycle",
                "min_tool_response_chars",
                default=_cfg(config, "lifecycle", "min_knowledge_chars", default=1200),
            )
        ),
        memory_cues=tuple(str(v).lower() for v in (_cfg(config, "lifecycle", "memory_cues", default=()) or ())),
        llm_enrichment_enabled=bool(_cfg(config, "llm_enrichment", "enabled", default=True)),
        llm_enrichment_provider=str(_cfg(config, "llm_enrichment", "provider", default="xai")),
        llm_enrichment_base_url=str(_cfg(config, "llm_enrichment", "base_url", default="https://api.x.ai/v1")).rstrip("/"),
        llm_enrichment_api_key=(
            _cfg(config, "llm_enrichment", "api_key")
            or os.environ.get(str(_cfg(config, "llm_enrichment", "api_key_env", default="XAI_API_KEY")))
        ),
        llm_enrichment_model=str(_cfg(config, "llm_enrichment", "model", default="grok-4-1-fast-non-reasoning")),
        llm_enrichment_timeout_seconds=int(_cfg(config, "llm_enrichment", "timeout_seconds", default=45)),
        llm_enrichment_batch_size=max(1, int(_cfg(config, "llm_enrichment", "batch_size", default=12))),
        llm_subject_enrichment_enabled=bool(_cfg(config, "llm_subject_enrichment", "enabled", default=True)),
        llm_subject_enrichment_model=str(_cfg(config, "llm_subject_enrichment", "model", default="grok-3-mini")),
        llm_subject_enrichment_timeout_seconds=int(_cfg(config, "llm_subject_enrichment", "timeout_seconds", default=20)),
        llm_subject_enrichment_max_tags=max(1, int(_cfg(config, "llm_subject_enrichment", "max_tags", default=6))),
        embeddings_enabled=bool(_cfg(config, "embeddings", "enabled", default=True)),
        embeddings_model=str(_cfg(config, "embeddings", "model", default="sentence-transformers/all-MiniLM-L6-v2")),
        embeddings_timeout_seconds=max(5, int(_cfg(config, "embeddings", "timeout_seconds", default=20))),
        embeddings_dimensions=384,
        embeddings_hash_fallback=bool(_cfg(config, "embeddings", "hash_fallback", default=True)),
    )


def _normalize_database_url(database_url: str, config: Mapping[str, Any] | None = None) -> str:
    parsed = urlparse(database_url)
    if parsed.hostname and "pooler.supabase.com" in parsed.hostname and parsed.username == "postgres":
        project_ref = (
            _cfg(config, "database", "project_ref")
            or os.environ.get("SUPABASE_PROJECT_REF")
            or os.environ.get("MEMORY_SUPABASE_PROJECT_REF")
            or project_ref
        )
        password = quote(unquote(parsed.password or ""), safe="")
        username = quote(f"postgres.{project_ref}", safe="")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{username}:{password}@{host}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return database_url


def sanitized_database_target(database_url: str) -> dict[str, Any]:
    parsed = urlparse(database_url)
    return {
        "scheme": parsed.scheme,
        "username": parsed.username,
        "host": parsed.hostname,
        "port": parsed.port,
        "database": parsed.path.lstrip("/"),
    }


def sanitized_data_api_target(settings: Settings) -> dict[str, Any]:
    return {
        "mode": settings.auth_mode,
        "supabase_url": settings.supabase_url,
        "schema": "memory",
        "service_role_key_set": bool(settings.service_role_key),
    }


class RestClient:
    def __init__(self, settings: Settings):
        if not settings.supabase_url or not settings.service_role_key:
            raise ConfigError("Data API mode requires supabase.url and a service role key.")
        self.base_url = settings.supabase_url.rstrip("/") + "/rest/v1"
        self.key = settings.service_role_key

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)}"
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
            "Accept-Profile": "memory",
            "Content-Profile": "memory",
        }
        data = None
        if body is not None:
            data = json.dumps(body, default=str).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        req = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {404, 406} and "schema" in detail.lower():
                raise ConfigError(
                    "Supabase Data API could not access the 'memory' schema. "
                    "Expose the memory schema in the project's Data API settings."
                ) from exc
            raise ConfigError(f"Data API request failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise ConfigError(f"Data API connection failed: {exc}") from exc

    def get(self, table: str, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = self.request("GET", table, query=query)
        return rows if isinstance(rows, list) else []

    def post(self, table: str, body: Mapping[str, Any] | list[Mapping[str, Any]], query: Mapping[str, Any] | None = None) -> Any:
        return self.request("POST", table, query=query, body=body, prefer="return=representation")

    def rpc(self, name: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = self.request("POST", f"rpc/{name}", body=body)
        return rows if isinstance(rows, list) else []

    def patch(self, table: str, body: Mapping[str, Any], query: Mapping[str, Any]) -> Any:
        return self.request("PATCH", table, query=query, body=body, prefer="return=representation")

    def upsert(self, table: str, body: Mapping[str, Any], on_conflict: str) -> dict[str, Any]:
        rows = self.request(
            "POST",
            table,
            query={"on_conflict": on_conflict},
            body=body,
            prefer="resolution=merge-duplicates,return=representation",
        )
        if isinstance(rows, list) and rows:
            return rows[0]
        return {}


def _rest_client(settings: Settings) -> RestClient:
    return RestClient(settings)


@contextmanager
def connect(settings: Settings) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    try:
        with psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=False) as conn:
            yield conn
    except psycopg.OperationalError as exc:
        hint = _connection_hint(settings.database_url, str(exc))
        raise ConfigError(hint) from exc


def _connection_hint(database_url: str, error: str) -> str:
    parsed = urlparse(database_url)
    username = parsed.username or ""
    host = parsed.hostname or ""
    if "password authentication failed" in error and "pooler.supabase.com" in host and username == "postgres":
        return (
            "Database authentication failed. This URL points at the Supabase pooler, "
            "but the username is 'postgres'. For Supabase pooler URLs, use "
            "'postgres.<project-ref>' as the username, for example "
            "'postgres.ufgbtyrdwngayrrutfdz'."
        )
    if "password authentication failed" in error:
        return "Database authentication failed. Check the database password in the configured Postgres URL."
    return f"connection failed: {error}"


def _limit(settings: Settings, value: Any = None) -> int:
    if value is None:
        return max(1, min(settings.default_limit, settings.max_limit))
    return max(1, min(int(value), settings.max_limit))


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str)


def _content_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text[0] not in "{[":
            return None
        try:
            return json.loads(text)
        except Exception:
            return None
    if isinstance(value, Mapping):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return value
    return None


def _plain_content(value: Any, prefer_thoughts: bool = False) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        if isinstance(value, Mapping):
            if prefer_thoughts:
                thoughts = value.get("thoughts") or value.get("thought") or value.get("reasoning")
                if thoughts:
                    return _plain_content(thoughts, prefer_thoughts=True)
            tool_args = value.get("tool_args") or value.get("args") or value.get("arguments")
            if isinstance(tool_args, Mapping):
                text = _plain_content(tool_args.get("text") or tool_args.get("content"))
                if text:
                    return text
            for key in ("content", "text", "message", "response", "output", "final_response"):
                text = _plain_content(value.get(key), prefer_thoughts=prefer_thoughts)
                if text:
                    return text
            if any(key in value for key in ("thought", "thoughts", "reasoning", "reasoning_summary", "monologue")):
                return None
            if not prefer_thoughts:
                parts = []
                for key, item in value.items():
                    text = _plain_content(item, prefer_thoughts=prefer_thoughts)
                    if text:
                        parts.append(f"{key}: {text}")
                return "\n".join(parts).strip() or None
            return None
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
            parts = [_plain_content(item, prefer_thoughts=prefer_thoughts) for item in value]
            text = "\n".join(part for part in parts if part).strip()
            return text or None
        return str(value).strip() or None
    text = value.strip()
    if not text:
        return None
    if text.startswith(("{", "[")):
        try:
            decoded = json.loads(text)
        except Exception:
            return None if text.lstrip().startswith('{"thoughts"') else text
        extracted = _plain_content(decoded, prefer_thoughts=prefer_thoughts)
        return extracted or (None if text.lstrip().startswith('{"thoughts"') else text)
    return text


def _content_fields(text_value: Any, json_value: Any = None, prefer_thoughts: bool = False) -> tuple[str | None, Any | None]:
    content_json = _content_json(json_value)
    if content_json is None:
        content_json = _content_json(text_value)
    source = content_json if content_json is not None else text_value
    content_text = _plain_content(source, prefer_thoughts=prefer_thoughts)
    return content_text, content_json


def _subject_enrichment_enabled(settings: Settings) -> bool:
    return bool(
        settings.llm_subject_enrichment_enabled
        and settings.llm_enrichment_provider.lower() == "xai"
        and settings.llm_enrichment_base_url
        and settings.llm_enrichment_api_key
    )


def _embeddings_enabled(settings: Settings) -> bool:
    return bool(
        settings.embeddings_enabled
        and settings.llm_enrichment_base_url
        and settings.llm_enrichment_api_key
    )


def _coerce_embedding_dimensions(values: Sequence[Any], dimensions: int) -> list[float]:
    vector = [float(item) for item in values]
    if len(vector) == dimensions:
        return vector
    if len(vector) > dimensions:
        return vector[:dimensions]
    return vector + [0.0] * (dimensions - len(vector))


def _hash_embedding(text: str, dimensions: int) -> list[float]:
    bins = [0.0] * dimensions
    for token in re.findall(r"[a-z0-9][a-z0-9_.:/-]{1,}", (text or "").lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        slot = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if (digest[4] & 1) else -1.0
        weight = 0.5 + (digest[5] / 255.0)
        bins[slot] += sign * weight
    norm = math.sqrt(sum(value * value for value in bins))
    if norm > 0:
        bins = [value / norm for value in bins]
    return bins


def _request_embedding(settings: Settings, text: str) -> list[float] | None:
    payload = {"model": settings.embeddings_model, "input": text}
    request = Request(
        f"{settings.llm_enrichment_base_url}/embeddings",
        data=json.dumps(payload, default=str).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.llm_enrichment_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.embeddings_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = body.get("data") if isinstance(body, Mapping) else None
        item = data[0] if isinstance(data, list) and data else None
        raw_vector = item.get("embedding") if isinstance(item, Mapping) else None
        if isinstance(raw_vector, Sequence) and not isinstance(raw_vector, (bytes, bytearray, str)):
            return _coerce_embedding_dimensions(raw_vector, settings.embeddings_dimensions)
    except Exception:
        return None
    return None


def _auto_embedding(settings: Settings, text: Any) -> str | None:
    plain = _plain_content(text)
    if not plain:
        return None
    vector: list[float] | None = None
    if _embeddings_enabled(settings):
        vector = _request_embedding(settings, plain)
    if vector is None and settings.embeddings_hash_fallback:
        vector = _hash_embedding(plain, settings.embeddings_dimensions)
    if vector is None:
        return None
    return _vector(vector)


def _resolve_embedding(settings: Settings, explicit_embedding: Any, text: Any) -> str | None:
    explicit = _vector(explicit_embedding)
    if explicit:
        return explicit
    return _auto_embedding(settings, text)


def _slugify_subject(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64]


def _normalize_tags(values: Sequence[Any], max_tags: int) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip().lower()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^a-z0-9 _./:-]", "", text).strip()
        if len(text) < 2 or text in seen:
            continue
        seen.add(text)
        tags.append(text[:48])
        if len(tags) >= max_tags:
            break
    return tags


def _fallback_tags(content_text: str, max_tags: int) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9_.:/-]{2,}", content_text.lower())
    stop = {
        "the",
        "and",
        "for",
        "that",
        "with",
        "this",
        "from",
        "into",
        "then",
        "when",
        "where",
        "have",
        "your",
        "json",
        "text",
        "content",
    }
    ranked: dict[str, int] = {}
    for token in tokens:
        if token in stop:
            continue
        ranked[token] = ranked.get(token, 0) + 1
    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [item[0][:48] for item in ordered[:max_tags]]


def _summarize_and_tag_subject(settings: Settings, content_text: str, existing_summary: str | None = None) -> dict[str, Any]:
    clean = (content_text or "").strip()
    if not clean:
        return {"summary": (existing_summary or "").strip(), "tags": [], "subject_display": ""}
    fallback_summary = (existing_summary or "").strip() or clean[:280]
    fallback_tags = _fallback_tags(clean, settings.llm_subject_enrichment_max_tags)
    fallback_subject = fallback_tags[0] if fallback_tags else ""
    if not _subject_enrichment_enabled(settings):
        return {"summary": fallback_summary, "tags": fallback_tags, "subject_display": fallback_subject}
    payload = {
        "model": settings.llm_subject_enrichment_model,
        "stream": False,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You extract concise metadata for storage. "
                    "Return strict JSON with keys: summary (string), tags (array of short strings), subject (string). "
                    "summary must be factual plain text under 220 chars. "
                    "tags must be concise lowercase topic tags (no sentences), max "
                    f"{settings.llm_subject_enrichment_max_tags}. "
                    "subject must be a short noun phrase representing the primary subject."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "content_text": clean,
                        "existing_summary": existing_summary or "",
                        "max_tags": settings.llm_subject_enrichment_max_tags,
                    },
                    ensure_ascii=True,
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
        with urlopen(request, timeout=settings.llm_subject_enrichment_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        message = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
        parsed = json.loads(message) if isinstance(message, str) else {}
        summary = str(parsed.get("summary") or "").strip() or fallback_summary
        tags = _normalize_tags(parsed.get("tags") or [], settings.llm_subject_enrichment_max_tags) or fallback_tags
        subject = str(parsed.get("subject") or "").strip() or (tags[0] if tags else fallback_subject)
        return {"summary": summary[:280], "tags": tags, "subject_display": subject[:80]}
    except Exception:
        return {"summary": fallback_summary, "tags": fallback_tags, "subject_display": fallback_subject}


def _merge_tags(existing: Sequence[Any] | None, inferred: Sequence[str], max_tags: int) -> list[str]:
    if isinstance(existing, str):
        base: list[Any] = [existing]
    else:
        base = list(existing or [])
    return _normalize_tags([*base, *inferred], max_tags)


def _metadata_with_enrichment(metadata: Any, model: str, enabled: bool) -> dict[str, Any]:
    base = dict(metadata) if isinstance(metadata, Mapping) else {"created_by": "memory_knowledge"}
    base["llm_subject_enrichment"] = {"enabled": bool(enabled), "model": model}
    return base


def _subject_from_display(display: str) -> dict[str, str] | None:
    label = str(display or "").strip()
    if not label:
        return None
    slug = _slugify_subject(label)
    if not slug:
        return None
    return {
        "subject_key": f"topic:{slug}",
        "subject_type": "topic",
        "display_name": label[:120],
    }


def _array(values: Sequence[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [str(v) for v in values if str(v).strip()]


def _vector(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        parts = [float(item) for item in value]
        if len(parts) != 384:
            raise ValueError("embedding must contain exactly 384 dimensions.")
        return "[" + ",".join(f"{item:.8g}" for item in parts) + "]"
    raise ValueError("embedding must be a vector string or numeric sequence.")


def _validate_uuid(value: str, label: str) -> str:
    if not re.match(r"^[0-9a-fA-F-]{36}$", value or ""):
        raise ValueError(f"{label} must be a UUID.")
    return value


def _fetch_one(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _fetch_all(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def ensure_tenant(conn: psycopg.Connection[dict[str, Any]], settings: Settings, create: bool = False) -> str:
    if settings.tenant_id:
        _validate_uuid(settings.tenant_id, "tenant_id")
        row = _fetch_one(conn, "select id from memory.tenants where id = %s", (settings.tenant_id,))
        if row:
            return str(row["id"])
        if not create or not settings.auto_create_tenant:
            raise ConfigError(f"Tenant not found: {settings.tenant_id}")
        row = _fetch_one(
            conn,
            """
            insert into memory.tenants (id, slug, display_name, metadata)
            values (%s, %s, %s, %s::jsonb)
            on conflict (id) do update set updated_at = now()
            returning id
            """,
            (settings.tenant_id, settings.tenant_slug, settings.tenant_display_name, _json({"created_by": "memory_knowledge"})),
        )
        return str(row["id"])

    row = _fetch_one(conn, "select id from memory.tenants where slug = %s", (settings.tenant_slug,))
    if row:
        return str(row["id"])
    if not create or not settings.auto_create_tenant:
        raise ConfigError("Set memory.tenant_id or create the configured memory.tenant_slug with a write operation.")
    row = _fetch_one(
        conn,
        """
        insert into memory.tenants (slug, display_name, metadata)
        values (%s, %s, %s::jsonb)
        on conflict (slug) do update set display_name = excluded.display_name, updated_at = now()
        returning id
        """,
        (settings.tenant_slug, settings.tenant_display_name, _json({"created_by": "memory_knowledge"})),
    )
    return str(row["id"])


def get_or_create_actor(conn: psycopg.Connection[dict[str, Any]], settings: Settings, tenant_id: str) -> str:
    row = _fetch_one(
        conn,
        """
        select id from memory.actors
        where tenant_id = %s and kind = 'agent' and external_ref = %s and archived_at is null
        """,
        (tenant_id, settings.agent_key),
    )
    if row:
        return str(row["id"])
    row = _fetch_one(
        conn,
        """
        insert into memory.actors (tenant_id, kind, external_ref, display_name, metadata)
        values (%s, 'agent', %s, %s, %s::jsonb)
        on conflict (tenant_id, kind, external_ref)
        do update set display_name = excluded.display_name, updated_at = now()
        returning id
        """,
        (tenant_id, settings.agent_key, settings.agent_display_name, _json({"source": "agent_zero"})),
    )
    return str(row["id"])


def get_or_create_subject(
    conn: psycopg.Connection[dict[str, Any]],
    settings: Settings,
    tenant_id: str,
    subject: Mapping[str, Any] | None,
) -> str | None:
    if not subject:
        return None
    subject_id = subject.get("id")
    if subject_id:
        return _validate_uuid(str(subject_id), "subject.id")
    subject_key = subject.get("key") or subject.get("subject_key")
    if not subject_key:
        return None
    row = _fetch_one(
        conn,
        "select id from memory.subjects where tenant_id = %s and subject_key = %s",
        (tenant_id, str(subject_key)),
    )
    if row:
        return str(row["id"])
    if not settings.auto_create_subjects:
        raise ValueError(f"Subject not found: {subject_key}")
    row = _fetch_one(
        conn,
        """
        insert into memory.subjects (
          tenant_id, subject_key, subject_type, display_name, aliases, attributes
        )
        values (%s, %s, %s, %s, %s::text[], %s::jsonb)
        on conflict (tenant_id, subject_key)
        do update set display_name = coalesce(excluded.display_name, memory.subjects.display_name),
                      updated_at = now()
        returning id
        """,
        (
            tenant_id,
            str(subject_key),
            str(subject.get("type") or subject.get("subject_type") or "general"),
            subject.get("display_name"),
            _array(subject.get("aliases") or []) or [],
            _json(subject.get("attributes") or {}),
        ),
    )
    return str(row["id"])


def _rest_ensure_tenant(client: RestClient, settings: Settings, create: bool = False) -> str:
    if settings.tenant_id:
        rows = client.get("tenants", {"select": "id", "id": f"eq.{settings.tenant_id}", "limit": 1})
        if rows:
            return str(rows[0]["id"])
        if not create or not settings.auto_create_tenant:
            raise ConfigError(f"Tenant not found: {settings.tenant_id}")
        row = client.upsert(
            "tenants",
            {
                "id": settings.tenant_id,
                "slug": settings.tenant_slug,
                "display_name": settings.tenant_display_name,
                "metadata": {"created_by": "memory_knowledge"},
            },
            "id",
        )
        return str(row["id"])

    rows = client.get("tenants", {"select": "id", "slug": f"eq.{settings.tenant_slug}", "limit": 1})
    if rows:
        return str(rows[0]["id"])
    if not create or not settings.auto_create_tenant:
        raise ConfigError("Set memory.tenant_id or create the configured memory.tenant_slug with a write operation.")
    row = client.upsert(
        "tenants",
        {
            "slug": settings.tenant_slug,
            "display_name": settings.tenant_display_name,
            "metadata": {"created_by": "memory_knowledge"},
        },
        "slug",
    )
    return str(row["id"])


def _rest_get_or_create_actor(client: RestClient, settings: Settings, tenant_id: str) -> str:
    rows = client.get(
        "actors",
        {
            "select": "id",
            "tenant_id": f"eq.{tenant_id}",
            "kind": "eq.agent",
            "external_ref": f"eq.{settings.agent_key}",
            "archived_at": "is.null",
            "limit": 1,
        },
    )
    if rows:
        return str(rows[0]["id"])
    row = client.upsert(
        "actors",
        {
            "tenant_id": tenant_id,
            "kind": "agent",
            "external_ref": settings.agent_key,
            "display_name": settings.agent_display_name,
            "metadata": {"source": "agent_zero"},
        },
        "tenant_id,kind,external_ref",
    )
    return str(row["id"])


def _rest_get_or_create_subject(
    client: RestClient,
    settings: Settings,
    tenant_id: str,
    subject: Mapping[str, Any] | None,
) -> str | None:
    if not subject:
        return None
    subject_id = subject.get("id")
    if subject_id:
        return _validate_uuid(str(subject_id), "subject.id")
    subject_key = subject.get("key") or subject.get("subject_key")
    if not subject_key:
        return None
    rows = client.get("subjects", {"select": "id", "tenant_id": f"eq.{tenant_id}", "subject_key": f"eq.{subject_key}", "limit": 1})
    if rows:
        return str(rows[0]["id"])
    if not settings.auto_create_subjects:
        raise ValueError(f"Subject not found: {subject_key}")
    row = client.upsert(
        "subjects",
        {
            "tenant_id": tenant_id,
            "subject_key": str(subject_key),
            "subject_type": str(subject.get("type") or subject.get("subject_type") or "general"),
            "display_name": subject.get("display_name"),
            "aliases": _array(subject.get("aliases") or []) or [],
            "attributes": subject.get("attributes") or {},
        },
        "tenant_id,subject_key",
    )
    return str(row["id"])


def _contains_query(row: Mapping[str, Any], query: str, fields: Sequence[str]) -> bool:
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query) if token]
    if not tokens:
        return True
    haystack = " ".join(str(row.get(field) or "") for field in fields).lower()
    return all(token in haystack for token in tokens)


def _rest_health(settings: Settings) -> dict[str, Any]:
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=settings.writes_enabled)
    counts: dict[str, int] = {}
    for table in ("memory_items", "knowledge_documents", "run_history"):
        rows = client.get(table, {"select": "id", "tenant_id": f"eq.{tenant_id}", "archived_at": "is.null", "limit": 1000})
        counts[table] = len(rows)
    return {
        "ok": True,
        "auth_mode": "data_api",
        "target": sanitized_data_api_target(settings),
        "tenant_id": tenant_id,
        "tenant_slug": settings.tenant_slug,
        "writes_enabled": settings.writes_enabled,
        "counts": counts,
    }


def _rest_search_memory(
    settings: Settings,
    query: str,
    limit: Any = None,
    kinds: Sequence[str] | None = None,
    include_inactive: bool | None = None,
    query_embedding: Any = None,
) -> list[dict[str, Any]]:
    if not query or not str(query).strip():
        raise ValueError("query is required.")
    kinds_array = _array(kinds)
    active_only = not (settings.include_inactive if include_inactive is None else include_inactive)
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    if query_embedding is not None:
        rows = client.rpc(
            "hybrid_search_memory",
            {
                "p_tenant_id": tenant_id,
                "p_query": query,
                "p_query_embedding": _vector(query_embedding),
                "p_match_count": _limit(settings, limit),
                "p_kinds": kinds_array,
                "p_include_inactive": not active_only,
            },
        )
        return rows[: _limit(settings, limit)]
    rows = client.rpc(
        "search_memory_text",
        {
            "p_tenant_id": tenant_id,
            "p_query": query,
            "p_match_count": _limit(settings, limit),
            "p_kinds": kinds_array,
            "p_include_inactive": not active_only,
        },
    )
    return rows[: _limit(settings, limit)]


def _rest_search_knowledge(settings: Settings, query: str, limit: Any = None, query_embedding: Any = None) -> list[dict[str, Any]]:
    if not query or not str(query).strip():
        raise ValueError("query is required.")
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    if query_embedding is not None:
        rows = client.rpc(
            "hybrid_search_knowledge",
            {
                "p_tenant_id": tenant_id,
                "p_query": query,
                "p_query_embedding": _vector(query_embedding),
                "p_match_count": _limit(settings, limit),
            },
        )
        return rows[: _limit(settings, limit)]
    rows = client.rpc(
        "search_knowledge_text",
        {
            "p_tenant_id": tenant_id,
            "p_query": query,
            "p_match_count": _limit(settings, limit),
        },
    )
    return rows[: _limit(settings, limit)]


def _rest_find_similar_memories(
    settings: Settings,
    summary: str,
    limit: Any = None,
    kinds: Sequence[str] | None = None,
    exclude_id: str | None = None,
    min_similarity: float = 0.35,
) -> list[dict[str, Any]]:
    if not summary or not summary.strip():
        return []
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    return client.rpc(
        "find_similar_memories",
        {
            "p_tenant_id": tenant_id,
            "p_summary": summary,
            "p_match_count": _limit(settings, limit),
            "p_kinds": _array(kinds),
            "p_exclude_id": exclude_id,
            "p_min_similarity": min_similarity,
        },
    )


def _rest_list_table(settings: Settings, table: str, filters: Mapping[str, Any] | None = None, limit: Any = None) -> list[dict[str, Any]]:
    if table not in READ_TABLES:
        raise ValueError(f"Unsupported table: {table}")
    filters = filters or {}
    unknown = set(filters) - TABLE_COLUMNS[table]
    if unknown:
        raise ValueError(f"Unsupported filter column(s) for {table}: {', '.join(sorted(unknown))}")
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    params: dict[str, Any] = {"select": ",".join(sorted(TABLE_COLUMNS[table])), "limit": _limit(settings, limit)}
    if table == "tenants":
        params["id"] = f"eq.{tenant_id}"
    elif table in TENANT_TABLES:
        params["tenant_id"] = f"eq.{tenant_id}"
    for key, value in filters.items():
        params[key] = f"eq.{value}"
    return client.get(table, params)


def _rest_create_memory(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not settings.writes_enabled:
        raise ConfigError("Memory writes are disabled.")
    kind = str(payload.get("kind") or "semantic")
    status = str(payload.get("status") or settings.default_status)
    if kind not in MEMORY_KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")
    if status not in RECORD_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    summary = str(payload.get("summary") or "").strip()
    body_text = _plain_content(payload.get("body")) or ""
    source_text = body_text or summary
    inferred = _summarize_and_tag_subject(settings, source_text, existing_summary=summary)
    summary = str(inferred.get("summary") or summary).strip()
    if not summary:
        raise ValueError("summary is required.")
    tags = _merge_tags(payload.get("tags"), inferred.get("tags") or [], settings.llm_subject_enrichment_max_tags)
    subject_payload = payload.get("subject") or _subject_from_display(str(inferred.get("subject_display") or ""))
    embedding_value = _resolve_embedding(
        settings,
        payload.get("embedding"),
        "\n\n".join(part for part in (payload.get("title"), summary, payload.get("body")) if part),
    )
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    actor_id = _rest_get_or_create_actor(client, settings, tenant_id)
    subject_id = _rest_get_or_create_subject(client, settings, tenant_id, subject_payload)
    row = client.post(
        "memory_items",
        {
            "tenant_id": tenant_id,
            "subject_id": subject_id,
            "kind": kind,
            "status": status,
            "title": payload.get("title"),
            "summary": summary,
            "body": payload.get("body"),
            "facts": payload.get("facts") or {},
            "tags": _array(tags) or [],
            "embedding": embedding_value,
            "importance": payload.get("importance") if payload.get("importance") is not None else 0.5,
            "confidence": payload.get("confidence") if payload.get("confidence") is not None else 0.5,
            "source_ref": payload.get("source_ref"),
            "metadata": _metadata_with_enrichment(
                payload.get("metadata"),
                settings.llm_subject_enrichment_model,
                settings.llm_subject_enrichment_enabled,
            ),
            "created_by_actor_id": actor_id,
        },
    )[0]
    memory_item_id = str(row["id"])
    if kind == "semantic":
        details = payload.get("semantic") or payload.get("details") or {}
        client.post(
            "semantic_memories",
            {
                "memory_item_id": memory_item_id,
                "tenant_id": tenant_id,
                "concept_key": details.get("concept_key"),
                "statement": details.get("statement") or summary,
                "qualifiers": details.get("qualifiers") or {},
            },
        )
    elif kind == "episodic":
        details = payload.get("episodic") or payload.get("details") or {}
        client.post(
            "episodic_memories",
            {
                "memory_item_id": memory_item_id,
                "tenant_id": tenant_id,
                "happened_at": details.get("happened_at"),
                "conversation_ref": details.get("conversation_ref"),
                "location": details.get("location"),
                "participants": details.get("participants") or [],
                "outcome": details.get("outcome"),
                "emotion": details.get("emotion") or {},
            },
        )
    for evidence in payload.get("evidence") or []:
        client.post(
            "memory_evidence",
            {
                "tenant_id": tenant_id,
                "memory_item_id": memory_item_id,
                "run_id": evidence.get("run_id"),
                "run_message_id": evidence.get("run_message_id"),
                "tool_execution_id": evidence.get("tool_execution_id"),
                "knowledge_chunk_id": evidence.get("knowledge_chunk_id"),
                "external_ref": evidence.get("external_ref") or "agent_zero",
                "quote": evidence.get("quote"),
                "support_score": evidence.get("support_score") if evidence.get("support_score") is not None else 1,
                "metadata": evidence.get("metadata") or {},
            },
        )
    return row


def _rest_upsert_knowledge_document(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not settings.writes_enabled:
        raise ConfigError("Knowledge writes are disabled.")
    document_key = str(payload.get("document_key") or "").strip()
    title = str(payload.get("title") or "").strip()
    content_text, content_json = _content_fields(payload.get("content_text"), payload.get("content_json"))
    if not document_key or not title:
        raise ValueError("document_key and title are required.")
    if content_text and len(content_text) > settings.max_document_chars:
        raise ValueError(f"content_text exceeds max_document_chars ({settings.max_document_chars}).")
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    doc = client.upsert(
        "knowledge_documents",
        {
            "tenant_id": tenant_id,
            "document_key": document_key,
            "title": title,
            "uri": payload.get("uri"),
            "content_text": content_text or None,
            "content_json": content_json,
            "status": str(payload.get("status") or "active"),
            "metadata": payload.get("metadata") or {"created_by": "memory_knowledge"},
            "archived_at": None,
        },
        "tenant_id,document_key",
    )
    document_id = str(doc["id"])
    chunks = payload.get("chunks")
    if chunks is None and content_text:
        chunks = [{"chunk_index": 0, "content_text": content_text, "heading": title, "tags": payload.get("tags") or []}]
    written = 0
    for index, chunk in enumerate(chunks or []):
        chunk_text, chunk_json = _content_fields(chunk.get("content_text"), chunk.get("content_json"))
        chunk_embedding = _resolve_embedding(
            settings,
            chunk.get("embedding"),
            "\n\n".join(part for part in (chunk.get("heading"), chunk_text) if part),
        )
        client.upsert(
            "knowledge_chunks",
            {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "chunk_index": int(chunk.get("chunk_index", index)),
                "heading": chunk.get("heading"),
                "content_text": chunk_text or "",
                "content_json": chunk_json,
                "token_count": chunk.get("token_count"),
                "embedding": chunk_embedding,
                "tags": _array(chunk.get("tags") or []) or [],
                "metadata": chunk.get("metadata") or {},
                "archived_at": None,
            },
            "document_id,chunk_index",
        )
        written += 1
    return {**doc, "chunks_written": written}


def _rest_log_run(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not settings.writes_enabled:
        raise ConfigError("Run logging is disabled.")
    operation = str(payload.get("operation") or "").strip()
    if not operation:
        raise ValueError("operation is required.")
    status = str(payload.get("status") or "running")
    if status not in RUN_STATUSES:
        raise ValueError(f"Unsupported run status: {status}")
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    actor_id = _rest_get_or_create_actor(client, settings, tenant_id)
    body = {
        "tenant_id": tenant_id,
        "run_key": payload.get("run_key"),
        "parent_run_id": payload.get("parent_run_id"),
        "actor_id": actor_id,
        "agent_name": payload.get("agent_name") or settings.agent_display_name,
        "model": payload.get("model"),
        "operation": operation,
        "status": status,
        "input_text": payload.get("input_text"),
        "input_payload": payload.get("input_payload") or {},
        "response_text": payload.get("response_text"),
        "response_payload": payload.get("response_payload"),
        "response_output": payload.get("response_output"),
        "reasoning_summary": payload.get("reasoning_summary"),
        "thoughts_payload": payload.get("thoughts_payload") or [],
        "duration_ms": payload.get("duration_ms"),
        "ended_at": payload.get("ended_at") or (_now_iso() if status in {"succeeded", "failed", "cancelled"} else None),
        "error_code": payload.get("error_code"),
        "error_message": payload.get("error_message"),
        "metadata": payload.get("metadata") or {"created_by": "memory_knowledge"},
    }
    if payload.get("run_key"):
        return client.upsert("run_history", _omit_none(body), "tenant_id,run_key")
    return client.post("run_history", body)[0]


def _rest_log_run_message(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    role = str(payload.get("role") or "")
    if role not in MESSAGE_ROLES:
        raise ValueError(f"Unsupported message role: {role}")
    content_text, content_json = _content_fields(payload.get("content_text"), payload.get("content_json"))
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    return client.upsert(
        "run_messages",
        {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "role": role,
            "ordinal": int(payload.get("ordinal") or 1),
            "content_text": content_text,
            "content_json": content_json,
            "token_count": payload.get("token_count"),
            "metadata": payload.get("metadata") or {},
        },
        "run_id,ordinal",
    )


def _rest_log_run_step(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    return client.upsert(
        "run_steps",
        _omit_none({
            "tenant_id": tenant_id,
            "run_id": run_id,
            "parent_step_id": payload.get("parent_step_id"),
            "sequence_number": int(payload.get("sequence_number") or 1),
            "step_type": str(payload.get("step_type") or "agent"),
            "name": str(payload.get("name") or payload.get("step_type") or "step"),
            "status": str(payload.get("status") or "running"),
            "input_payload": payload.get("input_payload") or {},
            "output_payload": payload.get("output_payload"),
            "ended_at": payload.get("ended_at")
            or (_now_iso() if str(payload.get("status") or "running") in {"succeeded", "failed", "cancelled"} else None),
            "duration_ms": payload.get("duration_ms"),
            "metadata": payload.get("metadata") or {},
        }),
        "run_id,sequence_number",
    )


def _rest_log_run_thought(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    content_text, content_json = _content_fields(
        payload.get("content_text"),
        payload.get("content_json"),
        prefer_thoughts=True,
    )
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    return client.upsert(
        "run_thoughts",
        {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "step_id": payload.get("step_id"),
            "sequence_number": int(payload.get("sequence_number") or 1),
            "thought_type": str(payload.get("thought_type") or "summary"),
            "content_text": content_text,
            "content_json": content_json,
            "visibility": str(payload.get("visibility") or "internal"),
        },
        "run_id,sequence_number",
    )


def _rest_log_tool_execution(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    tool_name = str(payload.get("tool_name") or "").strip()
    if not tool_name:
        raise ValueError("tool_name is required.")
    status = str(payload.get("status") or "running")
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    tool_call_id = payload.get("tool_call_id")
    body = _omit_none(
        {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "step_id": payload.get("step_id"),
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "input_payload": payload.get("input_payload") or {},
            "output_payload": payload.get("output_payload"),
            "stdout_text": payload.get("stdout_text"),
            "stderr_text": payload.get("stderr_text"),
            "status": status,
            "ended_at": payload.get("ended_at")
            or (_now_iso() if status in {"succeeded", "failed", "cancelled"} else None),
            "duration_ms": payload.get("duration_ms"),
            "error_code": payload.get("error_code"),
            "error_message": payload.get("error_message"),
            "metadata": payload.get("metadata") or {},
        }
    )
    if tool_call_id:
        existing = client.get(
            "tool_executions",
            {
                "tenant_id": f"eq.{tenant_id}",
                "run_id": f"eq.{run_id}",
                "tool_call_id": f"eq.{tool_call_id}",
                "limit": "1",
            },
        )
        if existing:
            current = existing[0]
            patch = dict(body)
            if body.get("input_payload") == {} and current.get("input_payload"):
                patch.pop("input_payload", None)
            if "metadata" in body and isinstance(current.get("metadata"), dict) and isinstance(body["metadata"], dict):
                patch["metadata"] = {**current["metadata"], **body["metadata"]}
            rows = client.patch("tool_executions", patch, {"id": f"eq.{current['id']}"})
            return rows[0] if isinstance(rows, list) and rows else {}
        return client.upsert("tool_executions", body, "tenant_id,run_id,tool_call_id")
    return client.post("tool_executions", body)[0]


def _rest_upsert_subject(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    subject_key = str(payload.get("subject_key") or payload.get("key") or "").strip()
    if not subject_key:
        raise ValueError("subject_key is required.")
    return client.upsert(
        "subjects",
        {
            "tenant_id": tenant_id,
            "subject_key": subject_key,
            "subject_type": str(payload.get("subject_type") or payload.get("type") or "general"),
            "display_name": payload.get("display_name"),
            "aliases": _array(payload.get("aliases") or []) or [],
            "attributes": payload.get("attributes") or {},
        },
        "tenant_id,subject_key",
    )


def _rest_add_memory_evidence(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=True)
    memory_item_id = _validate_uuid(str(payload.get("memory_item_id") or ""), "memory_item_id")
    return client.post(
        "memory_evidence",
        {
            "tenant_id": tenant_id,
            "memory_item_id": memory_item_id,
            "run_id": payload.get("run_id"),
            "run_message_id": payload.get("run_message_id"),
            "tool_execution_id": payload.get("tool_execution_id"),
            "knowledge_chunk_id": payload.get("knowledge_chunk_id"),
            "external_ref": payload.get("external_ref"),
            "quote": payload.get("quote"),
            "support_score": payload.get("support_score") if payload.get("support_score") is not None else 1,
            "metadata": payload.get("metadata") or {},
        },
    )[0]


def _rest_promote_memory(settings: Settings, memory_item_id: str, reason: str | None = None) -> dict[str, Any]:
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    existing = client.get(
        "memory_items",
        {"select": "metadata", "id": f"eq.{memory_item_id}", "tenant_id": f"eq.{tenant_id}", "limit": 1},
    )
    metadata = dict(existing[0].get("metadata") or {}) if existing else {}
    metadata["promotion"] = {
        "mode": "automatic",
        "reason": reason or "confidence_threshold",
        "promoted_at": _now_iso(),
    }
    rows = client.patch(
        "memory_items",
        {"status": "active", "metadata": metadata},
        {"id": f"eq.{memory_item_id}", "tenant_id": f"eq.{tenant_id}", "status": "eq.candidate"},
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return {}


def _rest_set_memory_status(settings: Settings, memory_item_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
    if status not in RECORD_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    existing = client.get(
        "memory_items",
        {"select": "metadata", "id": f"eq.{memory_item_id}", "tenant_id": f"eq.{tenant_id}", "limit": 1},
    )
    metadata = dict(existing[0].get("metadata") or {}) if existing else {}
    metadata["status_change"] = {"status": status, "reason": reason or "manual", "changed_at": _now_iso()}
    rows = client.patch(
        "memory_items",
        {"status": status, "metadata": metadata},
        {"id": f"eq.{memory_item_id}", "tenant_id": f"eq.{tenant_id}"},
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return {}


def _rest_adjust_memory_confidence(
    settings: Settings,
    memory_item_id: str,
    delta: float,
    reason: str | None = None,
    support_score: float | None = None,
) -> dict[str, Any]:
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    rows = client.get(
        "memory_items",
        {
            "select": "id,tenant_id,kind,status,title,summary,confidence,metadata,last_accessed_at,access_count",
            "id": f"eq.{memory_item_id}",
            "tenant_id": f"eq.{tenant_id}",
            "limit": 1,
        },
    )
    if not rows:
        return {}
    current = rows[0]
    confidence = max(0.0, min(1.0, float(current.get("confidence") or 0.5) + float(delta)))
    metadata = dict(current.get("metadata") or {})
    events = list(metadata.get("confidence_events") or [])[-19:]
    events.append(
        {
            "delta": float(delta),
            "reason": reason or "adjustment",
            "support_score": support_score,
            "confidence": confidence,
            "at": _now_iso(),
        }
    )
    metadata["confidence_events"] = events
    patched = client.patch(
        "memory_items",
        {"confidence": confidence, "metadata": metadata},
        {"id": f"eq.{memory_item_id}", "tenant_id": f"eq.{tenant_id}"},
    )
    if isinstance(patched, list) and patched:
        return patched[0]
    return {}


def _rest_record_memory_access(settings: Settings, memory_item_ids: Sequence[str], reason: str | None = None) -> list[dict[str, Any]]:
    ids = [_validate_uuid(str(memory_item_id), "memory_item_id") for memory_item_id in memory_item_ids if memory_item_id]
    if not ids:
        return []
    client = _rest_client(settings)
    tenant_id = _rest_ensure_tenant(client, settings, create=False)
    return client.rpc(
        "record_memory_access",
        {
            "p_tenant_id": tenant_id,
            "p_memory_item_ids": ids,
            "p_confidence_delta": settings.memory_access_confidence_delta,
            "p_min_interval": f"{max(0, settings.memory_access_min_interval_minutes)} minutes",
        },
    )


def health(settings: Settings) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_health(settings)
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        table_counts = _fetch_all(
            conn,
            """
            select table_name, table_type
            from information_schema.tables
            where table_schema = 'memory'
            order by table_name
            """,
        )
        counts = _fetch_one(
            conn,
            """
            select
              (select count(*) from memory.memory_items where tenant_id = %s and archived_at is null) as memory_items,
              (select count(*) from memory.knowledge_documents where tenant_id = %s and archived_at is null) as knowledge_documents,
              (select count(*) from memory.run_history where tenant_id = %s and archived_at is null) as runs
            """,
            (tenant_id, tenant_id, tenant_id),
        )
        conn.commit()
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "tenant_slug": settings.tenant_slug,
            "writes_enabled": settings.writes_enabled,
            "counts": counts,
            "schema_objects": table_counts,
        }


def search_memory(
    settings: Settings,
    query: str,
    limit: Any = None,
    kinds: Sequence[str] | None = None,
    include_inactive: bool | None = None,
    query_embedding: Any = None,
) -> list[dict[str, Any]]:
    if settings.auth_mode == "data_api":
        return _rest_search_memory(settings, query, limit, kinds, include_inactive, query_embedding)
    if not query or not str(query).strip():
        raise ValueError("query is required.")
    kinds_array = _array(kinds)
    for kind in kinds_array or []:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"Unsupported memory kind: {kind}")
    active_only = not (settings.include_inactive if include_inactive is None else include_inactive)
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        if query_embedding is not None:
            rows = _fetch_all(
                conn,
                """
                select id, kind, status, title, summary, body, tags, importance, confidence, source_ref,
                       rank, similarity, score, access_count, last_accessed_at, created_at, updated_at
                from memory.hybrid_search_memory(%s, %s, %s::extensions.vector, %s, %s::memory.memory_kind[], %s)
                """,
                (tenant_id, query, _vector(query_embedding), _limit(settings, limit), kinds_array, not active_only),
            )
            conn.commit()
            return rows
        rows = _fetch_all(
            conn,
            """
            select id, kind, status, title, summary, body, tags, importance, confidence, source_ref,
                   rank, score, access_count, last_accessed_at, created_at, updated_at
            from memory.search_memory_text(%s, %s, %s, %s::memory.memory_kind[], %s)
            """,
            (tenant_id, query, _limit(settings, limit), kinds_array, not active_only),
        )
        conn.commit()
        return rows


def search_knowledge(settings: Settings, query: str, limit: Any = None, query_embedding: Any = None) -> list[dict[str, Any]]:
    if settings.auth_mode == "data_api":
        return _rest_search_knowledge(settings, query, limit, query_embedding)
    if not query or not str(query).strip():
        raise ValueError("query is required.")
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        if query_embedding is not None:
            rows = _fetch_all(
                conn,
                """
                select chunk_id, document_id, document_title, heading, content_text, tags, rank, similarity, score, metadata, updated_at
                from memory.hybrid_search_knowledge(%s, %s, %s::extensions.vector, %s)
                """,
                (tenant_id, query, _vector(query_embedding), _limit(settings, limit)),
            )
            conn.commit()
            return rows
        rows = _fetch_all(
            conn,
            """
            select chunk_id, document_id, document_title, heading, content_text, tags, rank, score, metadata, updated_at
            from memory.search_knowledge_text(%s, %s, %s)
            """,
            (tenant_id, query, _limit(settings, limit)),
        )
        conn.commit()
        return rows


def find_similar_memories(
    settings: Settings,
    summary: str,
    limit: Any = None,
    kinds: Sequence[str] | None = None,
    exclude_id: str | None = None,
    min_similarity: float = 0.35,
) -> list[dict[str, Any]]:
    if settings.auth_mode == "data_api":
        return _rest_find_similar_memories(settings, summary, limit, kinds, exclude_id, min_similarity)
    if not summary or not summary.strip():
        return []
    kinds_array = _array(kinds)
    if exclude_id:
        exclude_id = _validate_uuid(str(exclude_id), "exclude_id")
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        rows = _fetch_all(
            conn,
            """
            select id, kind, status, title, summary, similarity, confidence, updated_at
            from memory.find_similar_memories(%s, %s, %s, %s::memory.memory_kind[], %s::uuid, %s)
            """,
            (tenant_id, summary, _limit(settings, limit), kinds_array, exclude_id, min_similarity),
        )
        conn.commit()
        return rows


def list_table(settings: Settings, table: str, filters: Mapping[str, Any] | None = None, limit: Any = None) -> list[dict[str, Any]]:
    if settings.auth_mode == "data_api":
        return _rest_list_table(settings, table, filters, limit)
    if table not in READ_TABLES:
        raise ValueError(f"Unsupported table: {table}")
    filters = filters or {}
    unknown = set(filters) - TABLE_COLUMNS[table]
    if unknown:
        raise ValueError(f"Unsupported filter column(s) for {table}: {', '.join(sorted(unknown))}")
    columns = ", ".join(TABLE_COLUMNS[table])
    where = []
    params: list[Any] = []
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        if table == "tenants":
            where.append("id = %s")
            params.append(tenant_id)
        elif table in TENANT_TABLES:
            where.append("tenant_id = %s")
            params.append(tenant_id)
        for key, value in filters.items():
            where.append(f"{key} = %s")
            params.append(value)
        sql = f"select {columns} from {READ_TABLES[table]} where {' and '.join(where)} order by created_at desc limit %s"
        params.append(_limit(settings, limit))
        rows = _fetch_all(conn, sql, params)
        conn.commit()
        return rows


def create_memory(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_create_memory(settings, payload)
    if not settings.writes_enabled:
        raise ConfigError("Memory writes are disabled.")
    kind = str(payload.get("kind") or "semantic")
    status = str(payload.get("status") or settings.default_status)
    if kind not in MEMORY_KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")
    if status not in RECORD_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    summary = str(payload.get("summary") or "").strip()
    body_text = _plain_content(payload.get("body")) or ""
    source_text = body_text or summary
    inferred = _summarize_and_tag_subject(settings, source_text, existing_summary=summary)
    summary = str(inferred.get("summary") or summary).strip()
    if not summary:
        raise ValueError("summary is required.")
    tags = _merge_tags(payload.get("tags"), inferred.get("tags") or [], settings.llm_subject_enrichment_max_tags)
    subject_payload = payload.get("subject") or _subject_from_display(str(inferred.get("subject_display") or ""))
    embedding_value = _resolve_embedding(
        settings,
        payload.get("embedding"),
        "\n\n".join(part for part in (payload.get("title"), summary, payload.get("body")) if part),
    )
    body = payload.get("body")
    if body and len(str(body)) > settings.max_body_chars:
        raise ValueError(f"body exceeds max_body_chars ({settings.max_body_chars}).")

    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        actor_id = get_or_create_actor(conn, settings, tenant_id)
        subject_id = get_or_create_subject(conn, settings, tenant_id, subject_payload)
        row = _fetch_one(
            conn,
            """
            insert into memory.memory_items (
              tenant_id, subject_id, kind, status, title, summary, body, facts, tags,
              embedding, importance, confidence, source_ref, metadata, created_by_actor_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::text[],
                    %s::extensions.vector, coalesce(%s, 0.5), coalesce(%s, 0.5), %s, %s::jsonb, %s)
            returning id, tenant_id, kind, status, title, summary, confidence, created_at
            """,
            (
                tenant_id,
                subject_id,
                kind,
                status,
                payload.get("title"),
                summary,
                body,
                _json(payload.get("facts") or {}),
                _array(tags) or [],
                embedding_value,
                payload.get("importance"),
                payload.get("confidence"),
                payload.get("source_ref"),
                _json(
                    _metadata_with_enrichment(
                        payload.get("metadata"),
                        settings.llm_subject_enrichment_model,
                        settings.llm_subject_enrichment_enabled,
                    )
                ),
                actor_id,
            ),
        )
        memory_item_id = str(row["id"])
        if kind == "semantic":
            details = payload.get("semantic") or payload.get("details") or {}
            _fetch_one(
                conn,
                """
                insert into memory.semantic_memories (
                  memory_item_id, tenant_id, concept_key, statement, qualifiers
                )
                values (%s, %s, %s, %s, %s::jsonb)
                returning memory_item_id
                """,
                (
                    memory_item_id,
                    tenant_id,
                    details.get("concept_key"),
                    details.get("statement") or summary,
                    _json(details.get("qualifiers") or {}),
                ),
            )
        elif kind == "episodic":
            details = payload.get("episodic") or payload.get("details") or {}
            _fetch_one(
                conn,
                """
                insert into memory.episodic_memories (
                  memory_item_id, tenant_id, happened_at, conversation_ref, location, participants, outcome, emotion
                )
                values (%s, %s, coalesce(%s::timestamptz, now()), %s, %s, %s::uuid[], %s, %s::jsonb)
                returning memory_item_id
                """,
                (
                    memory_item_id,
                    tenant_id,
                    details.get("happened_at"),
                    details.get("conversation_ref"),
                    details.get("location"),
                    details.get("participants") or [],
                    details.get("outcome"),
                    _json(details.get("emotion") or {}),
                ),
            )
        for evidence in payload.get("evidence") or []:
            _fetch_one(
                conn,
                """
                insert into memory.memory_evidence (
                  tenant_id, memory_item_id, run_id, run_message_id, tool_execution_id,
                  knowledge_chunk_id, external_ref, quote, support_score, metadata
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, 1), %s::jsonb)
                returning id
                """,
                (
                    tenant_id,
                    memory_item_id,
                    evidence.get("run_id"),
                    evidence.get("run_message_id"),
                    evidence.get("tool_execution_id"),
                    evidence.get("knowledge_chunk_id"),
                    evidence.get("external_ref") or "agent_zero",
                    evidence.get("quote"),
                    evidence.get("support_score"),
                    _json(evidence.get("metadata") or {}),
                ),
            )
        conn.commit()
        return row


def upsert_knowledge_document(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_upsert_knowledge_document(settings, payload)
    if not settings.writes_enabled:
        raise ConfigError("Knowledge writes are disabled.")
    document_key = str(payload.get("document_key") or "").strip()
    title = str(payload.get("title") or "").strip()
    content_text, content_json = _content_fields(payload.get("content_text"), payload.get("content_json"))
    if not document_key or not title:
        raise ValueError("document_key and title are required.")
    if content_text and len(content_text) > settings.max_document_chars:
        raise ValueError(f"content_text exceeds max_document_chars ({settings.max_document_chars}).")

    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        source = payload.get("source") or {}
        source_id = None
        if source:
            source_key = str(source.get("source_key") or source.get("key") or "agent-zero").strip()
            source_row = _fetch_one(
                conn,
                """
                insert into memory.knowledge_sources (
                  tenant_id, source_key, source_type, uri, trust_level, metadata
                )
                values (%s, %s, %s, %s, coalesce(%s, 50), %s::jsonb)
                on conflict (tenant_id, source_key)
                do update set source_type = excluded.source_type,
                              uri = coalesce(excluded.uri, memory.knowledge_sources.uri),
                              trust_level = excluded.trust_level,
                              metadata = memory.knowledge_sources.metadata || excluded.metadata,
                              updated_at = now()
                returning id
                """,
                (
                    tenant_id,
                    source_key,
                    str(source.get("source_type") or source.get("type") or "agent"),
                    source.get("uri"),
                    source.get("trust_level"),
                    _json(source.get("metadata") or {}),
                ),
            )
            source_id = str(source_row["id"])
        doc = _fetch_one(
            conn,
            """
            insert into memory.knowledge_documents (
              tenant_id, source_id, document_key, title, uri, content_text, content_json, status, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
            on conflict (tenant_id, document_key)
            do update set source_id = excluded.source_id,
                          title = excluded.title,
                          uri = excluded.uri,
                          content_text = excluded.content_text,
                          content_json = excluded.content_json,
                          status = excluded.status,
                          metadata = excluded.metadata,
                          updated_at = now(),
                          archived_at = null
            returning id, tenant_id, document_key, title, status, updated_at
            """,
            (
                tenant_id,
                source_id,
                document_key,
                title,
                payload.get("uri"),
                content_text or None,
                _json(content_json) if content_json is not None else None,
                str(payload.get("status") or "active"),
                _json(payload.get("metadata") or {"created_by": "memory_knowledge"}),
            ),
        )
        document_id = str(doc["id"])
        chunks = payload.get("chunks")
        if chunks is None and content_text:
            chunks = [{"chunk_index": 0, "content_text": content_text, "heading": title, "tags": payload.get("tags") or []}]
        if payload.get("replace_chunks", True):
            with conn.cursor() as cur:
                cur.execute("delete from memory.knowledge_chunks where document_id = %s", (document_id,))
        inserted_chunks = 0
        for index, chunk in enumerate(chunks or []):
            chunk_text, chunk_json = _content_fields(chunk.get("content_text"), chunk.get("content_json"))
            chunk_embedding = _resolve_embedding(
                settings,
                chunk.get("embedding"),
                "\n\n".join(part for part in (chunk.get("heading"), chunk_text) if part),
            )
            _fetch_one(
                conn,
                """
                insert into memory.knowledge_chunks (
                  tenant_id, document_id, chunk_index, heading, content_text, content_json, token_count, embedding, tags, metadata
                )
                values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::extensions.vector, %s::text[], %s::jsonb)
                on conflict (document_id, chunk_index)
                do update set heading = excluded.heading,
                              content_text = excluded.content_text,
                              content_json = excluded.content_json,
                              token_count = excluded.token_count,
                              embedding = coalesce(excluded.embedding, memory.knowledge_chunks.embedding),
                              tags = excluded.tags,
                              metadata = excluded.metadata,
                              updated_at = now(),
                              archived_at = null
                returning id
                """,
                (
                    tenant_id,
                    document_id,
                    int(chunk.get("chunk_index", index)),
                    chunk.get("heading"),
                    chunk_text or "",
                    _json(chunk_json) if chunk_json is not None else None,
                    chunk.get("token_count"),
                    chunk_embedding,
                    _array(chunk.get("tags") or []) or [],
                    _json(chunk.get("metadata") or {}),
                ),
            )
            inserted_chunks += 1
        conn.commit()
        return {**doc, "chunks_written": inserted_chunks}


def log_run(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_log_run(settings, payload)
    if not settings.writes_enabled:
        raise ConfigError("Run logging is disabled.")
    operation = str(payload.get("operation") or "").strip()
    if not operation:
        raise ValueError("operation is required.")
    status = str(payload.get("status") or "running")
    if status not in RUN_STATUSES:
        raise ValueError(f"Unsupported run status: {status}")
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        actor_id = get_or_create_actor(conn, settings, tenant_id)
        row = _fetch_one(
            conn,
            """
            insert into memory.run_history (
              tenant_id, run_key, parent_run_id, actor_id, agent_name, model, operation, status,
              input_text, input_payload, response_text, response_payload, response_output,
              reasoning_summary, thoughts_payload, prompt_tokens, completion_tokens, total_tokens,
              ended_at, duration_ms, error_code, error_message, metadata
            )
            values (
              %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s::jsonb, %s, %s::jsonb, %s::jsonb,
              %s, %s::jsonb, %s, %s, %s,
              case when %s::memory.run_status in ('succeeded', 'failed', 'cancelled') then now() else null end,
              %s, %s, %s, %s::jsonb
            )
            on conflict (tenant_id, run_key)
            do update set status = excluded.status,
                          response_text = coalesce(excluded.response_text, memory.run_history.response_text),
                          response_payload = coalesce(excluded.response_payload, memory.run_history.response_payload),
                          response_output = coalesce(excluded.response_output, memory.run_history.response_output),
                          reasoning_summary = coalesce(excluded.reasoning_summary, memory.run_history.reasoning_summary),
                          ended_at = coalesce(excluded.ended_at, memory.run_history.ended_at),
                          duration_ms = coalesce(excluded.duration_ms, memory.run_history.duration_ms),
                          error_code = coalesce(excluded.error_code, memory.run_history.error_code),
                          error_message = coalesce(excluded.error_message, memory.run_history.error_message),
                          metadata = memory.run_history.metadata || excluded.metadata,
                          updated_at = now()
            returning id, tenant_id, run_key, operation, status, started_at, ended_at
            """,
            (
                tenant_id,
                payload.get("run_key"),
                payload.get("parent_run_id"),
                actor_id,
                payload.get("agent_name") or settings.agent_display_name,
                payload.get("model"),
                operation,
                status,
                payload.get("input_text"),
                _json(payload.get("input_payload") or {}),
                payload.get("response_text"),
                _json(payload.get("response_payload")) if payload.get("response_payload") is not None else None,
                _json(payload.get("response_output")) if payload.get("response_output") is not None else None,
                payload.get("reasoning_summary"),
                _json(payload.get("thoughts_payload") or []),
                payload.get("prompt_tokens"),
                payload.get("completion_tokens"),
                payload.get("total_tokens"),
                status,
                payload.get("duration_ms"),
                payload.get("error_code"),
                payload.get("error_message"),
                _json(payload.get("metadata") or {"created_by": "memory_knowledge"}),
            ),
        )
        conn.commit()
        return row


def log_run_message(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_log_run_message(settings, payload)
    if not settings.writes_enabled:
        raise ConfigError("Run logging is disabled.")
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    role = str(payload.get("role") or "")
    if role not in MESSAGE_ROLES:
        raise ValueError(f"Unsupported message role: {role}")
    content_text, content_json = _content_fields(payload.get("content_text"), payload.get("content_json"))
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        row = _fetch_one(
            conn,
            """
            insert into memory.run_messages (
              tenant_id, run_id, role, ordinal, content_text, content_json, token_count, metadata
            )
            values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
            on conflict (run_id, ordinal)
            do update set content_text = excluded.content_text,
                          content_json = excluded.content_json,
                          token_count = excluded.token_count,
                          metadata = excluded.metadata
            returning id, run_id, role, ordinal, created_at
            """,
            (
                tenant_id,
                run_id,
                role,
                int(payload.get("ordinal") or 1),
                content_text,
                _json(content_json) if content_json is not None else None,
                payload.get("token_count"),
                _json(payload.get("metadata") or {}),
            ),
        )
        conn.commit()
        return row


def log_run_step(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_log_run_step(settings, payload)
    if not settings.writes_enabled:
        raise ConfigError("Run logging is disabled.")
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    status = str(payload.get("status") or "running")
    if status not in RUN_STATUSES:
        raise ValueError(f"Unsupported run status: {status}")
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        row = _fetch_one(
            conn,
            """
            insert into memory.run_steps (
              tenant_id, run_id, parent_step_id, sequence_number, step_type, name, status,
              input_payload, output_payload, ended_at, duration_ms, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    case when %s::memory.run_status in ('succeeded', 'failed', 'cancelled') then now() else null end,
                    %s, %s::jsonb)
            on conflict (run_id, sequence_number)
            do update set step_type = excluded.step_type,
                          name = excluded.name,
                          status = excluded.status,
                          output_payload = excluded.output_payload,
                          ended_at = coalesce(excluded.ended_at, memory.run_steps.ended_at),
                          duration_ms = coalesce(excluded.duration_ms, memory.run_steps.duration_ms),
                          metadata = memory.run_steps.metadata || excluded.metadata
            returning id, run_id, sequence_number, step_type, name, status, started_at, ended_at
            """,
            (
                tenant_id,
                run_id,
                payload.get("parent_step_id"),
                int(payload.get("sequence_number") or 1),
                str(payload.get("step_type") or "agent"),
                str(payload.get("name") or payload.get("step_type") or "step"),
                status,
                _json(payload.get("input_payload") or {}),
                _json(payload.get("output_payload")) if payload.get("output_payload") is not None else None,
                status,
                payload.get("duration_ms"),
                _json(payload.get("metadata") or {}),
            ),
        )
        conn.commit()
        return row


def log_run_thought(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_log_run_thought(settings, payload)
    if not settings.writes_enabled:
        raise ConfigError("Run logging is disabled.")
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    content_text, content_json = _content_fields(
        payload.get("content_text"),
        payload.get("content_json"),
        prefer_thoughts=True,
    )
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        row = _fetch_one(
            conn,
            """
            insert into memory.run_thoughts (
              tenant_id, run_id, step_id, sequence_number, thought_type,
              content_text, content_json, visibility
            )
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            on conflict (run_id, sequence_number)
            do update set step_id = excluded.step_id,
                          thought_type = excluded.thought_type,
                          content_text = excluded.content_text,
                          content_json = excluded.content_json,
                          visibility = excluded.visibility
            returning id, run_id, step_id, sequence_number, thought_type, visibility, created_at
            """,
            (
                tenant_id,
                run_id,
                payload.get("step_id"),
                int(payload.get("sequence_number") or 1),
                str(payload.get("thought_type") or "summary"),
                content_text,
                _json(content_json) if content_json is not None else None,
                str(payload.get("visibility") or "internal"),
            ),
        )
        conn.commit()
        return row


def log_tool_execution(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_log_tool_execution(settings, payload)
    if not settings.writes_enabled:
        raise ConfigError("Tool execution logging is disabled.")
    run_id = _validate_uuid(str(payload.get("run_id") or ""), "run_id")
    tool_name = str(payload.get("tool_name") or "").strip()
    if not tool_name:
        raise ValueError("tool_name is required.")
    status = str(payload.get("status") or "running")
    if status not in RUN_STATUSES:
        raise ValueError(f"Unsupported run status: {status}")
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        row = _fetch_one(
            conn,
            """
            insert into memory.tool_executions (
              tenant_id, run_id, step_id, tool_call_id, tool_name, input_payload,
              output_payload, stdout_text, stderr_text, status, ended_at,
              duration_ms, error_code, error_message, metadata
            )
            values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s,
                    case when %s::memory.run_status in ('succeeded', 'failed', 'cancelled') then now() else null end,
                    %s, %s, %s, %s::jsonb)
            on conflict (tenant_id, run_id, tool_call_id)
            do update set step_id = coalesce(excluded.step_id, memory.tool_executions.step_id),
                          tool_name = excluded.tool_name,
                          input_payload = case
                            when excluded.input_payload = '{}'::jsonb then memory.tool_executions.input_payload
                            else excluded.input_payload
                          end,
                          output_payload = coalesce(excluded.output_payload, memory.tool_executions.output_payload),
                          stdout_text = coalesce(excluded.stdout_text, memory.tool_executions.stdout_text),
                          stderr_text = coalesce(excluded.stderr_text, memory.tool_executions.stderr_text),
                          status = excluded.status,
                          ended_at = coalesce(excluded.ended_at, memory.tool_executions.ended_at),
                          duration_ms = coalesce(excluded.duration_ms, memory.tool_executions.duration_ms),
                          error_code = coalesce(excluded.error_code, memory.tool_executions.error_code),
                          error_message = coalesce(excluded.error_message, memory.tool_executions.error_message),
                          metadata = memory.tool_executions.metadata || excluded.metadata
            returning id, run_id, step_id, tool_call_id, tool_name, status, started_at, ended_at
            """,
            (
                tenant_id,
                run_id,
                payload.get("step_id"),
                payload.get("tool_call_id"),
                tool_name,
                _json(payload.get("input_payload") or {}),
                _json(payload.get("output_payload")) if payload.get("output_payload") is not None else None,
                payload.get("stdout_text"),
                payload.get("stderr_text"),
                status,
                status,
                payload.get("duration_ms"),
                payload.get("error_code"),
                payload.get("error_message"),
                _json(payload.get("metadata") or {}),
            ),
        )
        conn.commit()
        return row


def upsert_subject(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_upsert_subject(settings, payload)
    subject_key = str(payload.get("subject_key") or payload.get("key") or "").strip()
    if not subject_key:
        raise ValueError("subject_key is required.")
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        row = _fetch_one(
            conn,
            """
            insert into memory.subjects (
              tenant_id, subject_key, subject_type, display_name, aliases, attributes
            )
            values (%s, %s, %s, %s, %s::text[], %s::jsonb)
            on conflict (tenant_id, subject_key)
            do update set subject_type = excluded.subject_type,
                          display_name = coalesce(excluded.display_name, memory.subjects.display_name),
                          aliases = excluded.aliases,
                          attributes = memory.subjects.attributes || excluded.attributes,
                          updated_at = now()
            returning id, tenant_id, subject_key, subject_type, display_name, updated_at
            """,
            (
                tenant_id,
                subject_key,
                str(payload.get("subject_type") or payload.get("type") or "general"),
                payload.get("display_name"),
                _array(payload.get("aliases") or []) or [],
                _json(payload.get("attributes") or {}),
            ),
        )
        conn.commit()
        return row


def add_memory_evidence(settings: Settings, payload: Mapping[str, Any]) -> dict[str, Any]:
    if settings.auth_mode == "data_api":
        return _rest_add_memory_evidence(settings, payload)
    memory_item_id = _validate_uuid(str(payload.get("memory_item_id") or ""), "memory_item_id")
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=True)
        row = _fetch_one(
            conn,
            """
            insert into memory.memory_evidence (
              tenant_id, memory_item_id, run_id, run_message_id, tool_execution_id,
              knowledge_chunk_id, external_ref, quote, support_score, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, 1), %s::jsonb)
            returning id, tenant_id, memory_item_id, created_at
            """,
            (
                tenant_id,
                memory_item_id,
                payload.get("run_id"),
                payload.get("run_message_id"),
                payload.get("tool_execution_id"),
                payload.get("knowledge_chunk_id"),
                payload.get("external_ref"),
                payload.get("quote"),
                payload.get("support_score"),
                _json(payload.get("metadata") or {}),
            ),
        )
        conn.commit()
        return row


def promote_memory(settings: Settings, memory_item_id: str, reason: str | None = None) -> dict[str, Any]:
    memory_item_id = _validate_uuid(str(memory_item_id or ""), "memory_item_id")
    if settings.auth_mode == "data_api":
        return _rest_promote_memory(settings, memory_item_id, reason)
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        row = _fetch_one(
            conn,
            """
            update memory.memory_items
            set status = 'active',
                metadata = metadata || %s::jsonb,
                updated_at = now()
            where id = %s
              and tenant_id = %s
              and status = 'candidate'
              and archived_at is null
            returning id, tenant_id, kind, status, title, summary, updated_at
            """,
            (
                _json(
                    {
                        "promotion": {
                            "mode": "automatic",
                            "reason": reason or "confidence_threshold",
                            "promoted_at": _now_iso(),
                        }
                    }
                ),
                memory_item_id,
                tenant_id,
            ),
        )
        conn.commit()
        return row or {}


def set_memory_status(settings: Settings, memory_item_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
    memory_item_id = _validate_uuid(str(memory_item_id or ""), "memory_item_id")
    if status not in RECORD_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    if settings.auth_mode == "data_api":
        return _rest_set_memory_status(settings, memory_item_id, status, reason)
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        row = _fetch_one(
            conn,
            """
            update memory.memory_items
            set status = %s::memory.record_status,
                metadata = metadata || %s::jsonb,
                updated_at = now()
            where id = %s
              and tenant_id = %s
              and archived_at is null
            returning id, tenant_id, kind, status, title, summary, confidence, updated_at
            """,
            (
                status,
                _json({"status_change": {"status": status, "reason": reason or "manual", "changed_at": _now_iso()}}),
                memory_item_id,
                tenant_id,
            ),
        )
        conn.commit()
        return row or {}


def adjust_memory_confidence(
    settings: Settings,
    memory_item_id: str,
    delta: float,
    reason: str | None = None,
    support_score: float | None = None,
) -> dict[str, Any]:
    memory_item_id = _validate_uuid(str(memory_item_id or ""), "memory_item_id")
    if settings.auth_mode == "data_api":
        return _rest_adjust_memory_confidence(settings, memory_item_id, delta, reason, support_score)
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        row = _fetch_one(
            conn,
            """
            update memory.memory_items
            set confidence = least(1, greatest(0, confidence + %s)),
                metadata = jsonb_set(
                  metadata,
                  '{confidence_events}',
                  coalesce(metadata->'confidence_events', '[]'::jsonb) || %s::jsonb,
                  true
                ),
                updated_at = now()
            where id = %s
              and tenant_id = %s
              and archived_at is null
            returning id, tenant_id, kind, status, title, summary, confidence, updated_at
            """,
            (
                float(delta),
                _json(
                    [
                        {
                            "delta": float(delta),
                            "reason": reason or "adjustment",
                            "support_score": support_score,
                            "at": _now_iso(),
                        }
                    ]
                ),
                memory_item_id,
                tenant_id,
            ),
        )
        conn.commit()
        return row or {}


def record_memory_access(settings: Settings, memory_item_ids: Sequence[str], reason: str | None = None) -> list[dict[str, Any]]:
    ids = [_validate_uuid(str(memory_id), "memory_item_id") for memory_id in memory_item_ids if memory_id]
    if not ids:
        return []
    if settings.auth_mode == "data_api":
        return _rest_record_memory_access(settings, ids, reason)
    with connect(settings) as conn:
        tenant_id = ensure_tenant(conn, settings, create=False)
        rows = _fetch_all(
            conn,
            """
            update memory.memory_items
            set access_count = access_count + 1,
                last_accessed_at = now(),
                confidence = case
                  when last_accessed_at is null or last_accessed_at < now() - (%s::text)::interval
                    then least(1, greatest(0, confidence + %s))
                  else confidence
                end,
                metadata = case
                  when last_accessed_at is null or last_accessed_at < now() - (%s::text)::interval
                    then jsonb_set(
                      metadata,
                      '{confidence_events}',
                      coalesce(metadata->'confidence_events', '[]'::jsonb) || %s::jsonb,
                      true
                    )
                  else metadata
                end,
                updated_at = now()
            where tenant_id = %s
              and id = any(%s::uuid[])
              and archived_at is null
            returning id, tenant_id, kind, status, title, summary, confidence, access_count, last_accessed_at
            """,
            (
                f"{max(0, settings.memory_access_min_interval_minutes)} minutes",
                settings.memory_access_confidence_delta,
                f"{max(0, settings.memory_access_min_interval_minutes)} minutes",
                _json(
                    [
                        {
                            "delta": settings.memory_access_confidence_delta,
                            "reason": reason or "retrieval_access",
                            "at": _now_iso(),
                        }
                    ]
                ),
                tenant_id,
                ids,
            ),
        )
        conn.commit()
        return rows

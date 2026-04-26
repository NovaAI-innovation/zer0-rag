from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row


MEMORY_KINDS = {
    "working",
    "episodic",
    "semantic",
    "procedural",
    "preference",
    "profile",
    "artifact",
    "graph",
}


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing."""


@dataclass(frozen=True)
class MemorySettings:
    database_url: str
    tenant_id: str
    agent_key: str
    plugin_key: str
    plugin_version: str = "0.1.0"
    retrieval_enabled: bool = True
    writes_enabled: bool = True
    record_conversation: bool = True
    extract_candidates: bool = True
    auto_promote: bool = False
    default_limit: int = 10
    similarity_threshold: float = 0.7
    max_summary_chars: int = 1800


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _cfg(config: dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def load_settings(config: dict[str, Any] | None = None) -> MemorySettings:
    load_dotenv()
    config = config or {}
    database_url = (
        _cfg(config, "database", "url")
        or os.environ.get(_cfg(config, "database", "url_env", default="MEMORY_DATABASE_URL"))
        or os.environ.get("MEMORY_DATABASE_URL")
        or os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("DATABASE_URL")
    )
    tenant_id = (
        _cfg(config, "memory", "tenant_id")
        or os.environ.get(_cfg(config, "memory", "tenant_id_env", default="MEMORY_TENANT_ID"))
        or os.environ.get("MEMORY_TENANT_ID")
    )
    if not database_url:
        raise ConfigError("Set MEMORY_DATABASE_URL, SUPABASE_DB_URL, or DATABASE_URL.")
    if not tenant_id:
        raise ConfigError("Set MEMORY_TENANT_ID or configure memory.tenant_id.")

    return MemorySettings(
        database_url=database_url,
        tenant_id=tenant_id,
        agent_key=_cfg(config, "memory", "agent_key") or os.environ.get("MEMORY_AGENT_KEY", "agent0:zero"),
        plugin_key=_cfg(config, "memory", "plugin_key") or os.environ.get("MEMORY_PLUGIN_KEY", "local.memory-knowledge"),
        plugin_version=str(_cfg(config, "memory", "plugin_version", default="0.1.0")),
        retrieval_enabled=bool(_cfg(config, "retrieval", "enabled", default=True)),
        writes_enabled=bool(_cfg(config, "writes", "enabled", default=True)),
        record_conversation=bool(_cfg(config, "writes", "record_conversation", default=True)),
        extract_candidates=bool(_cfg(config, "writes", "extract_candidates", default=True)),
        auto_promote=bool(_cfg(config, "writes", "auto_promote", default=False)),
        default_limit=int(_cfg(config, "retrieval", "default_limit", default=10)),
        similarity_threshold=float(_cfg(config, "retrieval", "similarity_threshold", default=0.7)),
        max_summary_chars=int(_cfg(config, "retrieval", "max_summary_chars", default=1800)),
    )


@contextmanager
def connect(settings: MemorySettings) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def fetch_all(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn: psycopg.Connection[dict[str, Any]], sql: str, params: Sequence[Any] = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def vector_literal(values: Sequence[float], dimensions: int = 384) -> str:
    if len(values) != dimensions:
        raise ValueError(f"Expected {dimensions} embedding values, got {len(values)}.")
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def load_context(settings: MemorySettings) -> dict[str, Any] | None:
    with connect(settings) as conn:
        return fetch_one(
            conn,
            """
            select *
            from memory.agent_memory_context
            where tenant_id = %s
              and agent_key = %s
              and plugin_key = %s
            limit 1
            """,
            (settings.tenant_id, settings.agent_key, settings.plugin_key),
        )


def search_text(settings: MemorySettings, query: str, limit: int, kinds: list[str] | None = None) -> list[dict[str, Any]]:
    with connect(settings) as conn:
        return fetch_all(
            conn,
            """
            select *
            from memory.search_memory_text(%s, %s, %s, %s::memory.memory_kind[])
            """,
            (settings.tenant_id, query, limit, kinds),
        )


def search_vector(
    settings: MemorySettings,
    embedding: Sequence[float],
    limit: int,
    threshold: float,
    kinds: list[str] | None = None,
    required_tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    with connect(settings) as conn:
        return fetch_all(
            conn,
            """
            select *
            from memory.match_memory_items(
              %s,
              %s::vector(384),
              %s,
              %s,
              %s::memory.memory_kind[],
              null,
              %s::text[]
            )
            """,
            (settings.tenant_id, vector_literal(embedding, 384), limit, threshold, kinds, required_tags),
        )


def get_or_create_actor(
    conn: psycopg.Connection[dict[str, Any]],
    settings: MemorySettings,
    kind: str,
    external_ref: str | None,
    display_name: str,
) -> str:
    row = fetch_one(
        conn,
        """
        select id
        from memory.actors
        where tenant_id = %s
          and kind = %s
          and external_ref is not distinct from %s
        limit 1
        """,
        (settings.tenant_id, kind, external_ref),
    )
    if row:
        return str(row["id"])
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into memory.actors (tenant_id, kind, external_ref, display_name)
            values (%s, %s, %s, %s)
            returning id
            """,
            (settings.tenant_id, kind, external_ref, display_name),
        )
        return str(cur.fetchone()["id"])


def start_trace(settings: MemorySettings, operation: str, agent_profile_id: str | None = None) -> str:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into memory.trace_runs (tenant_id, agent_profile_id, operation, status)
                values (%s, %s, %s, 'running')
                returning id
                """,
                (settings.tenant_id, agent_profile_id, operation),
            )
            trace_id = str(cur.fetchone()["id"])
        conn.commit()
        return trace_id


def finish_trace(settings: MemorySettings, trace_run_id: str, status: str, summary: str | None = None) -> None:
    with connect(settings) as conn:
        execute(
            conn,
            """
            update memory.trace_runs
            set status = %s,
                output_summary = %s,
                ended_at = now(),
                duration_ms = greatest(0, floor(extract(epoch from (now() - started_at)) * 1000)::integer)
            where id = %s
            """,
            (status, summary, trace_run_id),
        )
        conn.commit()


def log_diagnostic(
    settings: MemorySettings,
    level: str,
    category: str,
    code: str,
    message: str,
    remediation: str | None = None,
) -> None:
    with connect(settings) as conn:
        execute(
            conn,
            """
            insert into memory.diagnostic_logs (tenant_id, level, category, code, message, remediation)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (settings.tenant_id, level, category, code, message, remediation),
        )
        conn.commit()


def record_conversation(
    settings: MemorySettings,
    agent_profile_id: str | None,
    external_thread_id: str,
    messages: list[dict[str, Any]],
    title: str | None = None,
) -> dict[str, Any]:
    with connect(settings) as conn:
        actor_id = get_or_create_actor(conn, settings, "agent", settings.agent_key, "Agent Zero")
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into memory.conversations (
                  tenant_id, agent_profile_id, started_by_actor_id, external_thread_id, title, status, metadata
                )
                values (%s, %s, %s, %s, %s, 'open', '{}'::jsonb)
                on conflict (tenant_id, external_thread_id)
                do update set title = coalesce(excluded.title, memory.conversations.title),
                              updated_at = now()
                returning id
                """,
                (settings.tenant_id, agent_profile_id, actor_id, external_thread_id, title),
            )
            conversation_id = str(cur.fetchone()["id"])
            inserted = 0
            for idx, message in enumerate(messages, start=1):
                ordinal = int(message.get("ordinal") or idx)
                cur.execute(
                    """
                    insert into memory.conversation_messages (
                      tenant_id, conversation_id, role, ordinal, content_text, content_json, metadata
                    )
                    values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    on conflict (conversation_id, ordinal) do nothing
                    returning id
                    """,
                    (
                        settings.tenant_id,
                        conversation_id,
                        message["role"],
                        ordinal,
                        message["content_text"],
                        dump_json(message.get("content_json")) if message.get("content_json") is not None else None,
                        dump_json(message.get("metadata", {})),
                    ),
                )
                if cur.fetchone():
                    inserted += 1
        conn.commit()
        return {"conversation_id": conversation_id, "messages_inserted": inserted}


def upsert_working_memory(
    settings: MemorySettings,
    agent_profile_id: str | None,
    key: str,
    value: dict[str, Any],
    priority: int = 100,
    conversation_id: str | None = None,
) -> str:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into memory.working_memory_items (
                  tenant_id, agent_profile_id, conversation_id, key, value, priority
                )
                values (%s, %s, %s, %s, %s::jsonb, %s)
                on conflict (tenant_id, agent_profile_id, conversation_id, key)
                do update set value = excluded.value,
                              priority = excluded.priority,
                              updated_at = now()
                returning id
                """,
                (settings.tenant_id, agent_profile_id, conversation_id, key, dump_json(value), priority),
            )
            item_id = str(cur.fetchone()["id"])
        conn.commit()
        return item_id


def create_memory_item(settings: MemorySettings, payload: dict[str, Any]) -> str:
    kind = payload["kind"]
    if kind not in MEMORY_KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")
    with connect(settings) as conn:
        actor_id = get_or_create_actor(conn, settings, "agent", settings.agent_key, "Agent Zero")
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into memory.memory_items (
                  tenant_id, kind, status, visibility, title, summary, body, facts, tags,
                  importance, confidence, metadata, created_by_actor_id
                )
                values (
                  %s, %s, coalesce(%s::memory.memory_status, 'candidate'),
                  coalesce(%s::memory.memory_visibility, 'tenant'), %s, %s, %s,
                  %s::jsonb, coalesce(%s::text[], array[]::text[]), coalesce(%s, 0.5),
                  coalesce(%s, 0.5), %s::jsonb, %s
                )
                returning id
                """,
                (
                    settings.tenant_id,
                    kind,
                    payload.get("status"),
                    payload.get("visibility"),
                    payload.get("title"),
                    payload["summary"],
                    payload.get("body"),
                    dump_json(payload.get("facts", {})),
                    payload.get("tags", []),
                    payload.get("importance"),
                    payload.get("confidence"),
                    dump_json(payload.get("metadata", {})),
                    actor_id,
                ),
            )
            memory_item_id = str(cur.fetchone()["id"])
            if kind == "semantic":
                details = payload.get("details", {})
                cur.execute(
                    """
                    insert into memory.semantic_memories (
                      memory_item_id, tenant_id, concept_key, statement, qualifiers, metadata
                    )
                    values (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        memory_item_id,
                        settings.tenant_id,
                        details.get("concept_key"),
                        details.get("statement", payload["summary"]),
                        dump_json(details.get("qualifiers", {})),
                        dump_json(details.get("metadata", {})),
                    ),
                )
            for idx, evidence in enumerate(payload.get("evidence", []), start=1):
                cur.execute(
                    """
                    insert into memory.memory_evidence_links (
                      tenant_id, memory_item_id, evidence_kind, conversation_message_id,
                      external_ref, quote, support_score, metadata
                    )
                    values (%s, %s, %s, %s, %s, %s, coalesce(%s, 1), %s::jsonb)
                    """,
                    (
                        settings.tenant_id,
                        memory_item_id,
                        evidence.get("evidence_kind", "conversation"),
                        evidence.get("conversation_message_id"),
                        evidence.get("external_ref"),
                        evidence.get("quote"),
                        evidence.get("support_score"),
                        dump_json(evidence.get("metadata", {"sequence": idx})),
                    ),
                )
        conn.commit()
        return memory_item_id


def promote_memory(settings: MemorySettings, memory_item_id: str, reason: str) -> dict[str, Any]:
    with connect(settings) as conn:
        current = fetch_one(
            conn,
            "select to_jsonb(mi.*) as snapshot from memory.memory_items mi where tenant_id = %s and id = %s",
            (settings.tenant_id, memory_item_id),
        )
        if not current:
            raise ValueError("Memory item not found.")
        revision = fetch_one(
            conn,
            "select coalesce(max(revision_number), 0) + 1 as next_revision from memory.memory_revisions where memory_item_id = %s",
            (memory_item_id,),
        )["next_revision"]
        with conn.cursor() as cur:
            cur.execute(
                """
                update memory.memory_items
                set status = 'active', updated_at = now()
                where tenant_id = %s and id = %s
                returning to_jsonb(memory.memory_items.*) as snapshot
                """,
                (settings.tenant_id, memory_item_id),
            )
            updated = cur.fetchone()
            cur.execute(
                """
                insert into memory.memory_revisions (
                  tenant_id, memory_item_id, revision_number, change_reason,
                  previous_snapshot, new_snapshot
                )
                values (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (settings.tenant_id, memory_item_id, revision, reason, dump_json(current["snapshot"]), dump_json(updated["snapshot"])),
            )
        conn.commit()
        return {"memory_item_id": memory_item_id, "revision_number": revision, "status": "active"}


def health(settings: MemorySettings) -> dict[str, Any]:
    with connect(settings) as conn:
        return {
            "context": load_context(settings),
            "tables": fetch_one(
                conn,
                """
                select
                  count(*) filter (where table_type = 'BASE TABLE') as base_tables,
                  count(*) filter (where table_type = 'VIEW') as views
                from information_schema.tables
                where table_schema = 'memory'
                """,
            ),
            "active_memory_items": fetch_one(
                conn,
                """
                select count(*) as count
                from memory.memory_items
                where tenant_id = %s
                  and status = 'active'
                  and archived_at is null
                """,
                (settings.tenant_id,),
            ),
            "candidate_memory_items": fetch_one(
                conn,
                """
                select count(*) as count
                from memory.memory_items
                where tenant_id = %s
                  and status = 'candidate'
                  and archived_at is null
                """,
                (settings.tenant_id,),
            ),
            "unresolved_diagnostics": fetch_one(
                conn,
                """
                select count(*) as count
                from memory.diagnostic_logs
                where tenant_id = %s
                  and resolved_at is null
                  and level in ('warning', 'error', 'critical')
                """,
                (settings.tenant_id,),
            ),
        }


def diagnostics(settings: MemorySettings, limit: int = 50) -> list[dict[str, Any]]:
    with connect(settings) as conn:
        return fetch_all(
            conn,
            """
            select id, level, category, code, message, remediation, occurred_at, resolved_at
            from memory.diagnostic_logs
            where tenant_id = %s
              and resolved_at is null
              and level in ('warning', 'error', 'critical')
            order by occurred_at desc
            limit %s
            """,
            (settings.tenant_id, limit),
        )

from __future__ import annotations

import argparse
import os
from typing import Any

from memory_db.db import connect, dump_json, parse_json_file, require_uuid, vector_literal


MEMORY_KINDS_WITH_DETAILS = {"episodic", "semantic", "procedural"}


def get_or_create_actor(conn, tenant_id: str, kind: str, external_ref: str | None, display_name: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            select id
            from memory.actors
            where tenant_id = %s
              and kind = %s
              and external_ref is not distinct from %s
            limit 1
            """,
            (tenant_id, kind, external_ref),
        )
        row = cur.fetchone()
        if row:
            return row["id"]

        cur.execute(
            """
            insert into memory.actors (tenant_id, kind, external_ref, display_name)
            values (%s, %s, %s, %s)
            returning id
            """,
            (tenant_id, kind, external_ref, display_name),
        )
        return cur.fetchone()["id"]


def get_agent_profile_id(conn, tenant_id: str, agent_key: str | None) -> str | None:
    if not agent_key:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            select id
            from memory.agent_profiles
            where tenant_id = %s
              and agent_key = %s
              and archived_at is null
            limit 1
            """,
            (tenant_id, agent_key),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def create_trace_run(conn, tenant_id: str, operation: str, agent_profile_id: str | None = None, conversation_id: str | None = None) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into memory.trace_runs (tenant_id, agent_profile_id, conversation_id, operation, status)
            values (%s, %s, %s, %s, 'running')
            returning id
            """,
            (tenant_id, agent_profile_id, conversation_id, operation),
        )
        return cur.fetchone()["id"]


def finish_trace_run(conn, trace_run_id: str, status: str, output_summary: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            update memory.trace_runs
            set status = %s,
                output_summary = %s,
                ended_at = now(),
                duration_ms = greatest(0, floor(extract(epoch from (now() - started_at)) * 1000)::integer)
            where id = %s
            """,
            (status, output_summary, trace_run_id),
        )


def trace_event(conn, tenant_id: str, trace_run_id: str, name: str, event_type: str, sequence: int, payload: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into memory.trace_events (tenant_id, trace_run_id, event_name, event_type, sequence_number, payload)
            values (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (tenant_id, trace_run_id, name, event_type, sequence, dump_json(payload)),
        )


def cmd_conversation(args: argparse.Namespace) -> dict[str, Any]:
    payload = parse_json_file(args.file)
    tenant_id = require_uuid(args.tenant_id, "tenant id")
    external_thread_id = payload.get("external_thread_id") or args.external_thread_id
    messages = payload.get("messages", [])
    if not messages:
        raise ValueError("Conversation ingest requires a non-empty 'messages' array.")

    with connect() as conn:
        agent_profile_id = get_agent_profile_id(conn, tenant_id, args.agent_key)
        actor_id = None
        if payload.get("started_by"):
            started_by = payload["started_by"]
            actor_id = get_or_create_actor(
                conn,
                tenant_id,
                started_by.get("kind", "human"),
                started_by.get("external_ref"),
                started_by.get("display_name", "Unknown Actor"),
            )

        trace_run_id = create_trace_run(conn, tenant_id, "conversation.record", agent_profile_id)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into memory.conversations (
                      tenant_id, agent_profile_id, started_by_actor_id, external_thread_id, title, status, metadata
                    )
                    values (%s, %s, %s, %s, %s, coalesce(%s::memory.conversation_status, 'open'), %s::jsonb)
                    on conflict (tenant_id, external_thread_id)
                    do update set
                      title = coalesce(excluded.title, memory.conversations.title),
                      metadata = memory.conversations.metadata || excluded.metadata,
                      updated_at = now()
                    returning id
                    """,
                    (
                        tenant_id,
                        agent_profile_id,
                        actor_id,
                        external_thread_id,
                        payload.get("title"),
                        payload.get("status"),
                        dump_json(payload.get("metadata", {})),
                    ),
                )
                conversation_id = cur.fetchone()["id"]

                inserted = 0
                for idx, message in enumerate(messages, start=1):
                    ordinal = message.get("ordinal", idx)
                    message_actor_id = None
                    if message.get("actor"):
                        actor = message["actor"]
                        message_actor_id = get_or_create_actor(
                            conn,
                            tenant_id,
                            actor.get("kind", "human"),
                            actor.get("external_ref"),
                            actor.get("display_name", message.get("role", "actor")),
                        )

                    cur.execute(
                        """
                        insert into memory.conversation_messages (
                          tenant_id, conversation_id, actor_id, role, ordinal, provider_message_id,
                          content_text, content_json, token_count, model, finish_reason, message_at, metadata
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, coalesce(%s::timestamptz, now()), %s::jsonb)
                        on conflict (conversation_id, ordinal) do nothing
                        returning id
                        """,
                        (
                            tenant_id,
                            conversation_id,
                            message_actor_id,
                            message["role"],
                            ordinal,
                            message.get("provider_message_id"),
                            message["content_text"],
                            dump_json(message.get("content_json")) if message.get("content_json") is not None else None,
                            message.get("token_count"),
                            message.get("model"),
                            message.get("finish_reason"),
                            message.get("message_at"),
                            dump_json(message.get("metadata", {})),
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        inserted += 1
                trace_event(conn, tenant_id, trace_run_id, "conversation.recorded", "conversation", 1, {"conversation_id": str(conversation_id), "messages_inserted": inserted})
                finish_trace_run(conn, trace_run_id, "succeeded", f"Inserted {inserted} messages")
            conn.commit()
            return {"conversation_id": str(conversation_id), "messages_inserted": inserted, "trace_run_id": str(trace_run_id)}
        except Exception:
            conn.rollback()
            with connect() as log_conn:
                finish_trace_run(log_conn, trace_run_id, "failed", "Conversation ingest failed")
                log_conn.commit()
            raise


def cmd_memory(args: argparse.Namespace) -> dict[str, Any]:
    payload = parse_json_file(args.file)
    tenant_id = require_uuid(args.tenant_id, "tenant id")
    kind = payload["kind"]
    if kind not in {"working", "episodic", "semantic", "procedural", "preference", "profile", "artifact", "graph"}:
        raise ValueError(f"Unsupported memory kind: {kind}")

    embedding = payload.get("embedding")
    embedding_literal = vector_literal(embedding, 384) if embedding else None

    with connect() as conn:
        agent_profile_id = get_agent_profile_id(conn, tenant_id, args.agent_key)
        actor_id = None
        if payload.get("created_by"):
            actor = payload["created_by"]
            actor_id = get_or_create_actor(
                conn,
                tenant_id,
                actor.get("kind", "agent"),
                actor.get("external_ref"),
                actor.get("display_name", "Agent 0"),
            )

        trace_run_id = create_trace_run(conn, tenant_id, "memory.extract", agent_profile_id)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into memory.memory_items (
                      tenant_id, subject_entity_id, source_id, kind, status, visibility, title,
                      summary, body, facts, tags, embedding, importance, confidence, valid_from,
                      valid_until, metadata, created_by_actor_id
                    )
                    values (
                      %s, %s, %s, %s, coalesce(%s::memory.memory_status, 'candidate'),
                      coalesce(%s::memory.memory_visibility, 'tenant'), %s, %s, %s,
                      %s::jsonb, coalesce(%s::text[], array[]::text[]), %s::vector(384),
                      coalesce(%s, 0.5), coalesce(%s, 0.5), coalesce(%s::timestamptz, now()),
                      %s::timestamptz, %s::jsonb, %s
                    )
                    returning id
                    """,
                    (
                        tenant_id,
                        payload.get("subject_entity_id"),
                        payload.get("source_id"),
                        kind,
                        payload.get("status"),
                        payload.get("visibility"),
                        payload.get("title"),
                        payload["summary"],
                        payload.get("body"),
                        dump_json(payload.get("facts", {})),
                        payload.get("tags", []),
                        embedding_literal,
                        payload.get("importance"),
                        payload.get("confidence"),
                        payload.get("valid_from"),
                        payload.get("valid_until"),
                        dump_json(payload.get("metadata", {})),
                        actor_id,
                    ),
                )
                memory_item_id = cur.fetchone()["id"]

                details = payload.get("details", {})
                if kind == "episodic":
                    cur.execute(
                        """
                        insert into memory.episodic_memories (
                          memory_item_id, tenant_id, conversation_id, happened_at, location,
                          participants, outcome, emotion, metadata
                        )
                        values (%s, %s, %s, coalesce(%s::timestamptz, now()), %s, coalesce(%s::uuid[], array[]::uuid[]), %s, %s::jsonb, %s::jsonb)
                        """,
                        (
                            memory_item_id,
                            tenant_id,
                            details.get("conversation_id"),
                            details.get("happened_at"),
                            details.get("location"),
                            details.get("participants", []),
                            details.get("outcome"),
                            dump_json(details.get("emotion", {})),
                            dump_json(details.get("metadata", {})),
                        ),
                    )
                elif kind == "semantic":
                    cur.execute(
                        """
                        insert into memory.semantic_memories (
                          memory_item_id, tenant_id, concept_key, statement, qualifiers,
                          contradiction_group, metadata
                        )
                        values (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                        """,
                        (
                            memory_item_id,
                            tenant_id,
                            details.get("concept_key"),
                            details.get("statement", payload["summary"]),
                            dump_json(details.get("qualifiers", {})),
                            details.get("contradiction_group"),
                            dump_json(details.get("metadata", {})),
                        ),
                    )
                elif kind == "procedural":
                    cur.execute(
                        """
                        insert into memory.procedural_memories (
                          memory_item_id, tenant_id, procedure_key, trigger_conditions,
                          steps, success_criteria, failure_modes, metadata
                        )
                        values (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                        """,
                        (
                            memory_item_id,
                            tenant_id,
                            details.get("procedure_key"),
                            dump_json(details.get("trigger_conditions", {})),
                            dump_json(details.get("steps", [])),
                            dump_json(details.get("success_criteria", {})),
                            dump_json(details.get("failure_modes", [])),
                            dump_json(details.get("metadata", {})),
                        ),
                    )

                for idx, evidence in enumerate(payload.get("evidence", []), start=1):
                    cur.execute(
                        """
                        insert into memory.memory_evidence_links (
                          tenant_id, memory_item_id, evidence_kind, conversation_message_id,
                          conversation_event_id, trace_event_id, data_source_id, external_ref,
                          quote, support_score, metadata
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, 1), %s::jsonb)
                        """,
                        (
                            tenant_id,
                            memory_item_id,
                            evidence["evidence_kind"],
                            evidence.get("conversation_message_id"),
                            evidence.get("conversation_event_id"),
                            evidence.get("trace_event_id"),
                            evidence.get("data_source_id"),
                            evidence.get("external_ref"),
                            evidence.get("quote"),
                            evidence.get("support_score"),
                            dump_json(evidence.get("metadata", {"sequence": idx})),
                        ),
                    )

                trace_event(conn, tenant_id, trace_run_id, "memory.created", "memory", 1, {"memory_item_id": str(memory_item_id), "kind": kind})
                finish_trace_run(conn, trace_run_id, "succeeded", f"Created memory {memory_item_id}")
            conn.commit()
            return {"memory_item_id": str(memory_item_id), "trace_run_id": str(trace_run_id)}
        except Exception:
            conn.rollback()
            with connect() as log_conn:
                finish_trace_run(log_conn, trace_run_id, "failed", "Memory ingest failed")
                log_conn.commit()
            raise


def cmd_working(args: argparse.Namespace) -> dict[str, Any]:
    payload = parse_json_file(args.file)
    tenant_id = require_uuid(args.tenant_id, "tenant id")
    with connect() as conn:
        agent_profile_id = payload.get("agent_profile_id") or get_agent_profile_id(conn, tenant_id, args.agent_key)
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into memory.working_memory_items (
                  tenant_id, agent_profile_id, conversation_id, key, value, priority, expires_at
                )
                values (%s, %s, %s, %s, %s::jsonb, coalesce(%s, 100), %s::timestamptz)
                on conflict (tenant_id, agent_profile_id, conversation_id, key)
                do update set value = excluded.value,
                              priority = excluded.priority,
                              expires_at = excluded.expires_at,
                              updated_at = now()
                returning id
                """,
                (
                    tenant_id,
                    agent_profile_id,
                    payload.get("conversation_id"),
                    payload["key"],
                    dump_json(payload["value"]),
                    payload.get("priority"),
                    payload.get("expires_at"),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return {"working_memory_item_id": str(row["id"])}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest data into the Agent 0 memory database.")
    parser.add_argument("--tenant-id", default=os.environ.get("MEMORY_TENANT_ID"))
    parser.add_argument("--agent-key", default=os.environ.get("MEMORY_AGENT_KEY"))
    sub = parser.add_subparsers(dest="command", required=True)

    conversation = sub.add_parser("conversation", help="Ingest a conversation JSON file.")
    conversation.add_argument("file")
    conversation.add_argument("--external-thread-id")
    conversation.set_defaults(func=cmd_conversation)

    memory = sub.add_parser("memory", help="Ingest a memory item JSON file.")
    memory.add_argument("file")
    memory.set_defaults(func=cmd_memory)

    working = sub.add_parser("working", help="Upsert a working memory JSON file.")
    working.add_argument("file")
    working.set_defaults(func=cmd_working)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = args.func(args)
    print(dump_json(result))


if __name__ == "__main__":
    main()

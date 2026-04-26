from __future__ import annotations

import argparse
import os
from typing import Any

from memory_db.db import connect, dump_json, parse_json_file, require_uuid, vector_literal


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id", default=os.environ.get("MEMORY_TENANT_ID"))
    parser.add_argument("--limit", type=int, default=20)


def cmd_context(args: argparse.Namespace) -> list[dict[str, Any]]:
    sql = """
        select *
        from memory.agent_memory_context
        where tenant_id = %s
          and (%s is null or agent_key = %s)
          and (%s is null or plugin_key = %s)
        order by agent_key, plugin_key nulls last
        limit %s
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    require_uuid(args.tenant_id, "tenant id"),
                    args.agent_key,
                    args.agent_key,
                    args.plugin_key,
                    args.plugin_key,
                    args.limit,
                ),
            )
            return list(cur.fetchall())


def cmd_text(args: argparse.Namespace) -> list[dict[str, Any]]:
    kinds = args.kind or None
    sql = """
        select *
        from memory.search_memory_text(
          %s,
          %s,
          %s,
          %s::memory.memory_kind[]
        )
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (require_uuid(args.tenant_id, "tenant id"), args.query, args.limit, kinds))
            return list(cur.fetchall())


def cmd_vector(args: argparse.Namespace) -> list[dict[str, Any]]:
    embedding = parse_json_file(args.embedding_file)
    vector = vector_literal(embedding, 384)
    kinds = args.kind or None
    tags = args.required_tag or None
    sql = """
        select *
        from memory.match_memory_items(
          %s,
          %s::vector(384),
          %s,
          %s,
          %s::memory.memory_kind[],
          %s,
          %s::text[]
        )
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    require_uuid(args.tenant_id, "tenant id"),
                    vector,
                    args.limit,
                    args.threshold,
                    kinds,
                    args.subject_entity_id,
                    tags,
                ),
            )
            return list(cur.fetchall())


def cmd_conversation(args: argparse.Namespace) -> list[dict[str, Any]]:
    sql = """
        select role, ordinal, content_text, content_json, message_at, metadata
        from memory.conversation_messages
        where conversation_id = %s
        order by ordinal
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (args.conversation_id,))
            return list(cur.fetchall())


def cmd_diagnostics(args: argparse.Namespace) -> list[dict[str, Any]]:
    sql = """
        select id, level, category, code, message, remediation, occurred_at, resolved_at
        from memory.diagnostic_logs
        where tenant_id = %s
          and (%s or resolved_at is null)
          and level = any(%s::memory.log_level[])
        order by occurred_at desc
        limit %s
    """
    levels = args.level or ["warning", "error", "critical"]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (require_uuid(args.tenant_id, "tenant id"), args.include_resolved, levels, args.limit))
            return list(cur.fetchall())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query the Agent 0 memory database.")
    sub = parser.add_subparsers(dest="command", required=True)

    context = sub.add_parser("context", help="Load Agent 0 plugin memory context.")
    add_common_args(context)
    context.add_argument("--agent-key", default=os.environ.get("MEMORY_AGENT_KEY"))
    context.add_argument("--plugin-key", default=os.environ.get("MEMORY_PLUGIN_KEY"))
    context.set_defaults(func=cmd_context)

    text = sub.add_parser("text", help="Run full-text memory search.")
    add_common_args(text)
    text.add_argument("query")
    text.add_argument("--kind", action="append", choices=["working", "episodic", "semantic", "procedural", "preference", "profile", "artifact", "graph"])
    text.set_defaults(func=cmd_text)

    vector = sub.add_parser("vector", help="Run vector memory search with a 384-dimensional JSON embedding array.")
    add_common_args(vector)
    vector.add_argument("--embedding-file", required=True)
    vector.add_argument("--threshold", type=float, default=0.7)
    vector.add_argument("--kind", action="append", choices=["working", "episodic", "semantic", "procedural", "preference", "profile", "artifact", "graph"])
    vector.add_argument("--subject-entity-id")
    vector.add_argument("--required-tag", action="append")
    vector.set_defaults(func=cmd_vector)

    conversation = sub.add_parser("conversation", help="Replay conversation messages.")
    conversation.add_argument("conversation_id")
    conversation.set_defaults(func=cmd_conversation)

    diagnostics = sub.add_parser("diagnostics", help="List diagnostic logs.")
    add_common_args(diagnostics)
    diagnostics.add_argument("--level", action="append", choices=["debug", "info", "warning", "error", "critical"])
    diagnostics.add_argument("--include-resolved", action="store_true")
    diagnostics.set_defaults(func=cmd_diagnostics)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    rows = args.func(args)
    print(dump_json(rows))


if __name__ == "__main__":
    main()

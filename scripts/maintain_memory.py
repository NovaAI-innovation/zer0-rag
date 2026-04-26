from __future__ import annotations

import argparse
import os
from typing import Any

from memory_db.db import connect, dump_json, execute, fetch_all, fetch_one, require_uuid


def cmd_health(args: argparse.Namespace) -> dict[str, Any]:
    tenant_id = args.tenant_id
    with connect() as conn:
        result = {
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
            "rls": fetch_one(
                conn,
                """
                select count(*) as rls_tables
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'memory'
                  and c.relkind = 'r'
                  and c.relrowsecurity
                """,
            ),
            "vectors": fetch_all(
                conn,
                """
                select c.relname as table_name,
                       a.attname as column_name,
                       format_type(a.atttypid, a.atttypmod) as data_type
                from pg_attribute a
                join pg_class c on c.oid = a.attrelid
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'memory'
                  and c.relname in ('memory_items', 'graph_nodes')
                  and a.attname = 'embedding'
                order by c.relname
                """,
            ),
            "unresolved_diagnostics": fetch_one(
                conn,
                """
                select count(*) as count
                from memory.diagnostic_logs
                where (%s::uuid is null or tenant_id = %s)
                  and resolved_at is null
                  and level in ('warning', 'error', 'critical')
                """,
                (tenant_id, tenant_id),
            ),
            "active_memory_items": fetch_one(
                conn,
                """
                select count(*) as count
                from memory.memory_items
                where (%s::uuid is null or tenant_id = %s)
                  and status = 'active'
                  and archived_at is null
                """,
                (tenant_id, tenant_id),
            ),
            "candidate_memory_items": fetch_one(
                conn,
                """
                select count(*) as count
                from memory.memory_items
                where (%s::uuid is null or tenant_id = %s)
                  and status = 'candidate'
                  and archived_at is null
                """,
                (tenant_id, tenant_id),
            ),
        }
        return result


def cmd_expire_working(args: argparse.Namespace) -> dict[str, Any]:
    tenant_id = require_uuid(args.tenant_id, "tenant id")
    sql = """
        delete from memory.working_memory_items
        where tenant_id = %s
          and expires_at is not null
          and expires_at < now()
    """
    with connect() as conn:
        if args.dry_run:
            row = fetch_one(
                conn,
                """
                select count(*) as expired_rows
                from memory.working_memory_items
                where tenant_id = %s
                  and expires_at is not null
                  and expires_at < now()
                """,
                (tenant_id,),
            )
            return {"dry_run": True, "expired_rows": row["expired_rows"]}
        deleted = execute(conn, sql, (tenant_id,))
        conn.commit()
        return {"dry_run": False, "deleted_rows": deleted}


def cmd_archive_invalid(args: argparse.Namespace) -> dict[str, Any]:
    tenant_id = require_uuid(args.tenant_id, "tenant id")
    sql = """
        update memory.memory_items
        set status = 'archived',
            archived_at = now(),
            updated_at = now()
        where tenant_id = %s
          and archived_at is null
          and valid_until is not null
          and valid_until < now()
    """
    with connect() as conn:
        if args.dry_run:
            row = fetch_one(
                conn,
                """
                select count(*) as archiveable_rows
                from memory.memory_items
                where tenant_id = %s
                  and archived_at is null
                  and valid_until is not null
                  and valid_until < now()
                """,
                (tenant_id,),
            )
            return {"dry_run": True, "archiveable_rows": row["archiveable_rows"]}
        updated = execute(conn, sql, (tenant_id,))
        conn.commit()
        return {"dry_run": False, "archived_rows": updated}


def cmd_unresolved_logs(args: argparse.Namespace) -> list[dict[str, Any]]:
    tenant_id = require_uuid(args.tenant_id, "tenant id")
    with connect() as conn:
        return fetch_all(
            conn,
            """
            select id, level, category, code, message, remediation, occurred_at
            from memory.diagnostic_logs
            where tenant_id = %s
              and resolved_at is null
              and level in ('warning', 'error', 'critical')
            order by occurred_at desc
            limit %s
            """,
            (tenant_id, args.limit),
        )


def cmd_promote(args: argparse.Namespace) -> dict[str, Any]:
    tenant_id = require_uuid(args.tenant_id, "tenant id")
    with connect() as conn:
        current = fetch_one(
            conn,
            """
            select to_jsonb(mi.*) as snapshot
            from memory.memory_items mi
            where tenant_id = %s
              and id = %s
            """,
            (tenant_id, args.memory_item_id),
        )
        if not current:
            raise ValueError("Memory item not found.")

        if args.dry_run:
            return {"dry_run": True, "memory_item_id": args.memory_item_id, "would_promote": True}

        revision = fetch_one(
            conn,
            """
            select coalesce(max(revision_number), 0) + 1 as next_revision
            from memory.memory_revisions
            where memory_item_id = %s
            """,
            (args.memory_item_id,),
        )["next_revision"]

        with conn.cursor() as cur:
            cur.execute(
                """
                update memory.memory_items
                set status = 'active',
                    updated_at = now()
                where tenant_id = %s
                  and id = %s
                returning to_jsonb(memory.memory_items.*) as snapshot
                """,
                (tenant_id, args.memory_item_id),
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
                (
                    tenant_id,
                    args.memory_item_id,
                    revision,
                    args.reason,
                    dump_json(current["snapshot"]),
                    dump_json(updated["snapshot"]),
                ),
            )
        conn.commit()
        return {"dry_run": False, "memory_item_id": args.memory_item_id, "revision_number": revision, "status": "active"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintenance utilities for the Agent 0 memory database.")
    parser.add_argument("--tenant-id", default=os.environ.get("MEMORY_TENANT_ID"))
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="Show schema and tenant health counts.")
    health.set_defaults(func=cmd_health)

    expire = sub.add_parser("expire-working", help="Delete expired working memory rows.")
    expire.add_argument("--dry-run", action="store_true")
    expire.set_defaults(func=cmd_expire_working)

    archive = sub.add_parser("archive-invalid", help="Archive memories whose valid_until has passed.")
    archive.add_argument("--dry-run", action="store_true")
    archive.set_defaults(func=cmd_archive_invalid)

    logs = sub.add_parser("unresolved-logs", help="List unresolved warning/error/critical diagnostic logs.")
    logs.add_argument("--limit", type=int, default=50)
    logs.set_defaults(func=cmd_unresolved_logs)

    promote = sub.add_parser("promote", help="Promote a candidate memory to active and create a revision.")
    promote.add_argument("memory_item_id")
    promote.add_argument("--reason", default="Promoted by maintenance script")
    promote.add_argument("--dry-run", action="store_true")
    promote.set_defaults(func=cmd_promote)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = args.func(args)
    print(dump_json(result))


if __name__ == "__main__":
    main()

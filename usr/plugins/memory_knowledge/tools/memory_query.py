from __future__ import annotations

from helpers.tool import Response, Tool

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import settings_for_agent


class MemoryQuery(Tool):
    """Query tenant-scoped memory, knowledge, or allowed memory schema tables."""

    async def execute(self, **kwargs):
        try:
            settings = settings_for_agent(getattr(self, "agent", None))
            target = str(self.args.get("target") or "memory").strip().lower()
            limit = self.args.get("limit")

            if target == "memory":
                rows = db.search_memory(
                    settings,
                    query=str(self.args.get("query") or ""),
                    limit=limit,
                    kinds=self.args.get("kinds"),
                    include_inactive=self.args.get("include_inactive"),
                    query_embedding=self.args.get("query_embedding"),
                )
            elif target in {"similar_memory", "similar_memories", "duplicates"}:
                rows = db.find_similar_memories(
                    settings,
                    summary=str(self.args.get("summary") or self.args.get("query") or ""),
                    limit=limit,
                    kinds=self.args.get("kinds"),
                    exclude_id=self.args.get("exclude_id"),
                    min_similarity=float(self.args.get("min_similarity") or 0.35),
                )
            elif target == "knowledge":
                rows = db.search_knowledge(
                    settings,
                    query=str(self.args.get("query") or ""),
                    limit=limit,
                    query_embedding=self.args.get("query_embedding"),
                )
            elif target == "table":
                rows = db.list_table(
                    settings,
                    table=str(self.args.get("table") or ""),
                    filters=self.args.get("filters") or {},
                    limit=limit,
                )
            else:
                return Response(
                    message="Unsupported target. Use target='memory', 'similar_memory', 'knowledge', or 'table'.",
                    break_loop=False,
                )
            return Response(message=db.dump_json({"rows": rows, "count": len(rows)}), break_loop=False)
        except Exception as exc:
            return Response(message=db.dump_json({"ok": False, "error": str(exc)}), break_loop=False)

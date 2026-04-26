from __future__ import annotations

from helpers.tool import Response, Tool
from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryRetrieve(Tool):
    async def execute(self, **kwargs):
        query = self.args.get("query") or self.args.get("text")
        if not query:
            return Response(message="Missing required argument: query", break_loop=False)

        runtime = ensure_runtime(self.agent)
        settings = runtime.settings if runtime else db.load_settings()
        limit = int(self.args.get("limit") or (runtime.max_context_items if runtime else settings.default_limit))
        kinds = self.args.get("kinds") or (runtime.allowed_kinds if runtime else None)
        rows = db.search_text(settings, query, limit, kinds)
        return Response(message=db.dump_json(rows), break_loop=False)

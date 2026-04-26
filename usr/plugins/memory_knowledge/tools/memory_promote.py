from __future__ import annotations

from helpers.tool import Response, Tool
from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryPromote(Tool):
    async def execute(self, **kwargs):
        runtime = ensure_runtime(self.agent)
        if runtime and not runtime.can_promote:
            return Response(message="Memory promotion is disabled by policy.", break_loop=False)

        memory_item_id = self.args.get("memory_item_id")
        if not memory_item_id:
            return Response(message="Missing required argument: memory_item_id", break_loop=False)
        settings = runtime.settings if runtime else db.load_settings()
        result = db.promote_memory(settings, memory_item_id, self.args.get("reason", "Promoted by Agent Zero tool"))
        return Response(message=db.dump_json(result), break_loop=False)

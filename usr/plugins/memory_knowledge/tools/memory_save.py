from __future__ import annotations

from helpers.tool import Response, Tool
from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemorySave(Tool):
    async def execute(self, **kwargs):
        runtime = ensure_runtime(self.agent)
        if runtime and not runtime.can_write:
            return Response(message="Memory writes are disabled by policy.", break_loop=False)

        settings = runtime.settings if runtime else db.load_settings()
        payload = {
            "kind": self.args.get("kind", "semantic"),
            "status": self.args.get("status", "candidate"),
            "visibility": self.args.get("visibility", "tenant"),
            "title": self.args.get("title"),
            "summary": self.args.get("summary"),
            "body": self.args.get("body"),
            "facts": self.args.get("facts", {}),
            "tags": self.args.get("tags", ["manual"]),
            "importance": self.args.get("importance", 0.5),
            "confidence": self.args.get("confidence", 0.7),
            "details": self.args.get("details", {}),
            "evidence": self.args.get("evidence", []),
            "metadata": {"tool": "memory_save"},
        }
        if not payload["summary"]:
            return Response(message="Missing required argument: summary", break_loop=False)
        memory_item_id = db.create_memory_item(settings, payload)
        return Response(message=db.dump_json({"memory_item_id": memory_item_id}), break_loop=False)

from __future__ import annotations

from helpers.tool import Response, Tool

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import settings_for_agent


class MemoryHealth(Tool):
    """Check database connectivity and memory schema readiness."""

    async def execute(self, **kwargs):
        try:
            settings = settings_for_agent(getattr(self, "agent", None))
            return Response(message=db.dump_json(db.health(settings)), break_loop=False)
        except Exception as exc:
            return Response(message=db.dump_json({"ok": False, "error": str(exc)}), break_loop=False)

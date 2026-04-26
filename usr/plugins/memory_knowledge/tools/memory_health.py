from __future__ import annotations

from helpers.tool import Response, Tool
from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryHealth(Tool):
    async def execute(self, **kwargs):
        runtime = ensure_runtime(self.agent)
        settings = runtime.settings if runtime else db.load_settings()
        return Response(message=db.dump_json(db.health(settings)), break_loop=False)

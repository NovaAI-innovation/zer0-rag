from __future__ import annotations

from helpers.api import ApiHandler, Request
from usr.plugins.memory_knowledge.helpers import db


class Context(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        settings = db.load_settings(input.get("config") or {})
        return {"context": db.load_context(settings)}

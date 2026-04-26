from __future__ import annotations

from helpers.api import ApiHandler, Request
from usr.plugins.memory_knowledge.helpers import db


class Diagnostics(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        settings = db.load_settings(input.get("config") or {})
        return {"ok": True, "rows": db.diagnostics(settings, int(input.get("limit") or 50))}

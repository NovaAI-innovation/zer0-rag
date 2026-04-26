from __future__ import annotations

from helpers.api import ApiHandler, Request
from usr.plugins.memory_knowledge.helpers import db


class Search(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        settings = db.load_settings(input.get("config") or {})
        query = input.get("query")
        if not query:
            return {"ok": False, "error": "Missing query"}
        rows = db.search_text(settings, query, int(input.get("limit") or settings.default_limit), input.get("kinds"))
        return {"ok": True, "rows": rows}

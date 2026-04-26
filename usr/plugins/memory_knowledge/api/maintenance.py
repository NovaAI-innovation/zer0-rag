from __future__ import annotations

from helpers.api import ApiHandler, Request
from usr.plugins.memory_knowledge.helpers import db


class Maintenance(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        settings = db.load_settings(input.get("config") or {})
        action = input.get("action", "health")
        if action == "health":
            return {"ok": True, "health": db.health(settings)}
        return {"ok": False, "error": f"Unsupported maintenance action: {action}"}

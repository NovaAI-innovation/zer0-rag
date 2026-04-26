from __future__ import annotations

from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.extractor import extract_candidates
from usr.plugins.memory_knowledge.helpers.recorder import record_turn
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemorySaveExtension(Extension):
    async def execute(self, **kwargs):
        runtime = ensure_runtime(self.agent)
        if not runtime or not runtime.enabled or not runtime.can_write:
            return
        conversation = None
        if runtime.settings.record_conversation:
            conversation = await record_turn(self.agent, runtime, kwargs)
        await extract_candidates(self.agent, runtime, conversation, kwargs)

from __future__ import annotations

from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.retrieval import inject_memory_block, retrieve_for_turn
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryRetrieveExtension(Extension):
    async def execute(self, **kwargs):
        runtime = ensure_runtime(self.agent)
        if not runtime or not runtime.enabled or not runtime.can_read:
            return
        memories = await retrieve_for_turn(self.agent, runtime, kwargs)
        inject_memory_block(self.agent, runtime, kwargs, memories)

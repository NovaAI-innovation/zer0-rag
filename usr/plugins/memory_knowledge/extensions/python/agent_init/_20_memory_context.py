from __future__ import annotations

from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.runtime import load_memory_runtime


class MemoryContextExtension(Extension):
    async def execute(self, **kwargs):
        self.agent.memory_knowledge = await load_memory_runtime(self.agent)

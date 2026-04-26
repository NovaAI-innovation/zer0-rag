from __future__ import annotations

from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.recorder import begin_turn
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryStartTraceExtension(Extension):
    async def execute(self, **kwargs):
        runtime = ensure_runtime(self.agent)
        if runtime and runtime.enabled:
            begin_turn(self.agent, runtime, kwargs)

from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.runtime import load_memory_runtime


class MemoryContextExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            load_memory_runtime(agent)
        except Exception as exc:
            remember_error(agent, exc)

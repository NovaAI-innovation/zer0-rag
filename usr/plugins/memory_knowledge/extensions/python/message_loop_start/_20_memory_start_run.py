from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import begin_turn
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryStartRunExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            begin_turn(agent, runtime, kwargs)
        except Exception as exc:
            remember_error(agent, exc)

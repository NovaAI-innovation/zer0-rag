from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import record_thought
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryMonologueStartExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            # In Agent Zero this hook can fire before the next message_loop_start,
            # so writing a step here can attach it to the previous run.
            record_thought(runtime, kwargs)
        except Exception as exc:
            remember_error(agent, exc)

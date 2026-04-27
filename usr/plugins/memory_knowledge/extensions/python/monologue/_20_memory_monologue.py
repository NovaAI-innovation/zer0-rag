from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import record_step, record_thought
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryMonologueExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            record_thought(runtime, kwargs)
            record_step(runtime, name="monologue", step_type="monologue", kwargs=kwargs)
        except Exception as exc:
            remember_error(agent, exc)

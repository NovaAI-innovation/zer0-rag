from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import record_step, record_tool_call
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryToolStartExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            record_step(runtime, name="tool_execution_start", step_type="tool", kwargs=kwargs, status="running")
            start_kwargs = dict(kwargs)
            start_kwargs.setdefault("status", "running")
            record_tool_call(runtime, start_kwargs)
        except Exception as exc:
            remember_error(agent, exc)

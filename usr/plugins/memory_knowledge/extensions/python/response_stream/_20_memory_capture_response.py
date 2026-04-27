from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import capture_response_chunk
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryResponseCaptureExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            capture_response_chunk(runtime, kwargs)
        except Exception as exc:
            remember_error(agent, exc)

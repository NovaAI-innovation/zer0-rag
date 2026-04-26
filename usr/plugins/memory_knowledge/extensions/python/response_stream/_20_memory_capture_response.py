from __future__ import annotations

from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.recorder import capture_response_chunk
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryResponseCaptureExtension(Extension):
    async def execute(self, **kwargs):
        runtime = ensure_runtime(self.agent)
        if runtime and runtime.enabled:
            capture_response_chunk(runtime, kwargs)

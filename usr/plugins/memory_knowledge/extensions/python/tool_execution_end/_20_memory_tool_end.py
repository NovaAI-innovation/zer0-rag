from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.automation import upsert_knowledge_from_tool
from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import record_step, record_tool_call
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryToolEndExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            end_kwargs = dict(kwargs)
            end_kwargs.setdefault("status", "succeeded")
            record_tool_call(runtime, end_kwargs)
            knowledge = upsert_knowledge_from_tool(runtime, end_kwargs)
            if knowledge:
                end_kwargs["knowledge_document_id"] = knowledge.get("id")
                end_kwargs["knowledge_chunks_written"] = knowledge.get("chunks_written")
            record_step(runtime, name="tool_execution_end", step_type="tool", kwargs=end_kwargs)
        except Exception as exc:
            remember_error(agent, exc)

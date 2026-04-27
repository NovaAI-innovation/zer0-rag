from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import record_step
from usr.plugins.memory_knowledge.helpers.retrieval import inject_memory_block, retrieve_for_turn
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemoryRetrieveExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            rows = retrieve_for_turn(agent, runtime, kwargs)
            inject_memory_block(agent, runtime, kwargs, rows)
            record_step(
                runtime,
                name="memory_context_retrieval",
                step_type="memory",
                kwargs={"matches": len(rows), "memory_ids": runtime.injected_memory_ids},
            )
        except Exception as exc:
            remember_error(agent, exc)

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.automation import upsert_knowledge_from_tool
from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import record_step, record_tool_call
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


def _current_tool(agent: Any | None) -> Any | None:
    loop_data = getattr(agent, "loop_data", None)
    return getattr(loop_data, "current_tool", None) if loop_data is not None else None


def _tool_call_id(tool: Any | None) -> str | None:
    log = getattr(tool, "log", None)
    return str(getattr(log, "id", "")) or None


def _response_payload(response: Any) -> Any:
    if response is None:
        return None
    if is_dataclass(response):
        return asdict(response)
    return {
        "message": getattr(response, "message", str(response)),
        "break_loop": getattr(response, "break_loop", None),
        "additional": getattr(response, "additional", None),
    }


class MemoryToolAfterExtension(Extension):
    async def execute(self, response: Any = None, tool_name: str = "", **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return

            tool = _current_tool(agent)
            output = _response_payload(response)
            payload = {
                **kwargs,
                "tool_name": tool_name or getattr(tool, "name", ""),
                "tool_call_id": _tool_call_id(tool),
                "args": getattr(tool, "args", {}) or {},
                "output": output,
                "stdout": output.get("message") if isinstance(output, dict) else None,
                "status": "succeeded",
            }
            record_tool_call(runtime, payload)
            knowledge = upsert_knowledge_from_tool(runtime, payload)
            if knowledge:
                payload["knowledge_document_id"] = knowledge.get("id")
                payload["knowledge_chunks_written"] = knowledge.get("chunks_written")
            record_step(runtime, name="tool_execute_after", step_type="tool", kwargs=payload)
        except Exception as exc:
            remember_error(agent, exc)

from __future__ import annotations

from typing import Any

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.recorder import record_step, record_tool_call
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


def _current_tool(agent: Any | None) -> Any | None:
    loop_data = getattr(agent, "loop_data", None)
    return getattr(loop_data, "current_tool", None) if loop_data is not None else None


def _tool_call_id(tool: Any | None) -> str | None:
    log = getattr(tool, "log", None)
    return str(getattr(log, "id", "")) or None


class MemoryToolBeforeExtension(Extension):
    async def execute(self, tool_args: dict[str, Any] | None = None, tool_name: str = "", **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return

            tool = _current_tool(agent)
            payload = {
                **kwargs,
                "tool_name": tool_name or getattr(tool, "name", ""),
                "tool_call_id": _tool_call_id(tool),
                "args": tool_args or getattr(tool, "args", {}) or {},
                "status": "running",
            }
            record_step(runtime, name="tool_execute_before", step_type="tool", kwargs=payload, status="running")
            record_tool_call(runtime, payload)
        except Exception as exc:
            remember_error(agent, exc)

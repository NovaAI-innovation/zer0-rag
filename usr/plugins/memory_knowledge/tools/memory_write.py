from __future__ import annotations

from helpers.tool import Response, Tool

from usr.plugins.memory_knowledge.helpers import db
from usr.plugins.memory_knowledge.helpers.enrichment import EnrichmentField, enrich_fields, plain_text
from usr.plugins.memory_knowledge.helpers.runtime import settings_for_agent


class MemoryWrite(Tool):
    """Write tenant-scoped records to memory, knowledge, and run-history tables."""

    def _enrich_args(self, settings: db.Settings, action: str) -> dict:
        payload = dict(self.args)
        if action in {"memory", "create_memory", "save_memory"}:
            enriched = enrich_fields(
                settings,
                [
                    EnrichmentField("title", "memory_items.title", payload.get("title")),
                    EnrichmentField("summary", "memory_items.summary", payload.get("summary")),
                    EnrichmentField("body", "memory_items.body", payload.get("body")),
                ],
            )
            payload.update({key: value for key, value in enriched.items() if value})
        elif action in {"knowledge", "upsert_knowledge", "knowledge_document"}:
            enriched = enrich_fields(
                settings,
                [
                    EnrichmentField("title", "knowledge_documents.title", payload.get("title")),
                    EnrichmentField("content_text", "knowledge_documents.content_text", payload.get("content_text") or payload.get("content_json")),
                ],
            )
            payload.update({key: value for key, value in enriched.items() if value})
            if "chunks" in payload and isinstance(payload["chunks"], list):
                chunk_fields = [
                    EnrichmentField(f"chunk_{index}", "knowledge_chunks.content_text", chunk.get("content_text") or chunk.get("content_json"))
                    for index, chunk in enumerate(payload["chunks"])
                    if isinstance(chunk, dict)
                ]
                chunk_enriched = enrich_fields(settings, chunk_fields)
                chunks = []
                for index, chunk in enumerate(payload["chunks"]):
                    if isinstance(chunk, dict):
                        chunk = dict(chunk)
                        chunk["content_text"] = chunk_enriched.get(f"chunk_{index}", plain_text(chunk.get("content_text") or chunk.get("content_json")))
                    chunks.append(chunk)
                payload["chunks"] = chunks
        elif action in {"run", "log_run"}:
            enriched = enrich_fields(
                settings,
                [
                    EnrichmentField("input_text", "run_history.input_text", payload.get("input_text")),
                    EnrichmentField("response_text", "run_history.response_text", payload.get("response_text")),
                    EnrichmentField("reasoning_summary", "run_history.reasoning_summary", payload.get("reasoning_summary")),
                ],
            )
            payload.update({key: value for key, value in enriched.items() if value})
        elif action in {"run_message", "message", "log_message"}:
            payload["content_text"] = enrich_fields(
                settings,
                [EnrichmentField("content_text", "run_messages.content_text", payload.get("content_text") or payload.get("content_json"))],
            ).get("content_text", plain_text(payload.get("content_text") or payload.get("content_json")))
        elif action in {"run_thought", "thought", "log_thought"}:
            payload["content_text"] = enrich_fields(
                settings,
                [EnrichmentField("content_text", "run_thoughts.content_text", payload.get("content_text") or payload.get("content_json"))],
            ).get("content_text", plain_text(payload.get("content_text") or payload.get("content_json")))
        elif action in {"tool_execution", "tool", "log_tool_execution"}:
            enriched = enrich_fields(
                settings,
                [
                    EnrichmentField("stdout_text", "tool_executions.stdout_text", payload.get("stdout_text") or payload.get("output_payload")),
                    EnrichmentField("stderr_text", "tool_executions.stderr_text", payload.get("stderr_text")),
                    EnrichmentField("error_message", "tool_executions.error_message", payload.get("error_message")),
                ],
            )
            payload.update({key: value for key, value in enriched.items() if value})
        return payload

    async def execute(self, **kwargs):
        try:
            settings = settings_for_agent(getattr(self, "agent", None))
            action = str(self.args.get("action") or "memory").strip().lower()
            args = self._enrich_args(settings, action)

            if action in {"memory", "create_memory", "save_memory"}:
                result = db.create_memory(settings, args)
            elif action in {"knowledge", "upsert_knowledge", "knowledge_document"}:
                result = db.upsert_knowledge_document(settings, args)
            elif action in {"run", "log_run"}:
                result = db.log_run(settings, args)
            elif action in {"run_message", "message", "log_message"}:
                result = db.log_run_message(settings, args)
            elif action in {"run_step", "step", "log_step"}:
                result = db.log_run_step(settings, args)
            elif action in {"run_thought", "thought", "log_thought"}:
                result = db.log_run_thought(settings, args)
            elif action in {"promote_memory", "promote", "activate_memory"}:
                result = db.promote_memory(settings, str(args.get("memory_item_id") or args.get("id") or ""), args.get("reason"))
            elif action in {"reinforce_memory", "reinforce", "confirm_memory"}:
                result = db.adjust_memory_confidence(
                    settings,
                    str(args.get("memory_item_id") or args.get("id") or ""),
                    float(args.get("delta") or settings.memory_evidence_confidence_delta),
                    args.get("reason") or "manual_reinforcement",
                    args.get("support_score"),
                )
            elif action in {"weaken_memory", "weaken", "correct_memory"}:
                result = db.adjust_memory_confidence(
                    settings,
                    str(args.get("memory_item_id") or args.get("id") or ""),
                    float(args.get("delta") or settings.memory_correction_confidence_delta),
                    args.get("reason") or "manual_correction",
                    args.get("support_score"),
                )
            elif action in {"reject_memory", "reject"}:
                memory_id = str(args.get("memory_item_id") or args.get("id") or "")
                db.adjust_memory_confidence(
                    settings,
                    memory_id,
                    float(args.get("delta") or settings.memory_correction_confidence_delta),
                    args.get("reason") or "manual_rejection",
                    args.get("support_score"),
                )
                result = db.set_memory_status(settings, memory_id, "rejected", args.get("reason") or "manual_rejection")
            elif action in {"tool_execution", "tool", "log_tool_execution"}:
                result = db.log_tool_execution(settings, args)
            else:
                return Response(
                    message=(
                        "Unsupported action. Use action='memory', 'knowledge', "
                        "'run', 'run_message', 'run_step', 'run_thought', "
                        "'promote_memory', 'reinforce_memory', 'weaken_memory', "
                        "'reject_memory', or 'tool_execution'."
                    ),
                    break_loop=False,
                )
            return Response(message=db.dump_json({"ok": True, "result": result}), break_loop=False)
        except Exception as exc:
            return Response(message=db.dump_json({"ok": False, "error": str(exc)}), break_loop=False)

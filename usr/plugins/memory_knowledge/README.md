# Memory Knowledge

Agent Zero plugin for querying and writing to the Supabase `memory` schema.

## Configuration

Preferred mode is Supabase Data API:

```text
auth.mode=data_api
supabase.url=https://ufgbtyrdwngayrrutfdz.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
```

The `memory` schema must be exposed in the Supabase project's Data API settings.

Postgres URL mode is still available with `auth.mode=postgres` and one of:

```text
MEMORY_DATABASE_URL
SUPABASE_DB_URL
DATABASE_URL
```

Optional tenant and agent settings:

```text
MEMORY_TENANT_ID=<uuid>
MEMORY_TENANT_SLUG=agent-zero
MEMORY_AGENT_KEY=agent0:zero
```

If `MEMORY_TENANT_ID` is omitted, the plugin uses `MEMORY_TENANT_SLUG` and creates the tenant when writes are enabled.

## Tools

`MemoryHealth` verifies connectivity and reports table counts.

`MemoryQuery` supports:

- `target="memory"` with `query`, optional `kinds`, `limit`, `include_inactive`
- `target="similar_memory"` with `summary` or `query`, optional `kinds`, `exclude_id`, `min_similarity`, `limit`
- `target="knowledge"` with `query`, optional `limit`
- `target="table"` with `table`, optional equality `filters`, `limit`

`MemoryWrite` supports:

- `action="memory"` to create `memory.memory_items`
- `action="knowledge"` to upsert documents and chunks
- `action="run"` to upsert `memory.run_history`
- `action="run_message"` to upsert `memory.run_messages`
- `action="run_step"` to upsert `memory.run_steps`
- `action="run_thought"` to upsert `memory.run_thoughts`
- `action="promote_memory"` to activate a candidate `memory.memory_items` row
- `action="reinforce_memory"` / `action="weaken_memory"` / `action="reject_memory"` to adjust confidence from feedback
- `action="tool_execution"` to upsert `memory.tool_executions` when `tool_call_id` is present

Writes are tenant-scoped and constrained to known schema operations. The plugin does not expose arbitrary SQL mutation to the agent.

## LLM Enrichment

Text fields are enriched before persistence with xAI by default:

- Provider endpoint: `https://api.x.ai/v1/chat/completions`
- Model: `grok-4-1-fast-non-reasoning`
- API key env var: `XAI_API_KEY`

The enrichment client batches fields where possible. If the API key is missing or a call fails, the plugin still writes deterministic plain text converted from the original value. JSON payload fields such as `input_payload`, `output_payload`, `metadata`, `facts`, and `content_json` remain complete JSON objects and are not truncated by the enrichment layer.

Memory item subject enrichment can run one record at a time with a cheaper designated model (`llm_subject_enrichment.model`, default `grok-3-mini`). When enabled, each `memory_items` write can infer:

- concise `summary` text from the memory body/content
- concise subject tags merged into `tags`
- a primary subject used to populate `subject_id` when no explicit subject is supplied

Embedding generation is configurable via `embeddings.*`. When enabled, missing `memory_items.embedding` and `knowledge_chunks.embedding` values are auto-generated on write. If the embedding API call fails, a deterministic hash-based 384-dim fallback can be used to avoid null vectors.

## Lifecycle Automation

The plugin includes deterministic Python extensions for:

- `agent_init`: load plugin runtime settings.
- `message_loop_start`: create a `memory.run_history` row.
- `message_loop_prompts_before`: retrieve active memory and inject a context block.
- `response_stream`: capture assistant response chunks.
- `monologue_start` / `monologue_end`: record monologue steps and thoughts when those extension points are available.
- `tool_execute_before` / `tool_execute_after`: record tool steps and upsert tool execution rows with the Agent Zero tool log id as `tool_call_id`.
- `tool_execution_start` / `tool_execution_end`: compatibility hooks for builds that expose those extension points.
- `message_loop_end`: write run messages, finalize the run, and create cue-matched candidate memories with evidence.
- tool-output knowledge parsing: successful tool responses are converted to text and upserted to `knowledge_documents` when they exceed `lifecycle.min_tool_response_chars` (fallback `lifecycle.min_knowledge_chars`).

Candidate memory extraction is rule-based. It only writes when configured cues such as `remember`, `prefer`, `workflow`, or `this repo` appear in the turn text. When `lifecycle.auto_promote_memories` is enabled, candidate semantic and episodic lifecycle memories with confidence at or above `lifecycle.min_promotion_confidence` are automatically promoted to `active` so normal retrieval can use them.

When `lifecycle.auto_reinforce_memories` is enabled, confidence changes automatically from repeated similar observations and retrieval usage. Repeated observations use the database trigram matcher, add supporting evidence, and raise confidence; retrieved memories increment `access_count`, set `last_accessed_at`, and receive a throttled confidence bump controlled by `lifecycle.memory_access_min_interval_minutes`. Manual feedback can reinforce, weaken, or reject a memory through `MemoryWrite`.

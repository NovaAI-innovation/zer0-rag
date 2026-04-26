# Agent 0 Memory and Knowledge Database

This document is the single point of truth for building the Agent 0 memory plugin and the matching Agent 0 profile. It describes the database purpose, schema design, routing model, access rules, and operational flows.

## Current Deployment

- Supabase cloud project: `zero`
- Project ref: `ufgbtyrdwngayrrutfdz`
- Region: `us-east-1`
- Schema: `memory`
- Embedding dimension: `384`
- Vector extension: `extensions.vector`
- Hashing extension: `extensions.pgcrypto`
- Local migrations: `supabase/migrations`

The database is designed as a private service-side memory system. Plugin writes should use a trusted server-side key or backend connection. Browser clients should not write directly to `memory`.

## Design Goals

The schema supports:

- Verbatim conversation history for replay, audit, and provenance.
- Working memory for short-lived per-agent/per-conversation state.
- Episodic memory for events and experiences.
- Semantic memory for durable facts and concepts.
- Procedural memory for reusable workflows and instructions.
- Graph memory for entities and relationships.
- Evidence links for traceable memory extraction.
- Revision history for memory changes.
- Trace logs for agent/plugin operations.
- Error and warning logs for diagnostics.
- Retrieval configuration for Agent 0 profiles and plugins.
- Capability bindings so a plugin can discover what it may read, write, and promote.

## Migration Map

- `20260426081518_001_memory_foundation.sql`: schemas, extensions, shared functions, tenants, actors, Agent 0 profiles, plugin integrations, subject entities, data sources, retention policies, base RLS.
- `20260426081523_002_conversation_history.sql`: conversations, verbatim messages, attachments, spans, conversation events.
- `20260426081524_003_layered_memory.sql`: unified memory item table, episodic/semantic/procedural/working memory, graph nodes/edges, evidence links, memory revisions.
- `20260426081525_004_observability_traceability.sql`: trace runs, trace events, tool invocations, diagnostic logs, ingestion jobs, trace evidence linkage.
- `20260426081526_005_retrieval_and_agent_integration.sql`: retrieval profiles, agent memory bindings, capability grants, Agent 0 context view, vector and text search functions.
- `20260426084046_006_vector_dimension_384.sql`: converts applied databases to 384-dimensional vectors and replaces the vector search RPC.

## Ownership Boundaries

`memory.tenants` is the highest-level boundary. Every tenant-owned row has `tenant_id`.

`memory.actors` identifies humans, agents, tools, systems, and services. The Agent 0 runtime should have an actor row with `kind = 'agent'`.

`memory.agent_profiles` identifies an Agent 0 profile and stores model, memory, and tool policies.

`memory.plugin_integrations` identifies plugin package/version metadata.

`memory.agent_memory_bindings` connects an Agent 0 profile, plugin integration, and retrieval profile. Treat this as the plugin's effective memory policy.

`memory.plugin_capability_grants` stores explicit plugin capabilities.

## Security Model

All base tables in `memory` have RLS enabled.

Authenticated select policies use:

```sql
tenant_id = any(memory.current_tenant_ids())
```

`memory.current_tenant_ids()` reads `auth.jwt()->app_metadata->tenant_ids`.

Do not authorize from `user_metadata`. Supabase user metadata is user-editable. Tenant membership must live in `app_metadata`.

The plugin should usually write through a server-side trusted connection. If you expose any `memory` schema objects through Supabase Data API, keep RLS enabled and create explicit policies that match the actual access model.

The view `memory.agent_memory_context` uses `security_invoker = true`, so the caller's RLS still applies.

## Schema Groups

### Foundation

`memory.tenants`

Tenant boundary for all memory data. Important fields:

- `slug`: stable human-readable tenant key.
- `display_name`: UI/operator label.
- `metadata`: tenant-level configuration.
- `archived_at`: soft archive marker.

`memory.actors`

Participants and systems that create or appear in memory records. Important fields:

- `kind`: `human`, `agent`, `system`, `tool`, or `service`.
- `external_ref`: external Agent 0/user/tool identifier.
- `auth_user_id`: optional link to `auth.users`.
- `metadata`: provider-specific details.

`memory.agent_profiles`

Agent 0 profile definition. Important fields:

- `agent_key`: stable key such as `agent0:zero`.
- `runtime`: default `agent-0`.
- `model_policy`: model choice, fallback model, temperature, context limits.
- `memory_policy`: read/write/promotion strategy.
- `tool_policy`: allowed tools and plugin execution limits.
- `status`: `draft`, `active`, `paused`, or `retired`.

`memory.plugin_integrations`

Plugin registration. Important fields:

- `plugin_key`: stable key such as `local.memory-knowledge`.
- `version`: plugin version.
- `required_capabilities`: capability list expected by the plugin.
- `config_schema`: JSON Schema for plugin settings.
- `default_config`: default plugin settings.

`memory.subject_entities`

Canonical entity records for people, projects, systems, documents, tasks, or organizations.

`memory.data_sources`

External or internal source descriptors. Use for imported docs, files, APIs, tools, or manual sources.

`memory.retention_policies`

Retention and archive rules. The current migrations store policy data; enforcement should be implemented by scheduled jobs or plugin maintenance tasks.

### Conversation History

`memory.conversations`

Conversation/thread container. Route every user session or Agent 0 task thread here.

Important fields:

- `agent_profile_id`: profile handling the conversation.
- `started_by_actor_id`: actor that opened the thread.
- `external_thread_id`: Agent 0 or provider thread id.
- `status`: `open`, `closed`, `archived`.

`memory.conversation_messages`

Verbatim message store. This is the source of truth for replay and audit.

Important fields:

- `role`: `system`, `user`, `assistant`, `tool`, `developer`, or `observer`.
- `ordinal`: deterministic message order inside the conversation.
- `content_text`: exact message text.
- `content_json`: structured provider/tool payload.
- `content_sha256`: generated integrity hash.
- `token_count`, `model`, `finish_reason`: optional model metadata.

`memory.message_attachments`

Attachment metadata. Store either a URI or Supabase Storage bucket/path.

`memory.message_spans`

Character offsets into `conversation_messages.content_text`. Use for citations, entity mentions, extraction spans, policy flags, and warning spans.

`memory.conversation_events`

Non-message lifecycle and routing events, such as retrieval decisions, handoffs, plugin state changes, or user corrections.

### Layered Memory

`memory.memory_items`

Unified durable memory table. Every long-lived memory starts here.

Important fields:

- `kind`: `working`, `episodic`, `semantic`, `procedural`, `preference`, `profile`, `artifact`, or `graph`.
- `status`: `candidate`, `active`, `superseded`, `rejected`, or `archived`.
- `visibility`: `private`, `tenant`, `shared`, or `public`.
- `summary`: concise retrievable memory text.
- `body`: optional detailed text.
- `facts`: structured fact payload.
- `tags`: filterable tags.
- `embedding`: `vector(384)` for vector retrieval.
- `importance`, `confidence`: ranking and promotion signals from `0` to `1`.
- `valid_from`, `valid_until`: temporal validity.
- `created_by_actor_id`: actor that created the memory.

Use `candidate` for extracted but unapproved memories. Promote to `active` only after enough confidence, evidence, or user confirmation.

`memory.episodic_memories`

Details for `memory_items.kind = 'episodic'`. Use for things that happened.

`memory.semantic_memories`

Details for durable facts, concepts, and statements. `contradiction_group` can group competing facts.

`memory.procedural_memories`

Details for reusable procedures: triggers, steps, success criteria, and failure modes.

`memory.working_memory_items`

Short-lived state. Use for active task state, planner scratch state, recent goals, and temporary context. Use `expires_at` where possible.

### Graph Memory

`memory.graph_nodes`

Entity or concept nodes. A node can link to a `subject_entity_id`, a `memory_item_id`, or both.

`memory.graph_edges`

Typed weighted relationships between nodes.

Important fields:

- `edge_type`: relationship type such as `owns`, `depends_on`, `prefers`, `mentioned`, `caused`, `located_in`.
- `weight`: relationship strength.
- `evidence_memory_item_id`: memory that supports the relationship.
- `valid_from`, `valid_until`: temporal relationship validity.

### Provenance

`memory.memory_evidence_links`

Connect every derived memory to its source. Evidence may point to a conversation message, conversation event, trace event, data source, manual assertion, or external ref.

Use `quote` for the exact supporting excerpt when available. Use `support_score` from `-1` to `1`, where negative values represent contradiction.

`memory.memory_revisions`

Append-only snapshots for memory updates. Use this whenever a plugin changes an important memory item.

### Observability

`memory.trace_runs`

One row per plugin/agent operation. Examples:

- `conversation.turn`
- `memory.retrieve`
- `memory.extract`
- `memory.promote`
- `graph.update`
- `tool.invoke`

`memory.trace_events`

Ordered events inside a trace run. `sequence_number` provides deterministic replay.

`memory.tool_invocations`

Tool call audit table. Store input, output, status, duration, and error details.

`memory.diagnostic_logs`

Warning/error/critical log. Use this for recoverable errors, failed writes, rejected memories, invalid embeddings, retrieval failures, and policy denials.

`memory.ingestion_jobs`

Batch import, embedding, reindexing, or extraction job state.

### Retrieval And Integration

`memory.retrieval_profiles`

Retrieval configuration.

Important fields:

- `embedding_model`: plugin embedding model name.
- `embedding_dimensions`: must be `384`.
- `default_match_count`: default vector result count.
- `default_similarity_threshold`: default vector threshold.
- `kind_weights`: ranking weights per memory kind.
- `filters`: default JSON filter configuration.

`memory.agent_memory_bindings`

Effective profile/plugin memory policy.

Important fields:

- `can_read`: plugin may retrieve memory.
- `can_write`: plugin may create memory.
- `can_promote`: plugin may promote candidates.
- `allowed_memory_kinds`: allowed `memory_kind` values.
- `denied_tags`: tags that must be excluded from reads/writes.
- `max_context_items`: maximum memory items to inject.

`memory.plugin_capability_grants`

Explicit capability registry. Recommended capabilities:

- `conversation.write`
- `memory.read`
- `memory.write`
- `memory.promote`
- `memory.revise`
- `graph.read`
- `graph.write`
- `trace.write`
- `diagnostic.write`
- `ingestion.write`

`memory.agent_memory_context`

Startup view for the plugin. Query this first to discover the profile, plugin, retrieval, and binding policy.

`memory.match_memory_items(...)`

Vector search RPC. Requires a `vector(384)` query embedding.

`memory.search_memory_text(...)`

Full-text search RPC over memory title, summary, and body.

## Plugin Routing Model

The plugin should route requests by intent. Keep these routes stable even if internal implementation changes.

### Startup Route

Route: `memory.context.load`

Input:

- `tenant_id`
- `agent_key`
- `plugin_key`
- optional `plugin_version`

Database path:

1. Read `memory.agent_memory_context`.
2. Resolve `agent_profile_id`, `plugin_integration_id`, `retrieval_profile_key`, allowed kinds, denied tags, and max context items.
3. Cache the result for the current Agent 0 run.

Failure behavior:

- Missing binding: log `warning` in `memory.diagnostic_logs`, disable memory writes.
- Missing capability: log `warning`, disable the denied operation.

### Conversation Write Route

Route: `conversation.record`

Input:

- thread id
- agent profile
- actor
- ordered messages
- provider metadata

Database path:

1. Upsert or create `memory.conversations` by `(tenant_id, external_thread_id)`.
2. Insert each message into `memory.conversation_messages`.
3. Insert attachments into `memory.message_attachments`.
4. Insert spans into `memory.message_spans`.
5. Insert lifecycle events into `memory.conversation_events`.

Routing rules:

- Never summarize instead of storing verbatim text.
- Preserve provider payloads in `content_json`.
- Use `ordinal` for deterministic replay.

### Retrieval Route

Route: `memory.retrieve`

Input:

- `tenant_id`
- query text
- query embedding, exactly 384 dimensions
- allowed memory kinds
- required tags
- subject entity id
- match limit

Database path:

1. Load policy from `memory.agent_memory_context`.
2. Apply `allowed_memory_kinds`, `denied_tags`, and `max_context_items`.
3. Call `memory.match_memory_items(...)` for vector search.
4. Optionally call `memory.search_memory_text(...)` for lexical recall.
5. Merge, deduplicate, rerank, and return context.
6. Write `memory.trace_runs` and `memory.trace_events`.

Recommended ranking inputs:

- vector similarity
- text rank
- importance
- confidence
- recency
- memory kind weight
- subject/entity match
- evidence quality

### Candidate Extraction Route

Route: `memory.extract`

Input:

- conversation id
- message ids
- extraction result
- confidence
- embedding

Database path:

1. Create `memory.memory_items` with `status = 'candidate'`.
2. Insert kind-specific detail row in `episodic_memories`, `semantic_memories`, or `procedural_memories`.
3. Insert `memory.memory_evidence_links` for every source message/event/trace.
4. Insert graph nodes/edges if entity relationships were extracted.
5. Insert trace events and diagnostic logs as needed.

Routing rules:

- Every derived memory must have evidence.
- Store exact supporting quote when possible.
- Do not promote low-confidence candidates automatically.

### Promotion Route

Route: `memory.promote`

Input:

- memory item id
- actor id
- promotion reason
- optional revision payload

Database path:

1. Check `agent_memory_bindings.can_promote`.
2. Insert `memory.memory_revisions` with old and new snapshots.
3. Update `memory.memory_items.status` to `active`.
4. Insert trace event.

### Revision Route

Route: `memory.revise`

Input:

- memory item id
- changed fields
- reason
- actor id

Database path:

1. Read current memory item.
2. Insert `memory.memory_revisions`.
3. Update `memory.memory_items`.
4. Insert new or updated evidence links if source support changed.

### Graph Route

Route: `graph.upsert`

Input:

- subject entities
- nodes
- edges
- supporting memory/evidence

Database path:

1. Upsert `memory.subject_entities`.
2. Upsert `memory.graph_nodes`.
3. Upsert or insert `memory.graph_edges`.
4. Link graph edges to supporting memory with `evidence_memory_item_id`.

### Trace Route

Route: `trace.record`

Input:

- operation
- status
- trace event payloads
- optional tool calls

Database path:

1. Create `memory.trace_runs`.
2. Insert ordered `memory.trace_events`.
3. Insert `memory.tool_invocations`.
4. Set final `trace_runs.status`, `ended_at`, and `duration_ms`.

### Diagnostic Route

Route: `diagnostic.record`

Input:

- level
- category
- code
- message
- remediation
- related trace/conversation/message

Database path:

1. Insert `memory.diagnostic_logs`.
2. Set `resolved_at` later when fixed.

Use levels consistently:

- `warning`: recoverable policy, validation, or ingestion issue.
- `error`: failed operation that affected memory behavior.
- `critical`: data integrity, auth, or cross-tenant safety risk.

## Agent 0 Profile Configuration

Create one Agent 0 profile row per runtime profile.

Recommended `agent_profiles.memory_policy`:

```json
{
  "memory_enabled": true,
  "default_retrieval_profile": "default-memory-retrieval",
  "write_candidates": true,
  "auto_promote": false,
  "require_evidence": true,
  "max_context_items": 20,
  "embedding_dimensions": 384,
  "allowed_memory_kinds": [
    "episodic",
    "semantic",
    "procedural",
    "preference",
    "profile",
    "artifact",
    "graph"
  ]
}
```

Recommended `agent_profiles.tool_policy`:

```json
{
  "memory_plugin": "local.memory-knowledge",
  "trace_all_operations": true,
  "log_tool_failures": true,
  "deny_browser_direct_writes": true
}
```

Recommended `plugin_integrations.default_config`:

```json
{
  "routes": {
    "startup": "memory.context.load",
    "recordConversation": "conversation.record",
    "retrieve": "memory.retrieve",
    "extract": "memory.extract",
    "promote": "memory.promote",
    "revise": "memory.revise",
    "upsertGraph": "graph.upsert",
    "trace": "trace.record",
    "diagnostic": "diagnostic.record"
  },
  "embedding": {
    "dimensions": 384,
    "distance": "cosine"
  },
  "writes": {
    "storeVerbatimConversation": true,
    "requireEvidenceLinks": true,
    "defaultMemoryStatus": "candidate"
  }
}
```

## Minimal Seed

Use this shape to bootstrap an Agent 0 profile and memory plugin. Replace placeholders before running.

```sql
insert into memory.tenants (slug, display_name)
values ('zero', 'Zero')
returning id;

insert into memory.actors (tenant_id, kind, external_ref, display_name)
values (:tenant_id, 'agent', 'agent0:zero', 'Agent 0 Zero')
returning id;

insert into memory.agent_profiles (
  tenant_id,
  actor_id,
  agent_key,
  display_name,
  runtime,
  model_policy,
  memory_policy,
  tool_policy,
  status
)
values (
  :tenant_id,
  :actor_id,
  'agent0:zero',
  'Agent 0 Zero',
  'agent-0',
  '{"primary_model":"default","fallback_model":"default"}'::jsonb,
  '{"memory_enabled":true,"embedding_dimensions":384,"write_candidates":true,"auto_promote":false,"require_evidence":true}'::jsonb,
  '{"memory_plugin":"local.memory-knowledge","trace_all_operations":true}'::jsonb,
  'active'
)
returning id;

insert into memory.plugin_integrations (
  tenant_id,
  plugin_key,
  display_name,
  version,
  status,
  required_capabilities,
  default_config
)
values (
  :tenant_id,
  'local.memory-knowledge',
  'Memory Knowledge Plugin',
  '0.1.0',
  'active',
  array['conversation.write','memory.read','memory.write','trace.write','diagnostic.write','graph.write'],
  '{"embedding":{"dimensions":384,"distance":"cosine"}}'::jsonb
)
returning id;

insert into memory.retrieval_profiles (
  tenant_id,
  profile_key,
  display_name,
  embedding_model,
  embedding_dimensions,
  default_match_count,
  default_similarity_threshold
)
values (
  :tenant_id,
  'default-memory-retrieval',
  'Default Memory Retrieval',
  'agent-zero-384',
  384,
  20,
  0.7
)
returning id;

insert into memory.agent_memory_bindings (
  tenant_id,
  agent_profile_id,
  plugin_integration_id,
  retrieval_profile_id,
  can_read,
  can_write,
  can_promote,
  max_context_items
)
values (
  :tenant_id,
  :agent_profile_id,
  :plugin_integration_id,
  :retrieval_profile_id,
  true,
  true,
  false,
  20
);
```

## Common Query Patterns

Load plugin context:

```sql
select *
from memory.agent_memory_context
where tenant_id = :tenant_id
  and agent_key = 'agent0:zero'
  and plugin_key = 'local.memory-knowledge';
```

Vector retrieve active memories:

```sql
select *
from memory.match_memory_items(
  :tenant_id,
  :query_embedding::vector(384),
  20,
  0.7,
  array['semantic','episodic']::memory.memory_kind[],
  null,
  null
);
```

Text retrieve active memories:

```sql
select *
from memory.search_memory_text(
  :tenant_id,
  :query_text,
  20,
  array['semantic','episodic']::memory.memory_kind[]
);
```

Replay conversation:

```sql
select role, ordinal, content_text, content_json, message_at
from memory.conversation_messages
where conversation_id = :conversation_id
order by ordinal;
```

Find unresolved diagnostics:

```sql
select level, category, code, message, remediation, occurred_at
from memory.diagnostic_logs
where tenant_id = :tenant_id
  and resolved_at is null
  and level in ('warning', 'error', 'critical')
order by occurred_at desc;
```

## Performance Design

Important access paths are indexed:

- Tenant/status/time indexes for operational lists.
- Foreign-key side indexes for joins.
- Partial indexes for active or unresolved rows.
- GIN indexes for tags, facts, metadata, and payload JSONB.
- GIN full-text indexes for messages and memory text.
- HNSW vector indexes on non-null `memory_items.embedding` and `graph_nodes.embedding`.

Pagination should use keyset pagination over stable columns such as:

- conversations: `(started_at, id)`
- messages: `(message_at, id)` or `(ordinal, id)`
- memory items: `(updated_at, id)`
- trace/log records: `(occurred_at, id)` or `(started_at, id)`

## Operational Checklist

- Keep `SUPABASE_ACCESS_TOKEN` as an `sbp_...` personal access token when using CLI cloud commands.
- Keep service-role keys server-side only.
- Confirm `memory.memory_items.embedding` and `memory.graph_nodes.embedding` remain `vector(384)`.
- Generate 384-dimensional embeddings before vector writes.
- Store verbatim conversation messages before extracting memories.
- Create evidence links for every derived memory.
- Write trace runs/events for every plugin operation.
- Write diagnostic logs for failed retrievals, invalid embeddings, policy denials, and failed writes.
- Use `candidate` first; promote to `active` only when policy allows.
- Run `supabase db lint --linked --schema memory --fail-on error` after schema changes.

# Agent 0 Memory and Knowledge Database

This document is the single point of truth for the Supabase memory database in this repo. It explains the migration layout, data model, access rules, and the integration contract for an Agent 0 plugin and agent profile.

## Purpose

The database stores durable agent knowledge with traceable provenance. It supports raw conversation replay, working memory, episodic memory, semantic memory, procedural memory, graph knowledge, retrieval, plugin capability grants, traceability, and diagnostic logs.

The schema is private under `memory`. Service-side plugin code should use a privileged server connection for writes. Authenticated read access is available only for tenants listed in `auth.jwt()->app_metadata->tenant_ids`.

## Migration Map

- `001_memory_foundation`: Extensions, private schema, tenants, actors, agent profiles, plugin integrations, subject entities, data sources, retention policies, shared trigger/function helpers, base RLS.
- `002_conversation_history`: Verbatim conversation threads, messages, attachments, message spans, and conversation events.
- `003_layered_memory`: Unified memory records plus episodic, semantic, procedural, working-memory, graph, evidence, and revision tables.
- `004_observability_traceability`: Trace runs, trace events, tool invocations, diagnostic warning/error logs, ingestion jobs, and trace evidence linkage.
- `005_retrieval_and_agent_integration`: Retrieval profiles, agent memory bindings, plugin capability grants, integration view, vector search RPC, and text search RPC.
- `006_vector_dimension_384`: Forward migration for already-applied local/remote databases that changes memory and graph embedding columns plus vector search RPCs to 384 dimensions.

## Core Model

`memory.tenants` is the top-level boundary. Every tenant-owned table includes `tenant_id`, and all RLS policies use that value.

`memory.actors` represents humans, agents, tools, services, and system actors. `auth_user_id` links a human actor to Supabase Auth where needed.

`memory.agent_profiles` describes Agent 0 runtime identity, model policy, memory policy, tool policy, and lifecycle status.

`memory.plugin_integrations` describes a plugin package, version, required capabilities, config schema, default config, and lifecycle status.

`memory.subject_entities` normalizes people, organizations, projects, files, systems, tasks, or other entities that memories refer to.

`memory.data_sources` records external sources and trust levels for imported or derived knowledge.

## Conversation History

`memory.conversations` stores thread metadata and links each thread to an agent profile and starting actor.

`memory.conversation_messages` stores verbatim message text in `content_text`. This is the replay source for audits and reconstruction. `content_json` stores structured provider payloads, and `content_sha256` gives deterministic integrity checks.

`memory.message_attachments` links messages to URIs or Supabase Storage bucket paths and can store extracted text.

`memory.message_spans` marks exact text ranges for citations, entities, warnings, policy hits, or extraction boundaries.

`memory.conversation_events` stores non-message events such as state changes, user corrections, handoffs, retrieval decisions, or plugin lifecycle events.

## Layered Memory

`memory.memory_items` is the unified memory table. Every durable memory has a kind, status, visibility, summary, optional body, facts JSON, tags, optional vector embedding, importance, confidence, validity window, and metadata.

Use `status = 'candidate'` for newly extracted memories that still need validation. Promote to `active` only when the agent or plugin has enough evidence. Use `superseded` when a newer memory replaces an older one.

Memory-specific detail tables:

- `memory.episodic_memories`: What happened, when, where, with whom, and with what outcome.
- `memory.semantic_memories`: Facts, concepts, statements, qualifiers, and contradiction grouping.
- `memory.procedural_memories`: Procedures, trigger conditions, steps, success criteria, and failure modes.
- `memory.working_memory_items`: Short-lived per-agent or per-conversation state.

Graph knowledge:

- `memory.graph_nodes`: Entity or concept nodes, optionally backed by a memory item.
- `memory.graph_edges`: Typed weighted relationships with evidence and validity windows.

Provenance:

- `memory.memory_evidence_links`: Connects a memory item to a conversation message, conversation event, trace event, data source, manual assertion, or derived external reference.
- `memory.memory_revisions`: Append-only snapshots for memory changes.

## Traceability and Logs

`memory.trace_runs` represents a complete agent/plugin operation.

`memory.trace_events` records ordered events inside a trace run. Use `sequence_number` for deterministic replay.

`memory.tool_invocations` records tool name, input, output, timing, status, and errors.

`memory.diagnostic_logs` is the warning/error log. Use `level in ('warning', 'error', 'critical')` for operational problems that may need remediation. `resolved_at` marks closure.

`memory.ingestion_jobs` tracks imports, embeddings, reindexing, or batch memory extraction.

## Retrieval

Vector search uses `memory.match_memory_items(...)`. It filters by tenant, active status, optional memory kinds, optional subject entity, optional required tags, and similarity threshold. The function also checks tenant membership through RLS-compatible JWT app metadata.

Text search uses `memory.search_memory_text(...)` over title, summary, and body.

`memory.retrieval_profiles` stores embedding model, dimensions, default match count, default threshold, recency policy, kind weights, and default filters. The database default is `384` dimensions.

The current vector dimension is `384`. Agent 0 plugin embeddings must be exactly 384-dimensional when writing `memory.memory_items.embedding`, `memory.graph_nodes.embedding`, or calling `memory.match_memory_items(...)`.

If the plugin uses a different embedding model, create a new forward migration that changes the vector dimension consistently across `memory.memory_items`, `memory.graph_nodes`, `memory.retrieval_profiles.embedding_dimensions`, and `memory.match_memory_items(...)`. Changing dimensions requires re-embedding existing rows; migration `006_vector_dimension_384` clears incompatible existing embeddings during the column type conversion.

## Agent 0 Integration Contract

Create one `memory.agent_profiles` row per Agent 0 profile. Recommended `agent_key` format: `agent0:<profile-name>`.

Create one `memory.plugin_integrations` row per plugin version. Recommended `plugin_key` format: `<publisher>.<plugin-name>`.

Create one `memory.retrieval_profiles` row for the plugin's default retrieval behavior.

Create one `memory.agent_memory_bindings` row linking the agent profile to the retrieval profile and plugin integration. This row is the plugin's effective memory policy:

- `can_read`: plugin may retrieve memory.
- `can_write`: plugin may create candidate or active memories.
- `can_promote`: plugin may promote candidate memories to active.
- `allowed_memory_kinds`: memory kinds the plugin may access.
- `denied_tags`: tags the plugin must exclude.
- `max_context_items`: hard cap for context injection.

Use `memory.plugin_capability_grants` for explicit capabilities such as `memory.read`, `memory.write`, `memory.promote`, `trace.write`, `diagnostic.write`, `conversation.write`, and `graph.write`.

`memory.agent_memory_context` is the main read view for plugin startup. It joins the agent profile, plugin integration, retrieval profile, and binding policy.

## Recommended Plugin Write Flow

1. Open or create `memory.conversations`.
2. Insert each verbatim message into `memory.conversation_messages`.
3. Write trace lifecycle records to `memory.trace_runs` and `memory.trace_events`.
4. Store tool calls in `memory.tool_invocations`.
5. Extract memory candidates into `memory.memory_items` with `status = 'candidate'`.
6. Add exact provenance in `memory.memory_evidence_links`.
7. Promote high-confidence items to `active` when policy allows.
8. Add or update graph nodes and edges for entity relationships.
9. Record warnings/errors in `memory.diagnostic_logs`.

## Security Rules

All tables have RLS enabled. Authenticated select policies require `tenant_id = any(memory.current_tenant_ids())`.

Do not authorize tenants from `user_metadata`. Tenant membership must be placed in Supabase Auth `app_metadata.tenant_ids` because user metadata is editable by users.

Keep service-role keys only on the server side. Browser clients should not write directly to this private schema.

The `memory.agent_memory_context` view uses `security_invoker = true` so the caller's RLS still applies.

## Performance Notes

Frequently filtered foreign keys and tenant/status columns are indexed. Active-row partial indexes keep common queries smaller. JSONB columns that are intended for metadata filtering use GIN indexes. Vector retrieval uses HNSW indexes on non-null embeddings.

Use keyset pagination for large history views: page by `(message_at, id)` or `(updated_at, id)` instead of deep offset pagination.

Keep raw conversation history append-only where possible. Store derived memories separately and link them through evidence records instead of rewriting source messages.

## Minimal Seed Shape

```sql
insert into memory.tenants (slug, display_name)
values ('default-tenant', 'Default Tenant')
returning id;

insert into memory.actors (tenant_id, kind, external_ref, display_name)
values (:tenant_id, 'agent', 'agent0:default', 'Agent 0');

insert into memory.agent_profiles (tenant_id, actor_id, agent_key, display_name, status)
values (:tenant_id, :actor_id, 'agent0:default', 'Agent 0 Default', 'active');

insert into memory.plugin_integrations (tenant_id, plugin_key, display_name, version, status)
values (:tenant_id, 'local.memory-knowledge', 'Memory Knowledge Plugin', '0.1.0', 'active');

insert into memory.retrieval_profiles (tenant_id, profile_key, display_name)
values (:tenant_id, 'default-memory-retrieval', 'Default Memory Retrieval');

insert into memory.agent_memory_bindings (
  tenant_id,
  agent_profile_id,
  plugin_integration_id,
  retrieval_profile_id,
  can_read,
  can_write,
  can_promote
)
values (:tenant_id, :agent_profile_id, :plugin_integration_id, :retrieval_profile_id, true, true, false);
```

## Operational Checklist

- Apply migrations in order.
- Confirm the `vector` extension is available in the Supabase project.
- Store tenant IDs in `app_metadata.tenant_ids` for authenticated read access.
- Keep plugin writes server-side with a privileged key.
- Add evidence links for every derived memory.
- Log all failed tool calls and memory extraction problems.
- Run advisors before production deployment.

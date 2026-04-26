create type memory.memory_kind as enum (
  'working',
  'episodic',
  'semantic',
  'procedural',
  'preference',
  'profile',
  'artifact',
  'graph'
);

create type memory.memory_status as enum (
  'candidate',
  'active',
  'superseded',
  'rejected',
  'archived'
);

create type memory.evidence_kind as enum (
  'conversation_message',
  'conversation_event',
  'trace_event',
  'data_source',
  'manual_assertion',
  'derived'
);

create table memory.memory_items (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  subject_entity_id uuid references memory.subject_entities(id) on delete set null,
  source_id uuid references memory.data_sources(id) on delete set null,
  kind memory.memory_kind not null,
  status memory.memory_status not null default 'candidate',
  visibility memory.memory_visibility not null default 'tenant',
  title text,
  summary text not null,
  body text,
  facts jsonb not null default '{}'::jsonb,
  tags text[] not null default array[]::text[],
  embedding vector(1536),
  importance numeric(5,4) not null default 0.5000,
  confidence numeric(5,4) not null default 0.5000,
  decay_rate numeric(7,6) not null default 0.000000,
  valid_from timestamptz not null default now(),
  valid_until timestamptz,
  last_accessed_at timestamptz,
  access_count bigint not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_by_actor_id uuid references memory.actors(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint memory_items_importance_chk check (importance between 0 and 1),
  constraint memory_items_confidence_chk check (confidence between 0 and 1),
  constraint memory_items_decay_rate_chk check (decay_rate >= 0),
  constraint memory_items_valid_window_chk check (valid_until is null or valid_until > valid_from)
);

create trigger memory_items_set_updated_at
before update on memory.memory_items
for each row execute function memory.set_updated_at();

create table memory.episodic_memories (
  memory_item_id uuid primary key references memory.memory_items(id) on delete cascade,
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  conversation_id uuid references memory.conversations(id) on delete set null,
  happened_at timestamptz not null,
  location text,
  participants uuid[] not null default array[]::uuid[],
  outcome text,
  emotion jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb
);

create table memory.semantic_memories (
  memory_item_id uuid primary key references memory.memory_items(id) on delete cascade,
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  concept_key text,
  statement text not null,
  qualifiers jsonb not null default '{}'::jsonb,
  contradiction_group uuid,
  metadata jsonb not null default '{}'::jsonb
);

create table memory.procedural_memories (
  memory_item_id uuid primary key references memory.memory_items(id) on delete cascade,
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  procedure_key text,
  trigger_conditions jsonb not null default '{}'::jsonb,
  steps jsonb not null default '[]'::jsonb,
  success_criteria jsonb not null default '{}'::jsonb,
  failure_modes jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb
);

create table memory.working_memory_items (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  agent_profile_id uuid references memory.agent_profiles(id) on delete cascade,
  conversation_id uuid references memory.conversations(id) on delete cascade,
  key text not null,
  value jsonb not null,
  priority integer not null default 100,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint working_memory_items_key_unique unique (tenant_id, agent_profile_id, conversation_id, key)
);

create trigger working_memory_items_set_updated_at
before update on memory.working_memory_items
for each row execute function memory.set_updated_at();

create table memory.graph_nodes (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  subject_entity_id uuid references memory.subject_entities(id) on delete set null,
  memory_item_id uuid references memory.memory_items(id) on delete set null,
  node_key text not null,
  node_type text not null,
  label text not null,
  properties jsonb not null default '{}'::jsonb,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint graph_nodes_key_unique unique (tenant_id, node_key)
);

create trigger graph_nodes_set_updated_at
before update on memory.graph_nodes
for each row execute function memory.set_updated_at();

create table memory.graph_edges (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  source_node_id uuid not null references memory.graph_nodes(id) on delete cascade,
  target_node_id uuid not null references memory.graph_nodes(id) on delete cascade,
  edge_type text not null,
  weight numeric(7,6) not null default 1.000000,
  properties jsonb not null default '{}'::jsonb,
  evidence_memory_item_id uuid references memory.memory_items(id) on delete set null,
  valid_from timestamptz not null default now(),
  valid_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint graph_edges_no_self_loop_chk check (source_node_id <> target_node_id),
  constraint graph_edges_weight_chk check (weight >= 0),
  constraint graph_edges_unique_active unique (tenant_id, source_node_id, target_node_id, edge_type)
);

create trigger graph_edges_set_updated_at
before update on memory.graph_edges
for each row execute function memory.set_updated_at();

create table memory.memory_evidence_links (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  memory_item_id uuid not null references memory.memory_items(id) on delete cascade,
  evidence_kind memory.evidence_kind not null,
  conversation_message_id uuid references memory.conversation_messages(id) on delete set null,
  conversation_event_id uuid references memory.conversation_events(id) on delete set null,
  data_source_id uuid references memory.data_sources(id) on delete set null,
  external_ref text,
  quote text,
  support_score numeric(5,4) not null default 1.0000,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint memory_evidence_links_support_score_chk check (support_score between -1 and 1),
  constraint memory_evidence_links_target_chk check (
    conversation_message_id is not null
    or conversation_event_id is not null
    or data_source_id is not null
    or external_ref is not null
  )
);

create table memory.memory_revisions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  memory_item_id uuid not null references memory.memory_items(id) on delete cascade,
  revision_number integer not null,
  changed_by_actor_id uuid references memory.actors(id) on delete set null,
  change_reason text,
  previous_snapshot jsonb not null,
  new_snapshot jsonb not null,
  created_at timestamptz not null default now(),
  constraint memory_revisions_unique unique (memory_item_id, revision_number)
);

create index memory_items_tenant_kind_status_idx on memory.memory_items (tenant_id, kind, status, updated_at desc) where archived_at is null;
create index memory_items_subject_idx on memory.memory_items (subject_entity_id, kind) where archived_at is null;
create index memory_items_tags_idx on memory.memory_items using gin (tags);
create index memory_items_facts_idx on memory.memory_items using gin (facts);
create index memory_items_summary_fts_idx on memory.memory_items using gin (to_tsvector('english', coalesce(title, '') || ' ' || summary || ' ' || coalesce(body, '')));
create index memory_items_embedding_hnsw_idx on memory.memory_items using hnsw (embedding vector_cosine_ops) where embedding is not null;
create index episodic_memories_tenant_time_idx on memory.episodic_memories (tenant_id, happened_at desc);
create index semantic_memories_tenant_concept_idx on memory.semantic_memories (tenant_id, concept_key) where concept_key is not null;
create index procedural_memories_tenant_key_idx on memory.procedural_memories (tenant_id, procedure_key) where procedure_key is not null;
create index working_memory_unbounded_idx on memory.working_memory_items (tenant_id, agent_profile_id, conversation_id, priority) where expires_at is null;
create index working_memory_expires_idx on memory.working_memory_items (tenant_id, expires_at) where expires_at is not null;
create index graph_nodes_tenant_type_idx on memory.graph_nodes (tenant_id, node_type) where archived_at is null;
create index graph_nodes_properties_idx on memory.graph_nodes using gin (properties);
create index graph_nodes_embedding_hnsw_idx on memory.graph_nodes using hnsw (embedding vector_cosine_ops) where embedding is not null;
create index graph_edges_source_idx on memory.graph_edges (source_node_id, edge_type) where archived_at is null;
create index graph_edges_target_idx on memory.graph_edges (target_node_id, edge_type) where archived_at is null;
create index graph_edges_properties_idx on memory.graph_edges using gin (properties);
create index memory_evidence_links_memory_idx on memory.memory_evidence_links (memory_item_id, evidence_kind);
create index memory_revisions_memory_idx on memory.memory_revisions (memory_item_id, revision_number desc);

alter table memory.memory_items enable row level security;
alter table memory.episodic_memories enable row level security;
alter table memory.semantic_memories enable row level security;
alter table memory.procedural_memories enable row level security;
alter table memory.working_memory_items enable row level security;
alter table memory.graph_nodes enable row level security;
alter table memory.graph_edges enable row level security;
alter table memory.memory_evidence_links enable row level security;
alter table memory.memory_revisions enable row level security;

create policy memory_items_authenticated_member_select
on memory.memory_items for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy episodic_memories_authenticated_member_select
on memory.episodic_memories for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy semantic_memories_authenticated_member_select
on memory.semantic_memories for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy procedural_memories_authenticated_member_select
on memory.procedural_memories for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy working_memory_items_authenticated_member_select
on memory.working_memory_items for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy graph_nodes_authenticated_member_select
on memory.graph_nodes for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy graph_edges_authenticated_member_select
on memory.graph_edges for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy memory_evidence_links_authenticated_member_select
on memory.memory_evidence_links for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy memory_revisions_authenticated_member_select
on memory.memory_revisions for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create schema if not exists extensions;
create schema if not exists memory;

create extension if not exists pgcrypto with schema extensions;
create extension if not exists vector with schema extensions;

create type memory.actor_kind as enum (
  'human',
  'agent',
  'system',
  'tool',
  'service'
);

create type memory.memory_kind as enum (
  'semantic',
  'episodic',
  'procedural',
  'preference',
  'profile',
  'working',
  'knowledge'
);

create type memory.record_status as enum (
  'candidate',
  'active',
  'superseded',
  'rejected',
  'archived'
);

create type memory.run_status as enum (
  'running',
  'succeeded',
  'failed',
  'cancelled'
);

create type memory.message_role as enum (
  'system',
  'developer',
  'user',
  'assistant',
  'tool'
);

create or replace function memory.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create or replace function memory.current_tenant_ids()
returns uuid[]
language sql
stable
as $$
  select coalesce(
    array(
      select value::uuid
      from jsonb_array_elements_text(
        coalesce(auth.jwt() -> 'app_metadata' -> 'tenant_ids', '[]'::jsonb)
      ) as value
    ),
    array[]::uuid[]
  );
$$;

create table memory.tenants (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  display_name text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint tenants_slug_format_chk
    check (slug ~ '^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$')
);

create trigger tenants_set_updated_at
before update on memory.tenants
for each row execute function memory.set_updated_at();

create table memory.actors (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  kind memory.actor_kind not null,
  external_ref text,
  display_name text not null,
  auth_user_id uuid references auth.users(id) on delete set null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint actors_external_ref_unique unique (tenant_id, kind, external_ref)
);

create trigger actors_set_updated_at
before update on memory.actors
for each row execute function memory.set_updated_at();

create table memory.subjects (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  subject_key text not null,
  subject_type text not null,
  display_name text,
  aliases text[] not null default array[]::text[],
  attributes jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint subjects_key_unique unique (tenant_id, subject_key)
);

create trigger subjects_set_updated_at
before update on memory.subjects
for each row execute function memory.set_updated_at();

create table memory.memory_items (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  subject_id uuid references memory.subjects(id) on delete set null,
  kind memory.memory_kind not null,
  status memory.record_status not null default 'candidate',
  title text,
  summary text not null,
  body text,
  facts jsonb not null default '{}'::jsonb,
  tags text[] not null default array[]::text[],
  embedding extensions.vector(384),
  importance numeric(5,4) not null default 0.5000,
  confidence numeric(5,4) not null default 0.5000,
  source_ref text,
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
  constraint memory_items_valid_window_chk check (valid_until is null or valid_until > valid_from)
);

create trigger memory_items_set_updated_at
before update on memory.memory_items
for each row execute function memory.set_updated_at();

create table memory.semantic_memories (
  memory_item_id uuid primary key references memory.memory_items(id) on delete cascade,
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  concept_key text,
  statement text not null,
  qualifiers jsonb not null default '{}'::jsonb,
  contradiction_group uuid,
  created_at timestamptz not null default now()
);

create table memory.episodic_memories (
  memory_item_id uuid primary key references memory.memory_items(id) on delete cascade,
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  happened_at timestamptz not null,
  conversation_ref text,
  location text,
  participants uuid[] not null default array[]::uuid[],
  outcome text,
  emotion jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table memory.knowledge_sources (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  source_key text not null,
  source_type text not null,
  uri text,
  trust_level integer not null default 50,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint knowledge_sources_key_unique unique (tenant_id, source_key),
  constraint knowledge_sources_trust_level_chk check (trust_level between 0 and 100)
);

create trigger knowledge_sources_set_updated_at
before update on memory.knowledge_sources
for each row execute function memory.set_updated_at();

create table memory.knowledge_documents (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  source_id uuid references memory.knowledge_sources(id) on delete set null,
  document_key text not null,
  title text not null,
  uri text,
  content_text text,
  content_json jsonb,
  content_sha256 text generated always as (
    case when content_text is null then null
    else encode(extensions.digest(content_text, 'sha256'), 'hex') end
  ) stored,
  status memory.record_status not null default 'active',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint knowledge_documents_key_unique unique (tenant_id, document_key)
);

create trigger knowledge_documents_set_updated_at
before update on memory.knowledge_documents
for each row execute function memory.set_updated_at();

create table memory.knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  document_id uuid not null references memory.knowledge_documents(id) on delete cascade,
  chunk_index integer not null,
  heading text,
  content_text text not null,
  content_json jsonb,
  token_count integer,
  embedding extensions.vector(384),
  tags text[] not null default array[]::text[],
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint knowledge_chunks_document_index_unique unique (document_id, chunk_index),
  constraint knowledge_chunks_token_count_chk check (token_count is null or token_count >= 0)
);

create trigger knowledge_chunks_set_updated_at
before update on memory.knowledge_chunks
for each row execute function memory.set_updated_at();

create table memory.run_history (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  run_key text,
  parent_run_id uuid references memory.run_history(id) on delete set null,
  actor_id uuid references memory.actors(id) on delete set null,
  agent_name text,
  model text,
  operation text not null,
  status memory.run_status not null default 'running',
  input_text text,
  input_payload jsonb not null default '{}'::jsonb,
  response_text text,
  response_payload jsonb,
  response_output jsonb,
  reasoning_summary text,
  thoughts_payload jsonb not null default '[]'::jsonb,
  prompt_tokens integer,
  completion_tokens integer,
  total_tokens integer,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  duration_ms integer,
  error_code text,
  error_message text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint run_history_key_unique unique (tenant_id, run_key),
  constraint run_history_duration_chk check (duration_ms is null or duration_ms >= 0),
  constraint run_history_token_counts_chk check (
    (prompt_tokens is null or prompt_tokens >= 0)
    and (completion_tokens is null or completion_tokens >= 0)
    and (total_tokens is null or total_tokens >= 0)
  )
);

create trigger run_history_set_updated_at
before update on memory.run_history
for each row execute function memory.set_updated_at();

create table memory.run_messages (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  run_id uuid not null references memory.run_history(id) on delete cascade,
  role memory.message_role not null,
  ordinal bigint not null,
  content_text text,
  content_json jsonb,
  token_count integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint run_messages_ordinal_unique unique (run_id, ordinal),
  constraint run_messages_token_count_chk check (token_count is null or token_count >= 0)
);

create table memory.run_steps (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  run_id uuid not null references memory.run_history(id) on delete cascade,
  parent_step_id uuid references memory.run_steps(id) on delete set null,
  sequence_number bigint not null,
  step_type text not null,
  name text not null,
  status memory.run_status not null default 'running',
  input_payload jsonb not null default '{}'::jsonb,
  output_payload jsonb,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  duration_ms integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint run_steps_sequence_unique unique (run_id, sequence_number),
  constraint run_steps_duration_chk check (duration_ms is null or duration_ms >= 0)
);

create table memory.run_thoughts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  run_id uuid not null references memory.run_history(id) on delete cascade,
  step_id uuid references memory.run_steps(id) on delete cascade,
  sequence_number bigint not null,
  thought_type text not null default 'summary',
  content_text text,
  content_json jsonb,
  visibility text not null default 'internal',
  created_at timestamptz not null default now(),
  constraint run_thoughts_sequence_unique unique (run_id, sequence_number)
);

create table memory.tool_executions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  run_id uuid not null references memory.run_history(id) on delete cascade,
  step_id uuid references memory.run_steps(id) on delete set null,
  tool_call_id text,
  tool_name text not null,
  input_payload jsonb not null default '{}'::jsonb,
  output_payload jsonb,
  stdout_text text,
  stderr_text text,
  status memory.run_status not null default 'running',
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  duration_ms integer,
  error_code text,
  error_message text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint tool_executions_duration_chk check (duration_ms is null or duration_ms >= 0)
);

create table memory.memory_evidence (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  memory_item_id uuid not null references memory.memory_items(id) on delete cascade,
  run_id uuid references memory.run_history(id) on delete set null,
  run_message_id uuid references memory.run_messages(id) on delete set null,
  tool_execution_id uuid references memory.tool_executions(id) on delete set null,
  knowledge_chunk_id uuid references memory.knowledge_chunks(id) on delete set null,
  external_ref text,
  quote text,
  support_score numeric(5,4) not null default 1.0000,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint memory_evidence_support_score_chk check (support_score between -1 and 1),
  constraint memory_evidence_target_chk check (
    run_id is not null
    or run_message_id is not null
    or tool_execution_id is not null
    or knowledge_chunk_id is not null
    or external_ref is not null
  )
);

create index actors_tenant_kind_idx on memory.actors (tenant_id, kind) where archived_at is null;
create index subjects_tenant_type_idx on memory.subjects (tenant_id, subject_type) where archived_at is null;
create index subjects_aliases_idx on memory.subjects using gin (aliases);
create index subjects_attributes_idx on memory.subjects using gin (attributes);

create index memory_items_tenant_kind_status_idx on memory.memory_items (tenant_id, kind, status, updated_at desc) where archived_at is null;
create index memory_items_subject_idx on memory.memory_items (subject_id, kind) where archived_at is null;
create index memory_items_tags_idx on memory.memory_items using gin (tags);
create index memory_items_facts_idx on memory.memory_items using gin (facts);
create index memory_items_fts_idx on memory.memory_items using gin (to_tsvector('english', coalesce(title, '') || ' ' || summary || ' ' || coalesce(body, '')));
create index memory_items_embedding_hnsw_idx on memory.memory_items using hnsw (embedding extensions.vector_cosine_ops) where embedding is not null;
create index semantic_memories_tenant_concept_idx on memory.semantic_memories (tenant_id, concept_key) where concept_key is not null;
create index episodic_memories_tenant_time_idx on memory.episodic_memories (tenant_id, happened_at desc);

create index knowledge_sources_tenant_type_idx on memory.knowledge_sources (tenant_id, source_type) where archived_at is null;
create index knowledge_documents_source_idx on memory.knowledge_documents (source_id, updated_at desc) where archived_at is null;
create index knowledge_documents_fts_idx on memory.knowledge_documents using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content_text, '')));
create index knowledge_chunks_document_idx on memory.knowledge_chunks (document_id, chunk_index);
create index knowledge_chunks_tags_idx on memory.knowledge_chunks using gin (tags);
create index knowledge_chunks_fts_idx on memory.knowledge_chunks using gin (to_tsvector('english', coalesce(heading, '') || ' ' || content_text));
create index knowledge_chunks_embedding_hnsw_idx on memory.knowledge_chunks using hnsw (embedding extensions.vector_cosine_ops) where embedding is not null;

create index run_history_tenant_status_idx on memory.run_history (tenant_id, status, started_at desc) where archived_at is null;
create index run_history_operation_idx on memory.run_history (tenant_id, operation, started_at desc) where archived_at is null;
create index run_messages_run_ordinal_idx on memory.run_messages (run_id, ordinal);
create index run_messages_fts_idx on memory.run_messages using gin (to_tsvector('english', coalesce(content_text, '')));
create index run_steps_run_sequence_idx on memory.run_steps (run_id, sequence_number);
create index run_steps_payload_idx on memory.run_steps using gin (input_payload, output_payload);
create index run_thoughts_run_sequence_idx on memory.run_thoughts (run_id, sequence_number);
create index tool_executions_run_idx on memory.tool_executions (run_id, started_at desc);
create index tool_executions_tool_status_idx on memory.tool_executions (tenant_id, tool_name, status, started_at desc);
create index tool_executions_payload_idx on memory.tool_executions using gin (input_payload, output_payload);
create index memory_evidence_memory_idx on memory.memory_evidence (memory_item_id);
create index memory_evidence_run_idx on memory.memory_evidence (run_id) where run_id is not null;

create or replace function memory.match_memory_items(
  p_tenant_id uuid,
  p_query_embedding extensions.vector(384),
  p_match_count integer default 20,
  p_similarity_threshold numeric default 0.7,
  p_kinds memory.memory_kind[] default null,
  p_subject_id uuid default null,
  p_required_tags text[] default null
)
returns table (
  memory_item_id uuid,
  kind memory.memory_kind,
  title text,
  summary text,
  body text,
  tags text[],
  similarity numeric,
  importance numeric,
  confidence numeric,
  updated_at timestamptz
)
language sql
stable
as $$
  select
    mi.id,
    mi.kind,
    mi.title,
    mi.summary,
    mi.body,
    mi.tags,
    (1 - (mi.embedding operator(extensions.<=>) p_query_embedding))::numeric as similarity,
    mi.importance,
    mi.confidence,
    mi.updated_at
  from memory.memory_items mi
  where mi.tenant_id = p_tenant_id
    and mi.tenant_id = any(memory.current_tenant_ids())
    and mi.status = 'active'
    and mi.archived_at is null
    and mi.embedding is not null
    and (p_kinds is null or mi.kind = any(p_kinds))
    and (p_subject_id is null or mi.subject_id = p_subject_id)
    and (p_required_tags is null or mi.tags @> p_required_tags)
    and (1 - (mi.embedding operator(extensions.<=>) p_query_embedding)) >= p_similarity_threshold
  order by mi.embedding operator(extensions.<=>) p_query_embedding, mi.importance desc, mi.confidence desc, mi.updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

create or replace function memory.search_memory_text(
  p_tenant_id uuid,
  p_query text,
  p_match_count integer default 20,
  p_kinds memory.memory_kind[] default null
)
returns table (
  memory_item_id uuid,
  kind memory.memory_kind,
  title text,
  summary text,
  rank real,
  updated_at timestamptz
)
language sql
stable
as $$
  select
    mi.id,
    mi.kind,
    mi.title,
    mi.summary,
    ts_rank(
      to_tsvector('english', coalesce(mi.title, '') || ' ' || mi.summary || ' ' || coalesce(mi.body, '')),
      plainto_tsquery('english', p_query)
    ) as rank,
    mi.updated_at
  from memory.memory_items mi
  where mi.tenant_id = p_tenant_id
    and mi.tenant_id = any(memory.current_tenant_ids())
    and mi.status = 'active'
    and mi.archived_at is null
    and (p_kinds is null or mi.kind = any(p_kinds))
    and to_tsvector('english', coalesce(mi.title, '') || ' ' || mi.summary || ' ' || coalesce(mi.body, ''))
      @@ plainto_tsquery('english', p_query)
  order by rank desc, mi.importance desc, mi.updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

create or replace function memory.match_knowledge_chunks(
  p_tenant_id uuid,
  p_query_embedding extensions.vector(384),
  p_match_count integer default 20,
  p_similarity_threshold numeric default 0.7,
  p_required_tags text[] default null
)
returns table (
  chunk_id uuid,
  document_id uuid,
  document_title text,
  heading text,
  content_text text,
  tags text[],
  similarity numeric,
  metadata jsonb
)
language sql
stable
as $$
  select
    kc.id,
    kc.document_id,
    kd.title,
    kc.heading,
    kc.content_text,
    kc.tags,
    (1 - (kc.embedding operator(extensions.<=>) p_query_embedding))::numeric as similarity,
    kc.metadata
  from memory.knowledge_chunks kc
  join memory.knowledge_documents kd on kd.id = kc.document_id
  where kc.tenant_id = p_tenant_id
    and kc.tenant_id = any(memory.current_tenant_ids())
    and kc.archived_at is null
    and kd.archived_at is null
    and kd.status = 'active'
    and kc.embedding is not null
    and (p_required_tags is null or kc.tags @> p_required_tags)
    and (1 - (kc.embedding operator(extensions.<=>) p_query_embedding)) >= p_similarity_threshold
  order by kc.embedding operator(extensions.<=>) p_query_embedding, kc.updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

create or replace function memory.search_knowledge_text(
  p_tenant_id uuid,
  p_query text,
  p_match_count integer default 20
)
returns table (
  chunk_id uuid,
  document_id uuid,
  document_title text,
  heading text,
  content_text text,
  rank real,
  metadata jsonb
)
language sql
stable
as $$
  select
    kc.id,
    kc.document_id,
    kd.title,
    kc.heading,
    kc.content_text,
    ts_rank(
      to_tsvector('english', coalesce(kc.heading, '') || ' ' || kc.content_text),
      plainto_tsquery('english', p_query)
    ) as rank,
    kc.metadata
  from memory.knowledge_chunks kc
  join memory.knowledge_documents kd on kd.id = kc.document_id
  where kc.tenant_id = p_tenant_id
    and kc.tenant_id = any(memory.current_tenant_ids())
    and kc.archived_at is null
    and kd.archived_at is null
    and kd.status = 'active'
    and to_tsvector('english', coalesce(kc.heading, '') || ' ' || kc.content_text)
      @@ plainto_tsquery('english', p_query)
  order by rank desc, kc.updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

alter table memory.tenants enable row level security;
alter table memory.actors enable row level security;
alter table memory.subjects enable row level security;
alter table memory.memory_items enable row level security;
alter table memory.semantic_memories enable row level security;
alter table memory.episodic_memories enable row level security;
alter table memory.knowledge_sources enable row level security;
alter table memory.knowledge_documents enable row level security;
alter table memory.knowledge_chunks enable row level security;
alter table memory.run_history enable row level security;
alter table memory.run_messages enable row level security;
alter table memory.run_steps enable row level security;
alter table memory.run_thoughts enable row level security;
alter table memory.tool_executions enable row level security;
alter table memory.memory_evidence enable row level security;

create policy tenants_authenticated_member_select on memory.tenants
for select to authenticated using (id = any(memory.current_tenant_ids()));
create policy actors_authenticated_member_select on memory.actors
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy subjects_authenticated_member_select on memory.subjects
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy memory_items_authenticated_member_select on memory.memory_items
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy semantic_memories_authenticated_member_select on memory.semantic_memories
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy episodic_memories_authenticated_member_select on memory.episodic_memories
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy knowledge_sources_authenticated_member_select on memory.knowledge_sources
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy knowledge_documents_authenticated_member_select on memory.knowledge_documents
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy knowledge_chunks_authenticated_member_select on memory.knowledge_chunks
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy run_history_authenticated_member_select on memory.run_history
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy run_messages_authenticated_member_select on memory.run_messages
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy run_steps_authenticated_member_select on memory.run_steps
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy run_thoughts_authenticated_member_select on memory.run_thoughts
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy tool_executions_authenticated_member_select on memory.tool_executions
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));
create policy memory_evidence_authenticated_member_select on memory.memory_evidence
for select to authenticated using (tenant_id = any(memory.current_tenant_ids()));

grant usage on schema memory to authenticated, service_role;
grant select on all tables in schema memory to authenticated;
grant all on all tables in schema memory to service_role;
grant execute on all functions in schema memory to authenticated, service_role;

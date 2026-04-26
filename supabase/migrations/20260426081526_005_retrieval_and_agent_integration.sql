create table memory.retrieval_profiles (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  profile_key text not null,
  display_name text not null,
  embedding_model text not null default 'text-embedding-3-small',
  embedding_dimensions integer not null default 1536,
  default_match_count integer not null default 20,
  default_similarity_threshold numeric(5,4) not null default 0.7000,
  recency_half_life interval,
  kind_weights jsonb not null default '{}'::jsonb,
  filters jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint retrieval_profiles_key_unique unique (tenant_id, profile_key),
  constraint retrieval_profiles_dimensions_chk check (embedding_dimensions > 0),
  constraint retrieval_profiles_match_count_chk check (default_match_count between 1 and 200),
  constraint retrieval_profiles_similarity_chk check (default_similarity_threshold between 0 and 1)
);

create trigger retrieval_profiles_set_updated_at
before update on memory.retrieval_profiles
for each row execute function memory.set_updated_at();

create table memory.agent_memory_bindings (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  agent_profile_id uuid not null references memory.agent_profiles(id) on delete cascade,
  retrieval_profile_id uuid references memory.retrieval_profiles(id) on delete set null,
  plugin_integration_id uuid references memory.plugin_integrations(id) on delete set null,
  can_read boolean not null default true,
  can_write boolean not null default true,
  can_promote boolean not null default false,
  allowed_memory_kinds memory.memory_kind[] not null default enum_range(null::memory.memory_kind),
  denied_tags text[] not null default array[]::text[],
  max_context_items integer not null default 20,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agent_memory_bindings_agent_plugin_unique unique (agent_profile_id, plugin_integration_id),
  constraint agent_memory_bindings_max_context_items_chk check (max_context_items between 1 and 200)
);

create trigger agent_memory_bindings_set_updated_at
before update on memory.agent_memory_bindings
for each row execute function memory.set_updated_at();

create table memory.plugin_capability_grants (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  plugin_integration_id uuid not null references memory.plugin_integrations(id) on delete cascade,
  capability text not null,
  scope jsonb not null default '{}'::jsonb,
  granted_by_actor_id uuid references memory.actors(id) on delete set null,
  granted_at timestamptz not null default now(),
  revoked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  constraint plugin_capability_grants_unique unique (plugin_integration_id, capability)
);

create or replace view memory.agent_memory_context
with (security_invoker = true)
as
select
  ap.tenant_id,
  ap.id as agent_profile_id,
  ap.agent_key,
  ap.display_name as agent_display_name,
  ap.runtime,
  ap.memory_policy,
  amb.plugin_integration_id,
  pi.plugin_key,
  pi.version as plugin_version,
  rp.profile_key as retrieval_profile_key,
  rp.embedding_model,
  rp.embedding_dimensions,
  rp.default_match_count,
  rp.default_similarity_threshold,
  amb.can_read,
  amb.can_write,
  amb.can_promote,
  amb.allowed_memory_kinds,
  amb.denied_tags,
  amb.max_context_items
from memory.agent_profiles ap
left join memory.agent_memory_bindings amb
  on amb.agent_profile_id = ap.id
left join memory.plugin_integrations pi
  on pi.id = amb.plugin_integration_id
left join memory.retrieval_profiles rp
  on rp.id = amb.retrieval_profile_id
where ap.archived_at is null;

create or replace function memory.match_memory_items(
  p_tenant_id uuid,
  p_query_embedding vector(1536),
  p_match_count integer default 20,
  p_similarity_threshold numeric default 0.7,
  p_kinds memory.memory_kind[] default null,
  p_subject_entity_id uuid default null,
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
    (1 - (mi.embedding <=> p_query_embedding))::numeric as similarity,
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
    and (p_subject_entity_id is null or mi.subject_entity_id = p_subject_entity_id)
    and (p_required_tags is null or mi.tags @> p_required_tags)
    and (1 - (mi.embedding <=> p_query_embedding)) >= p_similarity_threshold
  order by
    mi.embedding <=> p_query_embedding,
    mi.importance desc,
    mi.confidence desc,
    mi.updated_at desc
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

create index retrieval_profiles_tenant_idx on memory.retrieval_profiles (tenant_id) where archived_at is null;
create index agent_memory_bindings_agent_idx on memory.agent_memory_bindings (agent_profile_id);
create index agent_memory_bindings_plugin_idx on memory.agent_memory_bindings (plugin_integration_id) where plugin_integration_id is not null;
create index plugin_capability_grants_plugin_idx on memory.plugin_capability_grants (plugin_integration_id) where revoked_at is null;

alter table memory.retrieval_profiles enable row level security;
alter table memory.agent_memory_bindings enable row level security;
alter table memory.plugin_capability_grants enable row level security;

create policy retrieval_profiles_authenticated_member_select
on memory.retrieval_profiles for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy agent_memory_bindings_authenticated_member_select
on memory.agent_memory_bindings for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy plugin_capability_grants_authenticated_member_select
on memory.plugin_capability_grants for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create schema if not exists memory;
create schema if not exists extensions;

create extension if not exists pgcrypto with schema extensions;
create extension if not exists vector with schema extensions;

create type memory.actor_kind as enum (
  'human',
  'agent',
  'system',
  'tool',
  'service'
);

create type memory.memory_visibility as enum (
  'private',
  'tenant',
  'shared',
  'public'
);

create type memory.integration_status as enum (
  'draft',
  'active',
  'paused',
  'retired'
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
  description text,
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

create table memory.agent_profiles (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  actor_id uuid references memory.actors(id) on delete set null,
  agent_key text not null,
  display_name text not null,
  runtime text not null default 'agent-0',
  model_policy jsonb not null default '{}'::jsonb,
  memory_policy jsonb not null default '{}'::jsonb,
  tool_policy jsonb not null default '{}'::jsonb,
  status memory.integration_status not null default 'draft',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint agent_profiles_agent_key_unique unique (tenant_id, agent_key)
);

create trigger agent_profiles_set_updated_at
before update on memory.agent_profiles
for each row execute function memory.set_updated_at();

create table memory.plugin_integrations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  plugin_key text not null,
  display_name text not null,
  version text not null,
  status memory.integration_status not null default 'draft',
  required_capabilities text[] not null default array[]::text[],
  config_schema jsonb not null default '{}'::jsonb,
  default_config jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint plugin_integrations_key_version_unique unique (tenant_id, plugin_key, version)
);

create trigger plugin_integrations_set_updated_at
before update on memory.plugin_integrations
for each row execute function memory.set_updated_at();

create table memory.subject_entities (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  entity_key text not null,
  entity_type text not null,
  display_name text,
  aliases text[] not null default array[]::text[],
  attributes jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint subject_entities_key_unique unique (tenant_id, entity_key)
);

create trigger subject_entities_set_updated_at
before update on memory.subject_entities
for each row execute function memory.set_updated_at();

create table memory.data_sources (
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
  constraint data_sources_key_unique unique (tenant_id, source_key),
  constraint data_sources_trust_level_chk check (trust_level between 0 and 100)
);

create trigger data_sources_set_updated_at
before update on memory.data_sources
for each row execute function memory.set_updated_at();

create table memory.retention_policies (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  policy_key text not null,
  applies_to text not null,
  retain_for interval,
  archive_after interval,
  hard_delete_after interval,
  legal_hold boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint retention_policies_key_unique unique (tenant_id, policy_key),
  constraint retention_policies_window_chk check (
    hard_delete_after is null
    or archive_after is null
    or hard_delete_after >= archive_after
  )
);

create trigger retention_policies_set_updated_at
before update on memory.retention_policies
for each row execute function memory.set_updated_at();

create index tenants_archived_idx on memory.tenants (archived_at) where archived_at is null;
create index actors_tenant_kind_idx on memory.actors (tenant_id, kind) where archived_at is null;
create index actors_auth_user_idx on memory.actors (auth_user_id) where auth_user_id is not null;
create index agent_profiles_tenant_status_idx on memory.agent_profiles (tenant_id, status) where archived_at is null;
create index plugin_integrations_tenant_status_idx on memory.plugin_integrations (tenant_id, status) where archived_at is null;
create index subject_entities_tenant_type_idx on memory.subject_entities (tenant_id, entity_type) where archived_at is null;
create index subject_entities_aliases_idx on memory.subject_entities using gin (aliases);
create index subject_entities_attributes_idx on memory.subject_entities using gin (attributes);
create index data_sources_tenant_type_idx on memory.data_sources (tenant_id, source_type) where archived_at is null;

alter table memory.tenants enable row level security;
alter table memory.actors enable row level security;
alter table memory.agent_profiles enable row level security;
alter table memory.plugin_integrations enable row level security;
alter table memory.subject_entities enable row level security;
alter table memory.data_sources enable row level security;
alter table memory.retention_policies enable row level security;

create policy tenants_authenticated_member_select
on memory.tenants for select
to authenticated
using (id = any(memory.current_tenant_ids()));

create policy actors_authenticated_member_select
on memory.actors for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy agent_profiles_authenticated_member_select
on memory.agent_profiles for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy plugin_integrations_authenticated_member_select
on memory.plugin_integrations for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy subject_entities_authenticated_member_select
on memory.subject_entities for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy data_sources_authenticated_member_select
on memory.data_sources for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy retention_policies_authenticated_member_select
on memory.retention_policies for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create type memory.conversation_status as enum (
  'open',
  'closed',
  'archived'
);

create type memory.message_role as enum (
  'system',
  'user',
  'assistant',
  'tool',
  'developer',
  'observer'
);

create table memory.conversations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  agent_profile_id uuid references memory.agent_profiles(id) on delete set null,
  started_by_actor_id uuid references memory.actors(id) on delete set null,
  external_thread_id text,
  title text,
  status memory.conversation_status not null default 'open',
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint conversations_external_thread_unique unique (tenant_id, external_thread_id)
);

create trigger conversations_set_updated_at
before update on memory.conversations
for each row execute function memory.set_updated_at();

create table memory.conversation_messages (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  conversation_id uuid not null references memory.conversations(id) on delete cascade,
  actor_id uuid references memory.actors(id) on delete set null,
  role memory.message_role not null,
  ordinal bigint not null,
  provider_message_id text,
  content_text text not null,
  content_json jsonb,
  content_sha256 text generated always as (encode(extensions.digest(content_text, 'sha256'), 'hex')) stored,
  token_count integer,
  model text,
  finish_reason text,
  message_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  archived_at timestamptz,
  constraint conversation_messages_ordinal_unique unique (conversation_id, ordinal),
  constraint conversation_messages_token_count_chk check (token_count is null or token_count >= 0)
);

create table memory.message_attachments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  message_id uuid not null references memory.conversation_messages(id) on delete cascade,
  attachment_kind text not null,
  uri text,
  storage_bucket text,
  storage_path text,
  media_type text,
  byte_size bigint,
  sha256 text,
  extracted_text text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint message_attachments_location_chk check (
    uri is not null or (storage_bucket is not null and storage_path is not null)
  ),
  constraint message_attachments_byte_size_chk check (byte_size is null or byte_size >= 0)
);

create table memory.conversation_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  conversation_id uuid not null references memory.conversations(id) on delete cascade,
  message_id uuid references memory.conversation_messages(id) on delete set null,
  event_type text not null,
  event_payload jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table memory.message_spans (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  message_id uuid not null references memory.conversation_messages(id) on delete cascade,
  span_type text not null,
  char_start integer not null,
  char_end integer not null,
  label text,
  attributes jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint message_spans_range_chk check (char_start >= 0 and char_end > char_start)
);

create index conversations_tenant_status_idx on memory.conversations (tenant_id, status, started_at desc) where archived_at is null;
create index conversations_agent_idx on memory.conversations (agent_profile_id, started_at desc) where archived_at is null;
create index conversation_messages_conversation_ordinal_idx on memory.conversation_messages (conversation_id, ordinal);
create index conversation_messages_tenant_role_time_idx on memory.conversation_messages (tenant_id, role, message_at desc) where archived_at is null;
create index conversation_messages_text_fts_idx on memory.conversation_messages using gin (to_tsvector('english', content_text));
create index conversation_messages_metadata_idx on memory.conversation_messages using gin (metadata);
create index message_attachments_message_idx on memory.message_attachments (message_id);
create index conversation_events_conversation_time_idx on memory.conversation_events (conversation_id, occurred_at desc);
create index conversation_events_payload_idx on memory.conversation_events using gin (event_payload);
create index message_spans_message_idx on memory.message_spans (message_id, span_type);

alter table memory.conversations enable row level security;
alter table memory.conversation_messages enable row level security;
alter table memory.message_attachments enable row level security;
alter table memory.conversation_events enable row level security;
alter table memory.message_spans enable row level security;

create policy conversations_authenticated_member_select
on memory.conversations for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy conversation_messages_authenticated_member_select
on memory.conversation_messages for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy message_attachments_authenticated_member_select
on memory.message_attachments for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy conversation_events_authenticated_member_select
on memory.conversation_events for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy message_spans_authenticated_member_select
on memory.message_spans for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

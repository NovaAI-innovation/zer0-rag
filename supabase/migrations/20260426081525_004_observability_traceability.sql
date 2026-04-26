create type memory.trace_status as enum (
  'running',
  'succeeded',
  'failed',
  'cancelled'
);

create type memory.log_level as enum (
  'debug',
  'info',
  'warning',
  'error',
  'critical'
);

create table memory.trace_runs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  agent_profile_id uuid references memory.agent_profiles(id) on delete set null,
  plugin_integration_id uuid references memory.plugin_integrations(id) on delete set null,
  conversation_id uuid references memory.conversations(id) on delete set null,
  parent_trace_run_id uuid references memory.trace_runs(id) on delete set null,
  trace_key text,
  operation text not null,
  status memory.trace_status not null default 'running',
  input_summary text,
  output_summary text,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  duration_ms integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint trace_runs_duration_chk check (duration_ms is null or duration_ms >= 0)
);

create table memory.trace_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  trace_run_id uuid not null references memory.trace_runs(id) on delete cascade,
  parent_trace_event_id uuid references memory.trace_events(id) on delete set null,
  event_name text not null,
  event_type text not null,
  sequence_number bigint not null,
  payload jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint trace_events_sequence_unique unique (trace_run_id, sequence_number)
);

create table memory.tool_invocations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  trace_run_id uuid references memory.trace_runs(id) on delete set null,
  trace_event_id uuid references memory.trace_events(id) on delete set null,
  conversation_message_id uuid references memory.conversation_messages(id) on delete set null,
  tool_name text not null,
  tool_call_id text,
  input_payload jsonb not null default '{}'::jsonb,
  output_payload jsonb,
  status memory.trace_status not null default 'running',
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  duration_ms integer,
  error_code text,
  error_message text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint tool_invocations_duration_chk check (duration_ms is null or duration_ms >= 0)
);

create table memory.diagnostic_logs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  trace_run_id uuid references memory.trace_runs(id) on delete set null,
  trace_event_id uuid references memory.trace_events(id) on delete set null,
  conversation_id uuid references memory.conversations(id) on delete set null,
  conversation_message_id uuid references memory.conversation_messages(id) on delete set null,
  agent_profile_id uuid references memory.agent_profiles(id) on delete set null,
  level memory.log_level not null,
  category text not null,
  code text,
  message text not null,
  details jsonb not null default '{}'::jsonb,
  remediation text,
  occurred_at timestamptz not null default now(),
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);

create table memory.ingestion_jobs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memory.tenants(id) on delete cascade,
  source_id uuid references memory.data_sources(id) on delete set null,
  job_key text,
  status memory.trace_status not null default 'running',
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  records_seen bigint not null default 0,
  records_inserted bigint not null default 0,
  records_updated bigint not null default 0,
  records_failed bigint not null default 0,
  high_water_mark text,
  error_summary text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint ingestion_jobs_counts_chk check (
    records_seen >= 0
    and records_inserted >= 0
    and records_updated >= 0
    and records_failed >= 0
  )
);

alter table memory.memory_evidence_links
  add column trace_event_id uuid references memory.trace_events(id) on delete set null,
  drop constraint memory_evidence_links_target_chk,
  add constraint memory_evidence_links_target_chk check (
    conversation_message_id is not null
    or conversation_event_id is not null
    or trace_event_id is not null
    or data_source_id is not null
    or external_ref is not null
  );

create index trace_runs_tenant_operation_idx on memory.trace_runs (tenant_id, operation, started_at desc);
create index trace_runs_status_idx on memory.trace_runs (tenant_id, status, started_at desc);
create index trace_events_run_sequence_idx on memory.trace_events (trace_run_id, sequence_number);
create index trace_events_payload_idx on memory.trace_events using gin (payload);
create index tool_invocations_trace_idx on memory.tool_invocations (trace_run_id, started_at desc);
create index tool_invocations_tool_status_idx on memory.tool_invocations (tenant_id, tool_name, status, started_at desc);
create index diagnostic_logs_level_time_idx on memory.diagnostic_logs (tenant_id, level, occurred_at desc);
create index diagnostic_logs_unresolved_idx on memory.diagnostic_logs (tenant_id, level, occurred_at desc) where resolved_at is null and level in ('warning', 'error', 'critical');
create index diagnostic_logs_details_idx on memory.diagnostic_logs using gin (details);
create index ingestion_jobs_source_idx on memory.ingestion_jobs (source_id, started_at desc);
create index ingestion_jobs_status_idx on memory.ingestion_jobs (tenant_id, status, started_at desc);
create index memory_evidence_links_trace_event_idx on memory.memory_evidence_links (trace_event_id) where trace_event_id is not null;

alter table memory.trace_runs enable row level security;
alter table memory.trace_events enable row level security;
alter table memory.tool_invocations enable row level security;
alter table memory.diagnostic_logs enable row level security;
alter table memory.ingestion_jobs enable row level security;

create policy trace_runs_authenticated_member_select
on memory.trace_runs for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy trace_events_authenticated_member_select
on memory.trace_events for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy tool_invocations_authenticated_member_select
on memory.tool_invocations for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy diagnostic_logs_authenticated_member_select
on memory.diagnostic_logs for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

create policy ingestion_jobs_authenticated_member_select
on memory.ingestion_jobs for select
to authenticated
using (tenant_id = any(memory.current_tenant_ids()));

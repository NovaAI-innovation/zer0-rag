create extension if not exists pg_trgm with schema extensions;

alter table memory.memory_items
  add column if not exists search_vector tsvector generated always as (
    to_tsvector('english', coalesce(title, '') || ' ' || summary || ' ' || coalesce(body, ''))
  ) stored;

alter table memory.knowledge_chunks
  add column if not exists search_vector tsvector generated always as (
    to_tsvector('english', coalesce(heading, '') || ' ' || content_text)
  ) stored;

alter table memory.run_messages
  add column if not exists search_vector tsvector generated always as (
    to_tsvector('english', coalesce(content_text, ''))
  ) stored;

create index if not exists memory_items_search_vector_idx
  on memory.memory_items using gin (search_vector)
  where archived_at is null;

create index if not exists knowledge_chunks_search_vector_idx
  on memory.knowledge_chunks using gin (search_vector)
  where archived_at is null;

create index if not exists run_messages_search_vector_idx
  on memory.run_messages using gin (search_vector);

create index if not exists memory_items_tenant_status_confidence_idx
  on memory.memory_items (tenant_id, status, confidence desc, updated_at desc)
  where archived_at is null;

create index if not exists memory_items_tenant_last_accessed_idx
  on memory.memory_items (tenant_id, last_accessed_at desc)
  where archived_at is null and last_accessed_at is not null;

create index if not exists memory_evidence_memory_support_idx
  on memory.memory_evidence (memory_item_id, support_score desc, created_at desc);

create index if not exists memory_items_summary_trgm_idx
  on memory.memory_items using gin (summary extensions.gin_trgm_ops)
  where archived_at is null;

create or replace function memory.search_memory_text(
  p_tenant_id uuid,
  p_query text,
  p_match_count integer default 20,
  p_kinds memory.memory_kind[] default null,
  p_include_inactive boolean default false
)
returns table (
  id uuid,
  kind memory.memory_kind,
  status memory.record_status,
  title text,
  summary text,
  body text,
  tags text[],
  importance numeric,
  confidence numeric,
  source_ref text,
  rank real,
  score numeric,
  access_count bigint,
  last_accessed_at timestamptz,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
stable
as $$
  with q as (
    select
      plainto_tsquery('english', p_query) as tsq,
      nullif(trim(p_query), '') as raw_query
  )
  select
    mi.id,
    mi.kind,
    mi.status,
    mi.title,
    mi.summary,
    mi.body,
    mi.tags,
    mi.importance,
    mi.confidence,
    mi.source_ref,
    ts_rank(mi.search_vector, q.tsq) as rank,
    (
      ts_rank(mi.search_vector, q.tsq)::numeric * 0.55
      + mi.importance * 0.15
      + mi.confidence * 0.20
      + least(mi.access_count, 20)::numeric / 20 * 0.05
      + greatest(0, 1 - extract(epoch from (now() - mi.updated_at)) / 2592000)::numeric * 0.05
    ) as score,
    mi.access_count,
    mi.last_accessed_at,
    mi.created_at,
    mi.updated_at
  from memory.memory_items mi
  cross join q
  where mi.tenant_id = p_tenant_id
    and mi.archived_at is null
    and (p_include_inactive or mi.status = 'active')
    and (p_kinds is null or mi.kind = any(p_kinds))
    and mi.search_vector @@ q.tsq
  order by score desc, rank desc, mi.importance desc, mi.confidence desc, mi.updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

drop function if exists memory.search_knowledge_text(uuid, text, integer);

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
  tags text[],
  rank real,
  score numeric,
  metadata jsonb,
  updated_at timestamptz
)
language sql
stable
as $$
  with q as (
    select plainto_tsquery('english', p_query) as tsq
  )
  select
    kc.id,
    kc.document_id,
    kd.title,
    kc.heading,
    kc.content_text,
    kc.tags,
    ts_rank(kc.search_vector, q.tsq) as rank,
    (
      ts_rank(kc.search_vector, q.tsq)::numeric * 0.8
      + greatest(0, 1 - extract(epoch from (now() - kc.updated_at)) / 2592000)::numeric * 0.2
    ) as score,
    kc.metadata,
    kc.updated_at
  from memory.knowledge_chunks kc
  join memory.knowledge_documents kd on kd.id = kc.document_id
  cross join q
  where kc.tenant_id = p_tenant_id
    and kc.archived_at is null
    and kd.archived_at is null
    and kd.status = 'active'
    and kc.search_vector @@ q.tsq
  order by score desc, rank desc, kc.updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

create or replace function memory.find_similar_memories(
  p_tenant_id uuid,
  p_summary text,
  p_match_count integer default 8,
  p_kinds memory.memory_kind[] default null,
  p_exclude_id uuid default null,
  p_min_similarity numeric default 0.35
)
returns table (
  id uuid,
  kind memory.memory_kind,
  status memory.record_status,
  title text,
  summary text,
  similarity numeric,
  confidence numeric,
  updated_at timestamptz
)
language sql
stable
as $$
  select
    mi.id,
    mi.kind,
    mi.status,
    mi.title,
    mi.summary,
    extensions.similarity(mi.summary, p_summary)::numeric as similarity,
    mi.confidence,
    mi.updated_at
  from memory.memory_items mi
  where mi.tenant_id = p_tenant_id
    and mi.archived_at is null
    and mi.status in ('candidate', 'active')
    and (p_kinds is null or mi.kind = any(p_kinds))
    and (p_exclude_id is null or mi.id <> p_exclude_id)
    and extensions.similarity(mi.summary, p_summary) >= p_min_similarity
  order by similarity desc, confidence desc, updated_at desc
  limit least(greatest(p_match_count, 1), 50);
$$;

create or replace function memory.hybrid_search_memory(
  p_tenant_id uuid,
  p_query text,
  p_query_embedding extensions.vector(384),
  p_match_count integer default 20,
  p_kinds memory.memory_kind[] default null,
  p_include_inactive boolean default false
)
returns table (
  id uuid,
  kind memory.memory_kind,
  status memory.record_status,
  title text,
  summary text,
  body text,
  tags text[],
  importance numeric,
  confidence numeric,
  source_ref text,
  rank real,
  similarity numeric,
  score numeric,
  access_count bigint,
  last_accessed_at timestamptz,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
stable
as $$
  with q as (
    select plainto_tsquery('english', p_query) as tsq
  ),
  candidates as (
    select
      mi.*,
      case when mi.search_vector @@ q.tsq then ts_rank(mi.search_vector, q.tsq) else 0 end as text_rank,
      case when mi.embedding is not null then (1 - (mi.embedding operator(extensions.<=>) p_query_embedding))::numeric else 0 end as vector_similarity
    from memory.memory_items mi
    cross join q
    where mi.tenant_id = p_tenant_id
      and mi.archived_at is null
      and (p_include_inactive or mi.status = 'active')
      and (p_kinds is null or mi.kind = any(p_kinds))
      and (
        mi.search_vector @@ q.tsq
        or (mi.embedding is not null and (1 - (mi.embedding operator(extensions.<=>) p_query_embedding)) >= 0.55)
      )
  )
  select
    c.id,
    c.kind,
    c.status,
    c.title,
    c.summary,
    c.body,
    c.tags,
    c.importance,
    c.confidence,
    c.source_ref,
    c.text_rank,
    c.vector_similarity,
    (
      c.text_rank::numeric * 0.35
      + c.vector_similarity * 0.35
      + c.importance * 0.10
      + c.confidence * 0.15
      + least(c.access_count, 20)::numeric / 20 * 0.03
      + greatest(0, 1 - extract(epoch from (now() - c.updated_at)) / 2592000)::numeric * 0.02
    ) as score,
    c.access_count,
    c.last_accessed_at,
    c.created_at,
    c.updated_at
  from candidates c
  order by score desc, vector_similarity desc, text_rank desc, c.updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

create or replace function memory.hybrid_search_knowledge(
  p_tenant_id uuid,
  p_query text,
  p_query_embedding extensions.vector(384),
  p_match_count integer default 20
)
returns table (
  chunk_id uuid,
  document_id uuid,
  document_title text,
  heading text,
  content_text text,
  tags text[],
  rank real,
  similarity numeric,
  score numeric,
  metadata jsonb,
  updated_at timestamptz
)
language sql
stable
as $$
  with q as (
    select plainto_tsquery('english', p_query) as tsq
  ),
  candidates as (
    select
      kc.id,
      kc.document_id,
      kd.title as document_title,
      kc.heading,
      kc.content_text,
      kc.tags,
      kc.metadata,
      kc.updated_at,
      case when kc.search_vector @@ q.tsq then ts_rank(kc.search_vector, q.tsq) else 0 end as text_rank,
      case when kc.embedding is not null then (1 - (kc.embedding operator(extensions.<=>) p_query_embedding))::numeric else 0 end as vector_similarity
    from memory.knowledge_chunks kc
    join memory.knowledge_documents kd on kd.id = kc.document_id
    cross join q
    where kc.tenant_id = p_tenant_id
      and kc.archived_at is null
      and kd.archived_at is null
      and kd.status = 'active'
      and (
        kc.search_vector @@ q.tsq
        or (kc.embedding is not null and (1 - (kc.embedding operator(extensions.<=>) p_query_embedding)) >= 0.55)
      )
  )
  select
    c.id,
    c.document_id,
    c.document_title,
    c.heading,
    c.content_text,
    c.tags,
    c.text_rank,
    c.vector_similarity,
    (c.text_rank::numeric * 0.50 + c.vector_similarity * 0.40 + greatest(0, 1 - extract(epoch from (now() - c.updated_at)) / 2592000)::numeric * 0.10) as score,
    c.metadata,
    c.updated_at
  from candidates c
  order by score desc, vector_similarity desc, text_rank desc, updated_at desc
  limit least(greatest(p_match_count, 1), 200);
$$;

create or replace function memory.record_memory_access(
  p_tenant_id uuid,
  p_memory_item_ids uuid[],
  p_confidence_delta numeric default 0.01,
  p_min_interval interval default '1 hour'
)
returns table (
  id uuid,
  confidence numeric,
  access_count bigint,
  last_accessed_at timestamptz
)
language sql
as $$
  update memory.memory_items mi
  set access_count = mi.access_count + 1,
      last_accessed_at = now(),
      confidence = case
        when mi.last_accessed_at is null or mi.last_accessed_at < now() - p_min_interval
          then least(1, greatest(0, mi.confidence + p_confidence_delta))
        else mi.confidence
      end,
      metadata = case
        when mi.last_accessed_at is null or mi.last_accessed_at < now() - p_min_interval
          then jsonb_set(
            mi.metadata,
            '{confidence_events}',
            coalesce(mi.metadata->'confidence_events', '[]'::jsonb)
              || jsonb_build_array(jsonb_build_object('delta', p_confidence_delta, 'reason', 'retrieved_for_turn', 'at', now())),
            true
          )
        else mi.metadata
      end,
      updated_at = now()
  where mi.tenant_id = p_tenant_id
    and mi.id = any(p_memory_item_ids)
    and mi.archived_at is null
  returning mi.id, mi.confidence, mi.access_count, mi.last_accessed_at;
$$;

drop index if exists memory.memory_items_embedding_hnsw_idx;
drop index if exists memory.graph_nodes_embedding_hnsw_idx;

alter table memory.memory_items
  alter column embedding type vector(384)
  using null;

alter table memory.graph_nodes
  alter column embedding type vector(384)
  using null;

alter table memory.retrieval_profiles
  alter column embedding_dimensions set default 384;

update memory.retrieval_profiles
set
  embedding_dimensions = 384,
  updated_at = now()
where embedding_dimensions <> 384;

create index memory_items_embedding_hnsw_idx
on memory.memory_items
using hnsw (embedding vector_cosine_ops)
where embedding is not null;

create index graph_nodes_embedding_hnsw_idx
on memory.graph_nodes
using hnsw (embedding vector_cosine_ops)
where embedding is not null;

create or replace function memory.match_memory_items(
  p_tenant_id uuid,
  p_query_embedding vector(384),
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

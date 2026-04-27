with ranked as (
  select
    id,
    row_number() over (
      partition by tenant_id, run_id, tool_call_id
      order by ended_at desc nulls last, started_at desc, created_at desc, id desc
    ) as rn
  from memory.tool_executions
  where tool_call_id is not null
)
delete from memory.tool_executions te
using ranked r
where te.id = r.id
  and r.rn > 1;

create unique index if not exists tool_executions_call_unique_idx
  on memory.tool_executions (tenant_id, run_id, tool_call_id);

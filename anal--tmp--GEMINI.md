# GEMINI.md

## Mission

Evaluate live Supabase database contents first. Analyze every reachable table and every field, then produce a report that maps observed data issues to Agent Zero `memory_knowledge` plugin logic that performs automated DB tasks.

## Priority

1. Connect to Supabase via MCP SQL tools.
2. If MCP is unavailable, connect via Supabase Data API.
3. Inventory schemas/tables/columns.
4. Analyze live table contents.
5. Analyze cross-table relationships.
6. Inspect plugin code only where database findings suggest automation issues.
7. Write the required report.

## Scope

Primary:

- Live Supabase database contents.
- All reachable schemas/tables/columns.
- Especially the `memory` schema.

Secondary:

- Plugin automation code in `usr/plugins/memory_knowledge/`.
- Supabase migrations/config only when needed to explain live data.

Expected `memory` tables, if present:

- `tenants`
- `actors`
- `subjects`
- `memory_items`
- `semantic_memories`
- `episodic_memories`
- `knowledge_sources`
- `knowledge_documents`
- `knowledge_chunks`
- `run_history`
- `run_messages`
- `run_steps`
- `run_thoughts`
- `tool_executions`
- `memory_evidence`

Discover the actual table list. Do not assume this list is complete.

## Safety

- Do not mutate database contents.
- Use read-only SQL/API calls.
- Do not expose secrets. Mask keys, tokens, URLs with passwords, credentials, private payload fragments, and sensitive personal data.
- Preserve repo changes. Do not revert, delete, or reformat unrelated files.
- Cite evidence for each finding: table, column, row count, query/API result, file path, function, hook, or config key.
- Separate confirmed findings from hypotheses.

## Connection

### MCP Preferred

Use MCP SQL/database tools to:

- list schemas
- list tables
- list columns/types
- count rows
- sample rows
- inspect constraints/indexes/RLS/policies/grants/functions when useful

### Data API Fallback

Use Data API when MCP is unavailable:

- Use configured project URL and secret source.
- Query `memory` schema with required profile/schema headers.
- Page large tables.
- Record inaccessible tables and exact blocker: credentials, schema exposure, RLS, grants, API error, or unknown.

## Required Table Inventory

For each reachable table, capture:

- schema
- table
- row_count
- column_count
- columns: name/type/nullability/default
- primary_keys
- foreign_keys
- unique_constraints
- check_constraints
- relevant_indexes
- rls_status
- visible_policies
- access_status

## Required Field Analysis

For every column in every reachable table, capture:

- non_null_count
- null_rate
- distinct_count or approximate_distinct_count
- representative_values with sensitive values masked
- min/max for numeric and timestamp fields
- length distribution for text/json fields when useful
- expected_format_match
- anomalies

Flag:

- malformed UUIDs/timestamps/statuses/slugs/URLs/JSON/arrays/vectors
- duplicates
- stale values
- overlarge values
- truncated values
- unexpected nulls
- inconsistent enum-like strings
- sensitive data
- cross-field contradictions

For JSON/JSONB:

- common_keys
- rare_keys
- anomalous_shapes
- large_payloads
- stack_traces
- secrets
- personal_data

For vectors:

- null_count
- non_null_count
- observed_dimensions
- dimension_mismatches
- coverage_by_table

## Required Relationship Analysis

Check:

- orphan references
- tenant mismatches
- duplicate automation artifacts
- run rows without messages
- messages without runs
- steps/thoughts/tools without runs
- tool executions without matching steps
- evidence without memory items
- evidence pointing to missing runs/messages/steps
- chunks without documents
- documents without chunks
- invalid source/document/chunk lineage
- timestamps out of lifecycle order
- archived rows still acting active

## Table-Specific Checks

### Tenants/Actors/Subjects

Find duplicate tenants, invalid slugs, missing tenant links, weak subject metadata, cross-tenant references.

Plugin areas if suspicious:

- `helpers/runtime.py`
- `helpers/db.py`
- `execute.py`
- config keys: `MEMORY_TENANT_ID`, `MEMORY_TENANT_SLUG`, `MEMORY_AGENT_KEY`

### Memory/Evidence

Find weak memories, duplicate memories, bad statuses, missing evidence, bad confidence, irrelevant cue-created memories, evidence cross-tenant issues.

Plugin areas if suspicious:

- `helpers/extractor.py`
- `helpers/recorder.py`
- `tools/memory_write.py`
- `extensions/python/message_loop_end/_20_memory_save.py`

### Knowledge

Find missing sources, documents without chunks, chunks without documents, chunk gaps, duplicate chunks, poor chunk sizes, missing attribution, stale content, missing embeddings.

Plugin areas if suspicious:

- `tools/memory_write.py`
- `helpers/retrieval.py`
- `helpers/db.py`

### Runs/Messages

Find unfinalized runs, impossible durations, duplicate run starts, missing user/assistant messages, wrong roles, empty/truncated/duplicated/out-of-order messages.

Plugin areas if suspicious:

- `extensions/python/message_loop_start/_20_memory_start_run.py`
- `extensions/python/response_stream/_20_memory_capture_response.py`
- `extensions/python/message_loop_end/_20_memory_save.py`
- `helpers/runtime.py`
- `helpers/recorder.py`

### Steps/Thoughts/Tools

Find sequence gaps, duplicate steps, thoughts captured when unavailable, missing tool payloads, failed tool clusters, oversized payloads, secrets in payloads, duplicate tool records.

Plugin areas if suspicious:

- `extensions/python/monologue*/`
- `extensions/python/tool_execution*/`
- `helpers/extensions.py`
- `helpers/recorder.py`

## Plugin Investigation Rule

Inspect plugin code only after a database finding indicates a likely automation path.

For each inspected code path, record:

- triggering_database_finding
- file_path
- function/class/hook/config_key
- evidence_from_code
- suspected_cause
- confirmation_status: confirmed | likely | possible | disproven
- test_to_confirm
- data_to_recheck_after_fix

## Useful SQL Patterns

```sql
select table_schema, table_name
from information_schema.tables
where table_type = 'BASE TABLE'
order by table_schema, table_name;

select table_schema, table_name, column_name, data_type, is_nullable, column_default
from information_schema.columns
order by table_schema, table_name, ordinal_position;

select schemaname, tablename, policyname, roles, cmd, qual, with_check
from pg_policies
order by schemaname, tablename, policyname;

select count(*) from memory.run_history;
select * from memory.run_history order by started_at desc limit 50;
```

Adapt queries to actual schema and available tools. Prefer aggregates before raw sampling on large/sensitive tables.

## Output Format

Return exactly this structure:

```markdown
# Supabase Database Content Evaluation Report

## Executive Summary
- Health:
- Highest-risk findings:
- Main plugin automation areas to investigate:

## Connection Method
- Method:
- Reachable schemas:
- Unreachable tables:
- Limitations:

## Table Inventory
| Schema | Table | Rows | Columns | Access | Detailed Analysis |
|---|---:|---:|---:|---|---|

## Field-Level Findings
For each table:
- Table:
- Column findings:
- Anomalies:
- Evidence:

## Relationship Findings
- Finding:
- Evidence:
- Impact:

## Content Quality Findings
- Finding:
- Evidence:
- Impact:

## Security And Privacy Findings
- Finding:
- Evidence:
- Impact:
- Masking applied:

## Plugin Logic To Investigate
For each mapped issue:
- Database evidence:
- Suspected plugin area:
- Files/functions/hooks/config:
- Why involved:
- Confirmation status:
- Test to confirm:
- Suggested remediation:
- Data to recheck:

## Commands Queries And API Calls
- Item:
- Purpose:
- Result summary:

## Open Questions
- Question:
- Why it blocks confidence:
```

If a table is empty or unreachable, state that explicitly.

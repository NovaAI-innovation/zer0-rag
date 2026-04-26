# Memory Python Scripts

These scripts provide direct Postgres utilities for the Agent 0 memory database. They are intended for trusted server-side use, not browser clients.

## Setup

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Create `.env` from `.env.example` and set:

```text
SUPABASE_DB_URL=postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres
MEMORY_TENANT_ID=...
MEMORY_AGENT_KEY=agent0:zero
MEMORY_PLUGIN_KEY=local.memory-knowledge
```

For local Docker Supabase, use:

```text
SUPABASE_DB_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

## Query

Load plugin context:

```powershell
python .\scripts\query_memory.py context
```

Full-text memory search:

```powershell
python .\scripts\query_memory.py text "deployment preferences" --kind semantic --limit 10
```

Vector memory search:

```powershell
python .\scripts\query_memory.py vector --embedding-file .\embedding.json --threshold 0.7 --limit 10
```

Replay a conversation:

```powershell
python .\scripts\query_memory.py conversation CONVERSATION_UUID
```

List unresolved diagnostics:

```powershell
python .\scripts\query_memory.py diagnostics --level error --limit 20
```

## Ingest

Ingest a conversation:

```powershell
python .\scripts\ingest_memory.py conversation .\conversation.json
```

Ingest a memory item:

```powershell
python .\scripts\ingest_memory.py memory .\memory.json
```

Upsert working memory:

```powershell
python .\scripts\ingest_memory.py working .\working.json
```

## Maintenance

Health summary:

```powershell
python .\scripts\maintain_memory.py health
```

Dry-run expired working memory cleanup:

```powershell
python .\scripts\maintain_memory.py expire-working --dry-run
```

Archive expired durable memories:

```powershell
python .\scripts\maintain_memory.py archive-invalid --dry-run
python .\scripts\maintain_memory.py archive-invalid
```

Promote a candidate memory:

```powershell
python .\scripts\maintain_memory.py promote MEMORY_ITEM_UUID --reason "Confirmed by user"
```

## JSON Shapes

Conversation ingest:

```json
{
  "external_thread_id": "agent0-thread-1",
  "title": "Example thread",
  "started_by": {
    "kind": "human",
    "external_ref": "user:casey",
    "display_name": "Casey"
  },
  "messages": [
    {
      "role": "user",
      "ordinal": 1,
      "content_text": "Remember that the memory DB uses 384-dimensional embeddings."
    },
    {
      "role": "assistant",
      "ordinal": 2,
      "content_text": "Acknowledged."
    }
  ]
}
```

Semantic memory ingest:

```json
{
  "kind": "semantic",
  "status": "candidate",
  "summary": "The memory database uses 384-dimensional embeddings.",
  "facts": {
    "embedding_dimensions": 384
  },
  "tags": ["memory-db", "embeddings"],
  "confidence": 0.95,
  "importance": 0.8,
  "details": {
    "concept_key": "memory-db.embedding-dimensions",
    "statement": "The Agent 0 memory database uses 384-dimensional embeddings."
  },
  "evidence": [
    {
      "evidence_kind": "manual_assertion",
      "external_ref": "manual-bootstrap",
      "quote": "The current vector dimension is 384."
    }
  ]
}
```

Working memory ingest:

```json
{
  "key": "current_task",
  "value": {
    "goal": "Build Agent 0 memory plugin"
  },
  "priority": 10,
  "expires_at": "2026-04-27T00:00:00Z"
}
```

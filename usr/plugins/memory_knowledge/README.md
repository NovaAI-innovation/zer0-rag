# Memory Knowledge

Agent Zero plugin for autonomous Supabase-backed memory.

It retrieves relevant active memories before prompt assembly, records verbatim turns after the loop, and writes candidate durable memories with evidence. The backing schema is the `memory` schema in this repository's Supabase migrations.

## Install

Copy this folder to:

```text
<agent-zero-root>/usr/plugins/memory_knowledge
```

Then configure one of:

```text
MEMORY_DATABASE_URL=postgresql://...
SUPABASE_DB_URL=postgresql://...
DATABASE_URL=postgresql://...
MEMORY_TENANT_ID=...
MEMORY_AGENT_KEY=agent0:zero
MEMORY_PLUGIN_KEY=local.memory-knowledge
```

Run the plugin setup action in Agent Zero to verify `psycopg` is available.

## Lifecycle

- `agent_init`: load tenant/profile/plugin memory policy.
- `message_loop_start`: start a per-turn trace buffer.
- `message_loop_prompts_before`: retrieve and inject memory context.
- `response_stream`: buffer assistant response chunks.
- `message_loop_end`: record conversation, write working memory, and extract candidate memories.

Auto-promotion is disabled by default. Use the `memory_promote` tool when a candidate should become active.

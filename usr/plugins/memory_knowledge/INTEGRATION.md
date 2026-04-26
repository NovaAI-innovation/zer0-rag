# Agent Zero Integration

## Required database state

Apply the Supabase migrations in this repository through:

```text
supabase/migrations/20260426084046_006_vector_dimension_384.sql
```

Seed at least one tenant, actor, `memory.agent_profiles` row, `memory.plugin_integrations` row with plugin key `local.memory-knowledge`, one retrieval profile, and one `memory.agent_memory_bindings` row.

## Required runtime environment

Configure the Agent Zero framework runtime with:

```text
MEMORY_DATABASE_URL=postgresql://...
MEMORY_TENANT_ID=<tenant uuid>
MEMORY_AGENT_KEY=agent0:zero
MEMORY_PLUGIN_KEY=local.memory-knowledge
```

Install:

```text
python -m pip install "psycopg[binary]>=3.2,<4"
```

## Plugin copy target

Copy:

```text
usr/plugins/memory_knowledge
```

to:

```text
<agent-zero-root>/usr/plugins/memory_knowledge
```

Restart Agent Zero and enable the plugin.

## Autonomous loop behavior

- On `agent_init`, the plugin reads `memory.agent_memory_context`.
- On `message_loop_prompts_before`, it searches active memory and injects a compact `Relevant memory` block.
- On `message_loop_end`, it records the turn, updates working memory, and creates candidate semantic memories when durable cues are detected.
- Promotion remains manual unless both database policy and plugin config allow it.

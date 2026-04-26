Assuming you mean **Agent Zero**. Its main extension/integration points are:

### Core extension points

Agent Zero exposes lifecycle hooks where custom Python extension code can run: ([GitHub][1])

| Extension point               | Runs when                                 |
| ----------------------------- | ----------------------------------------- |
| `agent_init`                  | Agent is initialized                      |
| `before_main_llm_call`        | Before the main LLM call                  |
| `message_loop_start`          | Start of message processing loop          |
| `message_loop_prompts_before` | Before message-loop prompts are processed |
| `message_loop_prompts_after`  | After message-loop prompts are processed  |
| `message_loop_end`            | End of message processing loop            |
| `monologue_start`             | Start of agent monologue                  |
| `monologue_end`               | End of agent monologue                    |
| `reasoning_stream`            | Reasoning-stream data is received         |
| `response_stream`             | Response-stream data is received          |
| `system_prompt`               | System prompts are processed              |

### File locations

Default extensions go here:

```text
/python/extensions/{extension_point}/
```

Agent-specific overrides go here:

```text
/agents/{agent_profile}/extensions/{extension_point}/
```

If the same filename exists in both places, the agent-specific version replaces the default one. ([GitHub][1])

### Other integration surfaces

Agent Zero is also extensible through:

| Surface                    | Purpose                                                                | Location / mechanism                                 |
| -------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------- |
| **Tools**                  | Add callable agent capabilities                                        | `/python/tools/` or `/agents/{agent_profile}/tools/` |
| **API endpoints**          | Expose functionality to external systems or UI                         | `/python/api/`                                       |
| **Helpers**                | Shared framework utility logic                                         | `/python/helpers/`                                   |
| **Prompts**                | Customize LLM instructions/context                                     | `/prompts/` and `/agents/{agent_profile}/prompts/`   |
| **Plugins**                | Package extensions, tools, settings, frontend, routes, model providers | `usr/plugins/<plugin_name>/` recommended             |
| **Frontend plugins**       | Add Web UI HTML/JS hook behavior                                       | `extensions/webui/<extension_point>/`                |
| **Plugin API handlers**    | Add plugin-specific backend routes                                     | `POST /api/plugins/<name>/<handler>`                 |
| **Static plugin assets**   | Serve plugin frontend files                                            | `GET /plugins/<name>/<path>`                         |
| **Custom model providers** | Register extra LLM/embedding providers                                 | plugin `conf/model_providers.yaml`                   |

Plugins can also define runtime hooks like `install()` and `pre_update()` inside `hooks.py`, which Agent Zero calls automatically through its plugin hook system. ([agent-zero.ai][2])

The most important practical hook set is the lifecycle extension directory pattern:

```text
/agents/<profile>/extensions/<extension_point>/
```

That is where you inject behavior without modifying core Agent Zero.

[1]: https://github.com/agent0ai/agent-zero/blob/main/docs/developer/extensions.md "agent-zero/docs/developer/extensions.md at main · agent0ai/agent-zero · GitHub"
[2]: https://www.agent-zero.ai/p/docs/plugins/ "Plugins - Agent Zero"

## Autonomous memory plugin hook plan

Goal: the `memory_knowledge` Agent Zero plugin should retrieve useful memories before the model thinks, record conversation turns without being asked, and extract durable candidate memories at the end of each loop. Agent tools remain available for explicit user/agent actions, but normal memory behavior should be lifecycle-driven.

### Recommended plugin extension layout

Build these inside the plugin, not in core Agent Zero directories:

```text
usr/plugins/memory_knowledge/
├── hooks.py
├── helpers/
│   ├── runtime.py
│   ├── db.py
│   ├── retrieval.py
│   ├── recorder.py
│   └── extractor.py
├── extensions/
│   └── python/
│       ├── agent_init/
│       │   └── _20_memory_context.py
│       ├── message_loop_start/
│       │   └── _20_memory_start_trace.py
│       ├── message_loop_prompts_before/
│       │   └── _20_memory_retrieve.py
│       ├── message_loop_end/
│       │   └── _20_memory_save.py
│       └── response_stream/
│           └── _20_memory_capture_response.py
└── tools/
    ├── memory_retrieve.py
    ├── memory_save.py
    └── memory_promote.py
```

Use the `extensions/python/<extension_point>/` plugin layout because current Agent Zero plugin docs describe plugin backend extension directories under `extensions/python/...`.

### Hook responsibilities

| Hook | Responsibility | Database route |
| --- | --- | --- |
| `hooks.py::install()` | Verify Python dependency availability and print setup instructions. Do not create remote schema automatically unless the user runs an explicit setup action. | none |
| `hooks.py::pre_update()` | Flush local caches and warn if pending buffered memory writes exist. | `diagnostic.record` if needed |
| `agent_init` | Load plugin config and query `memory.agent_memory_context`; cache policy on the agent context/run state. If context is missing, disable autonomous writes. | `memory.context.load` |
| `message_loop_start` | Start a trace run, identify the external thread/conversation id, and initialize per-turn buffers for user input and assistant output. | `trace.record` |
| `message_loop_prompts_before` | Retrieve memory context based on latest user/task text, apply `can_read`, `allowed_memory_kinds`, `denied_tags`, and `max_context_items`, then inject a concise memory block into prompts/context. | `memory.retrieve` |
| `response_stream` | Append assistant response chunks to a per-turn buffer only. Do not write to the database on every chunk. | none |
| `message_loop_end` | Record verbatim conversation messages, upsert working memory, run extraction, write candidate memories with evidence, finish trace, and log diagnostics for failures. | `conversation.record`, `memory.extract`, `trace.record`, `diagnostic.record` |

### Retrieval behavior

Run retrieval in `message_loop_prompts_before`, not `before_main_llm_call`, because this gives the plugin a chance to add context before prompt processing finishes and keeps memory context visible to the model. Use `before_main_llm_call` only as a fallback if Agent Zero's prompt assembly changes for a target version.

Retrieval steps:

1. Resolve tenant/profile/plugin policy from the `agent_init` cache.
2. Build a query from the latest user message plus task title/project context when available.
3. If an embedding provider is configured, generate a 384-dimensional query embedding and call `memory.match_memory_items(...)`.
4. Always run `memory.search_memory_text(...)` as lexical fallback unless disabled.
5. Merge vector and text results, deduplicate by `memory_item_id`, rerank by similarity/rank, confidence, importance, recency, and kind weight.
6. Inject only a compact block, for example:

```text
Relevant memory:
- [semantic, confidence 0.92] The memory database uses 384-dimensional embeddings.
- [procedural, confidence 0.81] For schema edits, create Supabase migrations before applying cloud changes.
```

Do not inject raw secrets, denied tags, rejected memories, archived memories, or unresolved low-confidence candidates.

### Save/extract behavior

Run persistence in `message_loop_end`. It should be fault-tolerant: memory write failures must not break the user response after it has been generated.

Persistence steps:

1. Store verbatim user and assistant messages with stable ordinals in `memory.conversation_messages`.
2. Upsert working memory keys for current task, active project, pending decisions, and recent errors.
3. Extract candidate durable memories only when the turn contains durable signals:
   - explicit user preference
   - stable project fact
   - reusable procedure
   - correction to prior memory
   - completed task outcome
4. Require evidence for every candidate. Evidence should reference conversation/message ids and include a short quote when available.
5. Default durable writes to `status = 'candidate'`.
6. Promote only through an explicit tool or policy-approved hook path where `can_promote = true`.

### Minimal extension pseudocode

`agent_init/_20_memory_context.py`:

```python
from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.runtime import load_memory_runtime


class MemoryContextExtension(Extension):
    async def execute(self, **kwargs):
        self.agent.memory_knowledge = await load_memory_runtime(self.agent)
```

`message_loop_prompts_before/_20_memory_retrieve.py`:

```python
from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.retrieval import retrieve_for_turn, inject_memory_block


class MemoryRetrieveExtension(Extension):
    async def execute(self, **kwargs):
        runtime = getattr(self.agent, "memory_knowledge", None)
        if not runtime or not runtime.can_read:
            return
        memories = await retrieve_for_turn(self.agent, runtime, kwargs)
        inject_memory_block(kwargs, memories)
```

`response_stream/_20_memory_capture_response.py`:

```python
from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.recorder import capture_response_chunk


class MemoryResponseCaptureExtension(Extension):
    async def execute(self, **kwargs):
        runtime = getattr(self.agent, "memory_knowledge", None)
        if runtime:
            capture_response_chunk(runtime, kwargs)
```

`message_loop_end/_20_memory_save.py`:

```python
from helpers.extension import Extension
from usr.plugins.memory_knowledge.helpers.recorder import record_turn
from usr.plugins.memory_knowledge.helpers.extractor import extract_candidates


class MemorySaveExtension(Extension):
    async def execute(self, **kwargs):
        runtime = getattr(self.agent, "memory_knowledge", None)
        if not runtime or not runtime.can_write:
            return
        conversation = await record_turn(self.agent, runtime, kwargs)
        await extract_candidates(self.agent, runtime, conversation)
```

Exact `kwargs` names must be confirmed against the target Agent Zero checkout before implementation. Keep these extensions thin; put version-sensitive logic and defensive fallbacks in `helpers/runtime.py`.

### Required safeguards

- Disable autonomous writes when `memory.agent_memory_context` is missing or `can_write = false`.
- Enforce `max_context_items` and summarize retrieved memory aggressively.
- Never write browser/client-originated data directly to Postgres without server-side validation.
- Never auto-promote candidates unless `can_promote = true` and plugin config explicitly enables it.
- Store raw assistant/user turns before extracting memories so every derived memory has provenance.
- Treat extraction as best effort; write `memory.diagnostic_logs` on failure.
- Buffer stream chunks in memory and write once per turn.
- Add loop guards so memory-injected prompt text is not re-saved as if the user said it.

### Implementation order

1. Refactor the existing scripts into importable plugin helpers.
2. Implement `agent_init` and `message_loop_prompts_before` for read-only autonomous retrieval.
3. Add `response_stream` buffering and `message_loop_end` verbatim conversation recording.
4. Add candidate extraction with evidence links.
5. Add diagnostics and health checks.
6. Add explicit tools for manual retrieve, save, and promote.
7. Validate against a real Agent Zero checkout and adjust extension `kwargs` handling.

# AI operator guide

AI-calls v1 adds optional, best-effort AI helpers to Torque: cached boot
summaries for Engineers/Architects, local semantic recall over selected Torque
text, and metering/doctor visibility for the subsystem. It is deliberately not
load-bearing. If AI is off, stale, missing dependencies, or the provider fails,
Torque keeps using the raw deterministic tools for boot, dispatch, review, and
merge.

## Safety model

- **Off by default.** New installs do not send prompts to any AI provider.
- **Provider egress only after explicit enable.** Remote generation calls happen
  only after an operator enables AI in Settings → AI and configures a provider
  and model. Local embedding setup can still download packages/model weights
  when you install/build the local index.
- **Best effort.** AI failure degrades to raw tools such as session maps,
  journals, decisions, and task reads. It should never block boot, dispatch,
  review, merge, or daemon startup.
- **Privacy/cost aware.** Enabling AI may send selected prompt/source context to
  the configured generation provider and may incur provider costs. The local
  vector index stores selected corpus text/chunks in the Torque SQLite profile
  DB for recall.

## Optional dependencies

Base deploy does not install the embedding/index stack. From a normal operator
shell, install optional AI dependencies with:

```bash
make ai-deps
```

`make ai-deps` installs into Torque's owned runtime venv after `make deps`. The
AI dependency set includes `sentence-transformers` and `sqlite-vec`; first use
of the default embedding model (`BAAI/bge-m3`) can download GB-scale model
weights and may pull in a heavy PyTorch footprint. Plan disk/network time before
building the index.

## Settings → AI

Open Global Settings and choose the **AI** tab.

### Enable AI features

The master toggle controls the subsystem. Leave it off when you want a strictly
raw/deterministic Torque session. When off, semantic recall returns disabled or
empty results and cached boot-summary reads fall back to raw recovery tools.

### Generation provider and model

AI-calls v1 supports:

- `anthropic` — configure a Claude model and API key.
- `openai_compatible` — configure a base URL, model, and optional API key for a
  local or compatible server.

Generation calls are used for cached boot-summary refreshes. They are not made
by the MCP read tools themselves.

### API keys

Provider keys are write-only in the UI. In v1 they are stored in the local
profile SQLite database table `ai_provider_secrets`, not in `GlobalSettings`.
Settings snapshots, export paths, and command/log payloads expose only redacted
metadata such as configured/last-4 status; raw keys should stay out of logs and
exports.

### Embedding model

The default local embedding model is `BAAI/bge-m3`. Local embeddings run on the
operator machine through the optional dependency stack; Torque does not require
a remote embedding API.

Changing the embedding model requires rebuilding the entire vector index. The
UI asks for confirmation and then marks/queues the index rebuild. Until the
rebuild finishes, semantic recall may report `rebuild_pending` or
`model_mismatch` and return no snippets.

### Corpus toggles

The corpus section controls which Torque source families are harvested into the
local index:

- Architect journals
- Engineer journals
- Decisions
- Tasks
- Engineer peer threads

Corpus selection decides what can be indexed; it never bypasses role scope.
Semantic recall still filters snippets through the calling Engineer/Architect's
normal visibility rules before returning text.

### Build / Rebuild index

Use **Build index** for a new index and **Rebuild index** after corpus or model
changes. The status panel shows dependency status, active/desired embedding
model, dimensions, source/chunk counts, pending/stale/error counts, current job,
and last build/error metadata.

## MCP read tools

Engineers and Architects get read-only AI tools documented in
[MCP tools → AI read tools](../reference/mcp-tools.md#ai-read-tools):

- `engineer_semantic_recall` / `architect_semantic_recall` — ranked text
  snippets from the local vector index. Degraded statuses include `disabled`,
  `not_ready`, `dependency_missing`, `rebuild_pending`, and `model_mismatch`.
- `engineer_boot_summary` / `architect_boot_summary` — cached summary payloads
  with readiness statuses such as `ready`, `stale`, `empty`, `disabled`, and
  `error`.

There are no Worker AI recall/summary tools in v1.

## Cached boot summaries

Boot summaries are generated out of band after AI is enabled. Reading
`engineer_boot_summary` or `architect_boot_summary` never performs a live
provider call. If the cache is absent, stale, disabled, refreshing, or errored,
operators and agents should fall back to raw tools (`engineer_session_map`,
`engineer_journal_read`, `architect_journal_read`, `architect_decision_list`,
and scoped task reads).

The boot-summary toggle in Settings → AI can disable this cache independently
of the master AI toggle.

## Doctor checks

Run:

```bash
torque doctor
```

The `[ai]` section reports whether AI is enabled, optional dependency status,
install hint, desired/active embedding model, active dimensions, index status,
chunk counts, model-mismatch chunks, and rebuild-required state.

AI-specific warnings include:

- `ai_optional_deps_missing` — AI is enabled but optional embedding/index
  packages are missing. Run `make ai-deps` from a non-worker shell.
- `ai_index_rebuild_pending` — desired and active embedding/index state diverge
  or the index is marked rebuild-required.
- `ai_index_chunk_model_mismatch` — stored chunks do not match the active
  embedding model/dimensions.

## AI v1 hardening follow-ups

These are known follow-up ideas, not required operator action for v1:

- Hard-terminate a hung in-flight embedding `ProcessPool` worker. The current
  service recovers by resetting the executor.
- Add an index dirty/rerun coalescing flag so overlapping source-mutation and
  model-change rebuilds collapse into one follow-up rebuild.

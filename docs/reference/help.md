# Help docs contract

Torque Help is a read-only documentation lookup surface for users and agents. It is intentionally deterministic: it searches maintained markdown files that ship with Torque and returns concise excerpts with source references instead of reading board state, journals, secrets, logs, or arbitrary filesystem paths.

## Source model

Help content is loaded at query time from this allow-list:

- `mkdocs.yml` navigation entries under `docs/`.
- Root `README.md`.
- Root `AGENTS.md` and `CLAUDE.md`, so agent-facing operating rules stay tied to the maintained source-of-truth mirror.

The Help index excludes runtime `.torque/` state, SQLite databases, logs, credentials, prompt/session caches, and hidden board/user state. `AGENTS.md` should continue to mirror `CLAUDE.md`; do not fork separate Help policy prose when updating operational rules.

The v1 implementation reads markdown directly and does not cache in SQLite. Freshness is exposed through `source_hash`, `updated_at`, and the aggregate `index_hash` fields.

## CLI contract

Human/offline access is available without the daemon:

```bash
torque help list [--audience user|agent|worker|engineer|architect|operator] [--json]
torque help show <topic-id|source/path.md|source/path.md#anchor> [--max-chars 8000] [--json]
torque help search "query text" [--limit 8] [--json]
torque help query "question" [--limit 5] [--json]
```

The daemon also exposes trusted read-only API commands on `/api/cmd` for UI clients:

- `help_list` with optional `audience`.
- `help_show` with `topic`/`path` and optional `max_chars`.
- `help_search` with `query` and optional `limit`.
- `help_query` with `question`/`query` and optional `limit`.

## MCP contract

Help uses the same canonical names for every eligible caller:

- `help_search` browses topics when `query` is omitted and searches when it is
  present.
- `help_get` reads one topic.
- `help_query` answers a question from maintained documentation.

Restricted profiles see Help through the same profile projection gate as other MCP tools. The Help tools require only `observe.self_context` because the implementation itself is hard allow-listed to public maintained documentation and does not widen task, journal, decision, worktree, deploy, or secret visibility.

## Response schema

All Help payloads include:

| Field | Meaning |
|---|---|
| `type` | One of `help_topics`, `help_topic`, `help_search`, `help_query`. |
| `schema_version` | `1` for this contract. |
| `status` | `ok`, `answered`, `no_answer`, `not_found`, or `no_query`. |
| `source_model` | Allow-list, exclusions, cache strategy, and restricted-safe statement. |
| `index_hash` | Aggregate hash of indexed source path/hash pairs when available. |

Topic/result objects include the Panelsmith UI fields:

| Field | Meaning |
|---|---|
| `topic_id` | Stable id derived from the source path. |
| `title` | Topic or section title. |
| `summary` | Short first-paragraph summary. |
| `body_excerpt` / `excerpt` | Bounded markdown or plain-text excerpt. |
| `source_path` | Repo-relative markdown path. |
| `anchor` / `path_anchor` | Section anchor and `path#anchor` reference when applicable. |
| `source_hash` | Short SHA-256 hash of the full source file. |
| `updated_at` | File mtime in UTC ISO format. |
| `audience_tags` | Tags such as `user`, `agent`, `worker`, `engineer`, `architect`, `operator`, `maintainer`. |
| `restricted_safe` | Always `true` for v1 indexed sources. |
| `examples` | Up to a few command/code examples extracted from the source. |
| `sections` | `help_list`/`help_show` section summaries with ids, titles, anchors, and line ranges. |

`help_query` is extractive, not generative. It returns an `answer` string assembled from the top matching snippets and a `sources` list. If no maintained source matches, it returns `status: "no_answer"` with guidance to try broader search terms or `help_list`.

## Maintenance and testing

- Add user/agent-facing Help content by updating the normal docs and `mkdocs.yml` nav; avoid duplicating policy in a separate generated Help corpus.
- Update `AGENTS.md` and `CLAUDE.md` together when operational instructions change.
- Keep Help retrieval deterministic and dependency-light; AI/embeddings may be layered later only as optional fallback after preserving this source-referenced path.
- Tests cover topic discovery, show/search/query, path allow-list safety, MCP projection for restricted Product Manager profiles, and direct MCP dispatch.

## Wave B UI brief

Panelsmith can build a Help panel against the response fields above:

1. Left topic list from `help_list`, with audience chips and source path.
2. Search box backed by `help_search`, preserving `path_anchor` deep links.
3. Topic drawer from `help_show`, rendering `body_excerpt`, examples, sections, source hash, and updated time.
4. Agent-facing “Ask Help” input backed by `help_query`, clearly labeling answers as documentation excerpts and showing sources.
5. No write controls and no board/user state requests in the Help panel.

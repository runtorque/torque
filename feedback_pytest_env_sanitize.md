# feedback_pytest_env_sanitize

Workers/engineers launched from standalone Loom inherit `LOOM_*` runtime
variables. Those vars leak into Python test processes and can retarget config
paths, desktop defaults, or scoped CLI/MCP identity checks.

As of LOOM:298, `make test` self-sanitizes inherited `LOOM_*` variables before
running the suite; manual `env -u LOOM_* ...` prefixes are no longer required.

Audit notes for LOOM:298:
- Observed failures: `LOOM_DATA_DIR`, `LOOM_DESKTOP_PROFILE`,
  `LOOM_DESKTOP_PORT`, and `LOOM_DESKTOP_DATA_DIR`.
- Also test-sensitive or identity/runtime-affecting:
  `LOOM_PORT`, `LOOM_PROFILE`, `LOOM_STANDALONE`, `LOOM_DEFAULT_CMD`,
  `LOOM_BIND_ALL`/`LOOM_BIND_HOST`, `LOOM_CELL_ID`, `LOOM_ENGINEER_ID`,
  `LOOM_ARCHITECT_ID`, and `LOOM_DESKTOP_*` / standalone PTY variants.
- The Makefile intentionally strips every inherited `LOOM_*` var for tests so
  future runtime env additions do not reintroduce this class of failure.

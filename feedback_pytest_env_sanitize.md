# feedback_pytest_env_sanitize

Workers/engineers launched from standalone Torque inherit `TORQUE_*` runtime
variables. Those vars leak into Python test processes and can retarget config
paths, desktop defaults, or scoped CLI/MCP identity checks.

As of TORQUE:298, `make test` self-sanitizes inherited `TORQUE_*` variables before
running the suite; manual `env -u TORQUE_* ...` prefixes are no longer required.

Audit notes for TORQUE:298:
- Observed failures: `TORQUE_DATA_DIR`, `TORQUE_DESKTOP_PROFILE`,
  `TORQUE_DESKTOP_PORT`, and `TORQUE_DESKTOP_DATA_DIR`.
- Also test-sensitive or identity/runtime-affecting:
  `TORQUE_PORT`, `TORQUE_PROFILE`, `TORQUE_STANDALONE`, `TORQUE_DEFAULT_CMD`,
  `TORQUE_BIND_ALL`/`TORQUE_BIND_HOST`, `TORQUE_CELL_ID`, `TORQUE_ENGINEER_ID`,
  `TORQUE_ARCHITECT_ID`, and `TORQUE_DESKTOP_*` / standalone PTY variants.
- The Makefile intentionally strips every inherited `TORQUE_*` var for tests so
  future runtime env additions do not reintroduce this class of failure.

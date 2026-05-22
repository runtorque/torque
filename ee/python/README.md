# Torque EE Python connector skeleton

Enterprise-only Python package boundary for the future outbound Channels cloud connector.

This package is intentionally a no-op in Phase 2:

- it exposes the `torque_ee_connector` import surface expected by the open-core `torque.cloud_hooks` seam;
- it performs no network I/O;
- it is not copied by community `make install` / `make install-standalone` packaging.

Phase 3 wires the real outbound relay client behind this boundary.

"""Phase-2 no-op connector skeleton.

The outbound relay client is intentionally deferred to Phase 3.  This class only
proves the import and lifecycle shape consumed by the open-core hook seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EnterpriseConnector:
    """No-op connector placeholder for the future outbound relay client."""

    context: Any
    started: bool = False
    observed_events: list[str] = field(default_factory=list)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def on_direct_message(self, event: dict[str, Any]) -> None:
        event_type = str((event or {}).get("type", "") or "").strip()
        if event_type:
            self.observed_events.append(event_type)


def create_connector(context: Any) -> EnterpriseConnector:
    """Factory consumed by ``torque.cloud_hooks.start_cloud_connector``."""

    return EnterpriseConnector(context=context)

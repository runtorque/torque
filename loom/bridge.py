"""Backward-compatible iTerm2 bridge adapter surface."""

from . import iterm2_bridge_core as _core
from .iterm2_bridge_core import ITerm2BridgeCore

asyncio = _core.asyncio
iterm2 = _core.iterm2


class ITerm2Adapter(ITerm2BridgeCore):
    """Compatibility wrapper for the classic Python backend.

    The reusable iTerm2 control/monitor implementation now lives in
    ``loom.iterm2_bridge_core`` so the Rust bridge runtime can reuse it
    without turning ``loom.bridge`` into a second backend.
    """

    pass

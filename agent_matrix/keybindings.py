"""Global iTerm2 key bindings for Agent Matrix.

Installs Cmd+Shift+Arrow and Cmd+Shift+B as global shortcuts that invoke
registered RPC functions.  Bindings are added on startup and removed on
shutdown/restart.  Any pre-existing bindings with the same key combos are
saved and restored when our bindings are removed.
"""

import json

import iterm2
import iterm2.binding
import iterm2.keyboard

from .config import log

# Prefix used to identify our RPC functions in binding params
_RPC_PREFIX = "agent_matrix_"

# Binding specs: (character, modifiers, keycode, rpc_invocation)
_BINDING_SPECS = [
    # Cmd+Shift+Down → focus next agent
    (0xF701,
     [iterm2.keyboard.Modifier.COMMAND,
      iterm2.keyboard.Modifier.SHIFT,
      iterm2.keyboard.Modifier.FUNCTION],
     iterm2.keyboard.Keycode.DOWN_ARROW,
     "agent_matrix_focus_next()"),
    # Cmd+Shift+Up → focus prev agent
    (0xF700,
     [iterm2.keyboard.Modifier.COMMAND,
      iterm2.keyboard.Modifier.SHIFT,
      iterm2.keyboard.Modifier.FUNCTION],
     iterm2.keyboard.Keycode.UP_ARROW,
     "agent_matrix_focus_prev()"),
    # Cmd+Shift+B → toggle broadcast
    (ord('B'),
     [iterm2.keyboard.Modifier.COMMAND,
      iterm2.keyboard.Modifier.SHIFT],
     iterm2.keyboard.Keycode.ANSI_B,
     "agent_matrix_toggle_broadcast()"),
]


def _make_bindings():
    """Build KeyBinding objects from specs."""
    bindings = []
    for char, mods, keycode, invocation in _BINDING_SPECS:
        bindings.append(iterm2.binding.KeyBinding(
            character=char,
            modifiers=mods,
            keycode=keycode,
            action=iterm2.binding.BindingAction.INVOKE_SCRIPT_FUNCTION,
            param=invocation,
            version=None,
            label=None,
        ))
    return bindings


def _our_keys():
    """Return the set of key strings we own."""
    return {b.key for b in _make_bindings()}


def _is_ours(binding):
    """Check if a binding was installed by Agent Matrix."""
    return (binding.action == iterm2.binding.BindingAction.INVOKE_SCRIPT_FUNCTION
            and isinstance(binding.param, str)
            and binding.param.startswith(_RPC_PREFIX))


def get_ordered_cells(state):
    """Return all cells with a live session, in group/position order."""
    ordered = []
    for gname in state.groups:
        for aid in state.groups[gname]:
            cell = state.agents.get(aid)
            if cell and cell.session_id:
                ordered.append(cell)
            # Include child terminals after their parent
            for child_id in state._children.get(aid, []):
                child = state.agents.get(child_id)
                if child and child.session_id:
                    ordered.append(child)
    return ordered


async def install(connection):
    """Add our key bindings to the global set.

    Returns the list of displaced bindings for later restoration.
    """
    try:
        existing = await iterm2.binding.async_get_global_key_bindings(
            connection)
    except Exception:
        log.exception("Failed to read global key bindings")
        return []

    our_bindings = _make_bindings()
    our_key_set = {b.key for b in our_bindings}

    # Save displaced bindings (same key combos, not ours from a prior run)
    displaced = [b for b in existing
                 if b.key in our_key_set and not _is_ours(b)]

    # Remove stale Agent Matrix bindings AND displaced originals
    kept = [b for b in existing
            if b.key not in our_key_set and not _is_ours(b)]

    merged = kept + our_bindings

    try:
        await iterm2.binding.async_set_global_key_bindings(
            connection, merged)
        log.info("Global key bindings installed: %d bindings "
                 "(%d displaced, %d kept)",
                 len(our_bindings), len(displaced), len(kept))
    except Exception:
        log.exception("Failed to install global key bindings")
        return []

    return displaced


async def remove(connection, displaced=None):
    """Remove our key bindings and restore any we displaced."""
    try:
        existing = await iterm2.binding.async_get_global_key_bindings(
            connection)
    except Exception:
        log.exception("Failed to read global key bindings for cleanup")
        return

    cleaned = [b for b in existing if not _is_ours(b)]
    restored = cleaned + (displaced or [])

    try:
        await iterm2.binding.async_set_global_key_bindings(
            connection, restored)
        log.info("Global key bindings removed, %d displaced restored",
                 len(displaced or []))
    except Exception:
        log.exception("Failed to remove global key bindings")


async def setup(connection, state, bridge):
    """Register RPC functions and install global key bindings.

    Returns the displaced bindings list for cleanup on shutdown.
    """

    @iterm2.RPC
    async def agent_matrix_focus_next():
        ordered = get_ordered_cells(state)
        if not ordered:
            return
        current_idx = -1
        for i, cell in enumerate(ordered):
            if cell.session_id == state.active_session_id:
                current_idx = i
                break
        next_idx = (current_idx + 1) % len(ordered)
        await bridge.focus_session(ordered[next_idx].session_id)

    @iterm2.RPC
    async def agent_matrix_focus_prev():
        ordered = get_ordered_cells(state)
        if not ordered:
            return
        current_idx = 0
        for i, cell in enumerate(ordered):
            if cell.session_id == state.active_session_id:
                current_idx = i
                break
        prev_idx = (current_idx - 1) % len(ordered)
        await bridge.focus_session(ordered[prev_idx].session_id)

    @iterm2.RPC
    async def agent_matrix_toggle_broadcast():
        # Find the group of the currently active session
        group = None
        for cell in state.agents.values():
            if cell.session_id == state.active_session_id:
                group = cell.group
                break
        if not group:
            return
        # Send action to webview
        msg = json.dumps({
            "type": "action",
            "action": "toggle_broadcast",
            "group": group,
        })
        dead = set()
        for ws in state._ws_clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        state._ws_clients -= dead

    # Register all RPCs
    await agent_matrix_focus_next.async_register(connection, timeout=10)
    await agent_matrix_focus_prev.async_register(connection, timeout=10)
    await agent_matrix_toggle_broadcast.async_register(connection, timeout=10)
    log.info("Agent Matrix RPCs registered")

    # Install global key bindings
    displaced = await install(connection)
    return displaced

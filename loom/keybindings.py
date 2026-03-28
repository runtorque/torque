"""Global iTerm2 key bindings for Loom.

Installs Cmd+Option+Arrow (all four directions) and Cmd+Shift+B as global
shortcuts that invoke registered RPC functions.  Up/Down cycle through all
managed cells; Left/Right cycle through agents only.  Bindings are added on
startup and removed on shutdown/restart.  Any pre-existing bindings with the
same key combos are saved and restored when our bindings are removed.
"""

import json

import iterm2
import iterm2.binding
import iterm2.keyboard

from .config import log

# Prefix used to identify our RPC functions in binding params
_RPC_PREFIX = "loom_"

# Action definitions: action_name → (default_character, default_modifiers,
#                                     default_keycode, rpc_invocation)
_ACTION_DEFAULTS = {
    "focus_next": (0xF701,
                   [iterm2.keyboard.Modifier.COMMAND,
                    iterm2.keyboard.Modifier.OPTION,
                    iterm2.keyboard.Modifier.FUNCTION],
                   iterm2.keyboard.Keycode.DOWN_ARROW,
                   "loom_focus_next()"),
    "focus_prev": (0xF700,
                   [iterm2.keyboard.Modifier.COMMAND,
                    iterm2.keyboard.Modifier.OPTION,
                    iterm2.keyboard.Modifier.FUNCTION],
                   iterm2.keyboard.Keycode.UP_ARROW,
                   "loom_focus_prev()"),
    "next_agent": (0xF703,
                   [iterm2.keyboard.Modifier.COMMAND,
                    iterm2.keyboard.Modifier.OPTION,
                    iterm2.keyboard.Modifier.FUNCTION],
                   iterm2.keyboard.Keycode.RIGHT_ARROW,
                   "loom_next_agent()"),
    "prev_agent": (0xF702,
                   [iterm2.keyboard.Modifier.COMMAND,
                    iterm2.keyboard.Modifier.OPTION,
                    iterm2.keyboard.Modifier.FUNCTION],
                   iterm2.keyboard.Keycode.LEFT_ARROW,
                   "loom_prev_agent()"),
    "toggle_broadcast": (ord('B'),
                         [iterm2.keyboard.Modifier.COMMAND,
                          iterm2.keyboard.Modifier.SHIFT],
                         iterm2.keyboard.Keycode.ANSI_B,
                         "loom_toggle_broadcast()"),
    "close_cell": (ord('C'),
                   [iterm2.keyboard.Modifier.COMMAND,
                    iterm2.keyboard.Modifier.OPTION],
                   iterm2.keyboard.Keycode.ANSI_C,
                   "loom_close_cell()"),
    "add_agent": (ord('A'),
                  [iterm2.keyboard.Modifier.COMMAND,
                   iterm2.keyboard.Modifier.OPTION],
                  iterm2.keyboard.Keycode.ANSI_A,
                  "loom_add_agent()"),
    "add_terminal": (ord('T'),
                     [iterm2.keyboard.Modifier.COMMAND,
                      iterm2.keyboard.Modifier.OPTION],
                     iterm2.keyboard.Keycode.ANSI_T,
                     "loom_add_terminal()"),
}

_ACTION_LABELS = {
    "focus_next": "Focus next cell",
    "focus_prev": "Focus previous cell",
    "next_agent": "Next agent",
    "prev_agent": "Previous agent",
    "toggle_broadcast": "Toggle broadcast",
    "close_cell": "Close cell",
    "add_agent": "Add agent",
    "add_terminal": "Add terminal",
}

# String → iterm2 enum mappings for settings overrides
_MODIFIER_MAP = {
    "command": iterm2.keyboard.Modifier.COMMAND,
    "option": iterm2.keyboard.Modifier.OPTION,
    "shift": iterm2.keyboard.Modifier.SHIFT,
    "control": iterm2.keyboard.Modifier.CONTROL,
    "function": iterm2.keyboard.Modifier.FUNCTION,
}

_KEYCODE_MAP = {kc.name: kc for kc in iterm2.keyboard.Keycode}

# Keycodes that require the FUNCTION modifier
_FUNCTION_KEYCODES = {
    "UP_ARROW", "DOWN_ARROW", "LEFT_ARROW", "RIGHT_ARROW",
    "HOME", "END", "PAGE_UP", "PAGE_DOWN", "FORWARD_DELETE",
}
_FUNCTION_KEYCODES.update(f"F{i}" for i in range(1, 21))


def _resolve_binding_specs(overrides=None):
    """Build binding specs from defaults + any overrides from global settings."""
    specs = []
    for action, (char, mods, keycode, rpc) in _ACTION_DEFAULTS.items():
        if overrides and action in overrides:
            ov = overrides[action]
            char = ov.get("character", char)
            mods = [_MODIFIER_MAP[m] for m in ov.get("modifiers", [])
                    if m in _MODIFIER_MAP]
            kc_name = ov.get("keycode", "")
            if kc_name and kc_name in _KEYCODE_MAP:
                keycode = _KEYCODE_MAP[kc_name]
            # Arrow/function keys need the FUNCTION modifier
            if keycode and keycode.name in _FUNCTION_KEYCODES:
                if iterm2.keyboard.Modifier.FUNCTION not in mods:
                    mods.append(iterm2.keyboard.Modifier.FUNCTION)
        specs.append((char, mods, keycode, rpc))
    return specs


def _make_bindings(specs=None):
    """Build KeyBinding objects from specs or defaults."""
    if specs is None:
        specs = _resolve_binding_specs()
    bindings = []
    for char, mods, keycode, invocation in specs:
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


def get_default_bindings():
    """Return the default keybinding specs as a serializable dict for the frontend."""
    result = {}
    for action, (char, mods, keycode, rpc) in _ACTION_DEFAULTS.items():
        result[action] = {
            "character": char,
            "modifiers": [m.name.lower() for m in mods
                          if m != iterm2.keyboard.Modifier.FUNCTION],
            "keycode": keycode.name if keycode else "",
            "label": _ACTION_LABELS.get(action, action),
        }
    return result


def _is_ours(binding):
    """Check if a binding was installed by Loom."""
    return (binding.action == iterm2.binding.BindingAction.INVOKE_SCRIPT_FUNCTION
            and isinstance(binding.param, str)
            and binding.param.startswith(_RPC_PREFIX))


def get_ordered_cells(state, window_id=None):
    """Return all cells with a live session, in group/position order."""
    ordered = []
    for gname in state.groups:
        for aid in state.groups[gname]:
            cell = state.agents.get(aid)
            if cell and cell.session_id:
                if not window_id or cell.window_id == window_id:
                    ordered.append(cell)
            for child_id in state._children.get(aid, []):
                child = state.agents.get(child_id)
                if child and child.session_id:
                    if not window_id or child.window_id == window_id:
                        ordered.append(child)
    return ordered


def get_ordered_agents(state, window_id=None):
    """Return only agent cells (not terminals) with a live session."""
    ordered = []
    for gname in state.groups:
        for aid in state.groups[gname]:
            cell = state.agents.get(aid)
            if cell and cell.cell_type == 'agent' and cell.session_id:
                if not window_id or cell.window_id == window_id:
                    ordered.append(cell)
    return ordered


def _find_current_agent(state):
    """Find the agent associated with the active session.

    If the active session is a terminal, returns its parent agent.
    Returns (agent_cell, is_on_terminal).
    """
    for cell in state.agents.values():
        if cell.session_id != state.active_session_id:
            continue
        if cell.cell_type == 'agent':
            return cell, False
        elif cell.parent_id:
            parent = state.agents.get(cell.parent_id)
            if parent:
                return parent, True
        return None, True
    return None, False


async def install(connection, specs=None):
    """Add our key bindings to the global set.

    Returns the list of displaced bindings for later restoration.
    """
    try:
        existing = await iterm2.binding.async_get_global_key_bindings(
            connection)
    except Exception:
        log.exception("Failed to read global key bindings")
        return []

    our_bindings = _make_bindings(specs)
    our_key_set = {b.key for b in our_bindings}

    # Save displaced bindings (same key combos, not ours from a prior run)
    displaced = [b for b in existing
                 if b.key in our_key_set and not _is_ours(b)]

    # Remove stale Loom bindings AND displaced originals
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


async def reinstall(connection, displaced, overrides=None):
    """Remove current bindings and install new ones with optional overrides.

    Returns new displaced bindings list.
    """
    await remove(connection, displaced)
    specs = _resolve_binding_specs(overrides)
    return await install(connection, specs)


async def setup(connection, state, bridge, overrides=None):
    """Register RPC functions and install global key bindings.

    Returns the displaced bindings list for cleanup on shutdown.
    """

    @iterm2.RPC
    async def loom_focus_next():
        wid = state.current_window_id
        ordered = get_ordered_cells(state, wid)
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
    async def loom_focus_prev():
        wid = state.current_window_id
        ordered = get_ordered_cells(state, wid)
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
    async def loom_next_agent():
        wid = state.current_window_id
        ordered = get_ordered_agents(state, wid)
        if not ordered:
            return
        # Find current agent (or parent agent if on a terminal)
        current_agent, _ = _find_current_agent(state)
        current_idx = -1
        if current_agent:
            for i, cell in enumerate(ordered):
                if cell.id == current_agent.id:
                    current_idx = i
                    break
        next_idx = (current_idx + 1) % len(ordered)
        await bridge.focus_session(ordered[next_idx].session_id)

    @iterm2.RPC
    async def loom_prev_agent():
        wid = state.current_window_id
        ordered = get_ordered_agents(state, wid)
        if not ordered:
            return
        current_agent, _ = _find_current_agent(state)
        current_idx = 0
        if current_agent:
            for i, cell in enumerate(ordered):
                if cell.id == current_agent.id:
                    current_idx = i
                    break
        prev_idx = (current_idx - 1) % len(ordered)
        await bridge.focus_session(ordered[prev_idx].session_id)

    async def _send_action(action, **fields):
        """Send an action message to all connected WebSocket clients."""
        msg = json.dumps({"type": "action", "action": action, **fields})
        dead = set()
        for ws in state._ws_clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        state._ws_clients -= dead

    @iterm2.RPC
    async def loom_toggle_broadcast():
        # Find the group of the currently active session
        group = None
        for cell in state.agents.values():
            if cell.session_id == state.active_session_id:
                group = cell.group
                break
        if not group:
            return
        await _send_action("toggle_broadcast", group=group)

    @iterm2.RPC
    async def loom_close_cell():
        for cell in state.agents.values():
            if cell.session_id == state.active_session_id:
                await _send_action("close_cell", cell_id=cell.id)
                return

    @iterm2.RPC
    async def loom_add_agent():
        # Find the group of the currently active session
        group = None
        for cell in state.agents.values():
            if cell.session_id == state.active_session_id:
                group = cell.group
                break
        if not group:
            # Fall back to first group
            groups = list(state.groups.keys())
            group = groups[0] if groups else None
        if group:
            await _send_action("add_agent", group=group)

    @iterm2.RPC
    async def loom_add_terminal():
        # Find the agent associated with the active session
        current_agent, _ = _find_current_agent(state)
        if current_agent:
            await _send_action("add_terminal",
                               group=current_agent.group,
                               parent_id=current_agent.id)

    # Register all RPCs
    await loom_focus_next.async_register(connection, timeout=10)
    await loom_focus_prev.async_register(connection, timeout=10)
    await loom_next_agent.async_register(connection, timeout=10)
    await loom_prev_agent.async_register(connection, timeout=10)
    await loom_toggle_broadcast.async_register(connection, timeout=10)
    await loom_close_cell.async_register(connection, timeout=10)
    await loom_add_agent.async_register(connection, timeout=10)
    await loom_add_terminal.async_register(connection, timeout=10)
    log.info("Loom RPCs registered")

    # Install global key bindings
    specs = _resolve_binding_specs(overrides)
    displaced = await install(connection, specs)
    return displaced

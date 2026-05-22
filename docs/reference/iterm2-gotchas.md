# iTerm2 API gotchas

Implementation notes for Torque's iTerm2 adapter and Toolbelt integration.

- **Tab color**: Must set all three variants (`set_tab_color`, `set_tab_color_light`, `set_tab_color_dark`) plus their `set_use_tab_color*` flags — otherwise color won't show depending on user's light/dark mode settings.
- **Tab reorder**: Use `window.async_set_tabs(list)` with the full tab list in desired order. No per-tab move API.
- **PromptMonitor**: Only fires when shell integration is active. For TUI apps (Claude Code, vim), the prompt won't fire until the app exits.
- **VariableMonitor**: `jobName` and `path` are session-scoped variables. Monitor triggers on change, not on initial value — seed with `async_get_variable()` first.
- **Port conflicts**: If the daemon crashes on start, an old instance is likely still holding port 18932. Run `make stop` first.
- **Restart**: `os.execv(sys.executable, [sys.executable] + sys.argv)` replaces the process in-place. Must set `reuse_address=True` on the TCPSite so the new process can bind immediately.
- **Global key bindings**: `async_set_global_key_bindings` is replace-all — must read→merge→write. Displaced user bindings are saved and restored on shutdown. On crash, stale `torque_*` bindings are cleaned up on next startup. Bindings are configurable via global settings — `keybindings.reinstall()` swaps them at runtime without re-registering RPCs.
- **RPC invocation**: `INVOKE_SCRIPT_FUNCTION` param format is `"function_name()"`. RPCs are registered via `@iterm2.RPC` decorator + `async_register(connection)`. Arrow key bindings require the `FUNCTION` modifier flag alongside other modifiers.
- **Menu shortcut precedence**: iTerm2's built-in menu shortcuts (e.g., Cmd+Shift+Left/Right for Move Tab) override global key bindings. Use Cmd+Option for arrow bindings to avoid conflicts.
- **Right-click menus**: `window.confirm()` and native context menus don't work in WKWebView — use custom `contextmenu` event handlers with positioned `<div>` elements instead.

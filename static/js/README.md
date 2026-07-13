# Frontend module contract

Torque's frontend is intentionally a no-build-step application. JavaScript is
loaded as classic scripts from `webview.html`; stylesheet order is likewise
defined by the `<link>` elements in that file.

## Rules

1. `webview.html` is the canonical runtime load order. A script may call a
   symbol from an earlier script during evaluation. Symbols from later scripts
   may only be referenced from functions that run after boot.
2. Existing public globals and inline-handler names are compatibility APIs.
   Physical module extraction must preserve those names until a separate,
   reviewed namespace migration provides adapters.
3. Behavior-preserving extraction is the default: move cohesive functions and
   their private state without changing signatures, DOM structure, commands,
   or rendering behavior in the same change.
4. WebSocket state mutation remains owned by `ws.js` and its `ws/` modules.
   Rendering modules consume state; they do not create a second store.
5. Live surfaces must preserve scroll, focus, caret, drafts, expanded rows,
   selection, and terminal tail intent across delta-driven rerenders.
6. New first-party modules belong in a feature directory (`agent-panel/`,
   `terminal/`, `board/`, `modals/`, or `ws/`). Vendor scripts stay isolated in
   `vendor/`.
7. Every extraction updates the runtime script-order test and relevant VM test
   harnesses, runs the full frontend suite, and receives a live browser smoke.

## Runtime groups

- **State and infrastructure:** `constants.js`, `compact.js`, `ws.js`,
  `keybindings.js`, relay and shared rendering helpers.
- **Agent workspace:** `render.js`, `grid/`, `agent-detail.js`,
  `agent-focus.js`, `canvas.js`, and commands.
- **Terminal and operator input:** `terminal.js` plus `terminal/` modules.
- **Panels and forms:** modal modules, Board modules, feature panels,
  `agent_panel.js` plus `agent-panel/` modules, and `panel_manager.js`.
- **Boot:** `main.js` is last. It may assume every runtime module has loaded.

The stylesheet cascade is also explicit in `webview.html`: tokens/base,
workspace/grid, modals, workspace shell, Board and operator panels, Agent
panel, desktop features, then feature panels. `static/style.css` is retained
only as a compatibility `@import` entrypoint; production loads the files in
`static/styles/` directly.

The source manifest used by tests is derived from `webview.html`; do not create
a second handwritten production manifest.

# Stylesheet cascade contract

Torque loads these files directly from `webview.html` in this order:

1. `tokens-base.css`
2. `workspace-grid.css`
3. `modals.css`
4. `workspace-shell.css`
5. `board-panels.css`
6. `agent-panel.css`
7. `desktop-features.css`
8. `feature-panels.css`

The split is contiguous from the former monolithic stylesheet. Preserve this
order: later selectors intentionally override earlier shared primitives.
`../style.css` mirrors the list with `@import` for compatibility, but the app
does not load that indirection.

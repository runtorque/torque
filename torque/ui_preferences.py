"""Bounded, versioned presentation preferences shared by loading and commands."""

REACT_PANELS = frozenset({"board", "agents", "planning", "control"})
REACT_CONTROL_TABS = frozenset({
    "mission", "activity", "history", "context", "logs", "chat", "pipelines",
    "actions", "catalog", "settings", "help",
})


def normalize_react_workspace_state(raw) -> dict:
    if not isinstance(raw, dict) or type(raw.get("version")) is not int:
        return {}
    panel, tab = raw.get("activePanel"), raw.get("controlTab")
    if (raw["version"] != 1 or not isinstance(panel, str)
            or not isinstance(tab, str) or panel not in REACT_PANELS
            or tab not in REACT_CONTROL_TABS):
        return {}
    return {"version": 1, "activePanel": panel, "controlTab": tab}

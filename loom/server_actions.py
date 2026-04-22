"""YAML serialization helpers for Loom action editor commands."""

from __future__ import annotations

import yaml


class _BlockStr(str):
    """Tagged string subclass so the YAML representer emits block scalar."""


_ACTION_KEY_ORDER = [
    "name", "description", "agent", "group", "worktree",
    "auto_close_on_done", "disable_role_preamble",
    "review_required_above_loc", "prompt", "labels", "transitions",
    "terminals",
]


class _ActionDumper(yaml.SafeDumper):
    pass


_ActionDumper.add_representer(
    _BlockStr,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|"
    ),
)


def _action_to_yaml(name: str, data: dict) -> str:
    """Convert an action data dict to YAML text."""
    doc = {"name": name}
    if data.get("description"):
        doc["description"] = data["description"]

    agent = data.get("agent", {})
    if isinstance(agent, str):
        if agent:
            doc["agent"] = agent
    else:
        agent_keys = (
            "name_prefix", "command", "directory", "profile",
            "shell", "tab_color",
        )
        agent_block = {key: agent[key] for key in agent_keys if agent.get(key)}
        if agent_block:
            doc["agent"] = agent_block

    if data.get("group"):
        doc["group"] = data["group"]
    if data.get("worktree"):
        doc["worktree"] = True
    if data.get("auto_close_on_done"):
        doc["auto_close_on_done"] = True
    if data.get("disable_role_preamble"):
        doc["disable_role_preamble"] = True
    if data.get("review_required_above_loc") is not None:
        try:
            doc["review_required_above_loc"] = int(
                data["review_required_above_loc"])
        except (TypeError, ValueError):
            pass

    prompt = data.get("prompt", "")
    if prompt:
        doc["prompt"] = _BlockStr(prompt.rstrip("\n") + "\n")

    labels = data.get("labels", [])
    if labels:
        doc["labels"] = labels

    transitions = data.get("transitions", [])
    if transitions:
        clean = []
        for transition in transitions:
            if isinstance(transition, dict):
                if transition.get("ask"):
                    entry = {"ask": True}
                    if transition.get("when"):
                        entry["when"] = transition["when"]
                    clean.append(entry)
                elif transition.get("action"):
                    entry = {"action": transition["action"]}
                    if transition.get("when"):
                        entry["when"] = transition["when"]
                    if transition.get("status"):
                        entry["status"] = transition["status"]
                    if transition.get("target"):
                        entry["target"] = transition["target"]
                    clean.append(entry)
        if clean:
            doc["transitions"] = clean

    terminals = data.get("terminals", [])
    if terminals:
        clean = []
        for terminal in terminals:
            entry = {"name": terminal.get("name", "shell")}
            if terminal.get("command"):
                entry["command"] = terminal["command"]
            clean.append(entry)
        doc["terminals"] = clean

    ordered = {key: doc[key] for key in _ACTION_KEY_ORDER if key in doc}
    return yaml.dump(
        ordered,
        Dumper=_ActionDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

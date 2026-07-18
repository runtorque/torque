"""Provider-neutral Torque workspace skill definitions and installers."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent


SKILLS = {
    "torque-status": {
        "frontmatter": {
            "name": "torque-status",
            "description": (
                "Show current Torque agent context, linked task, "
                "and pipeline state"
            ),
            "allowed-tools": "mcp__torque__context",
        },
        "body": dedent("""\
            Show the current Torque agent context using the context MCP tool.

            Format the output clearly:
            - Agent name, group, and status
            - Current task title, lane, and action
            - Pipeline info (parent task, depth) if this is a derived task
            - Worktree branch and diff stats if in a worktree
        """),
    },
    "torque-board": {
        "frontmatter": {
            "name": "torque-board",
            "description": "Show the Torque task board with all lanes and tasks",
            "allowed-tools": "Bash",
        },
        "body": dedent("""\
            Show the current Torque task board.

            ## Board state
            !`torque board list`

            Summarize the board state: how many tasks in each lane,
            any tasks that need attention (blocked/error labels),
            and what's currently in progress.
        """),
    },
    "torque-done": {
        "frontmatter": {
            "name": "torque-done",
            "description": "Mark the current Torque task as complete",
            "allowed-tools": "mcp__torque__task_complete",
            "argument-hint": "[completion message]",
        },
        "body": dedent("""\
            Mark the current task as complete using the task_complete MCP tool.

            If $ARGUMENTS is provided, use it as the completion message.
            Otherwise, write a brief summary of what was accomplished.
        """),
    },
}


def render_skill_md(skill: dict) -> str:
    """Render one workspace skill as Markdown with YAML frontmatter."""

    lines = ["---"]
    for key, value in skill["frontmatter"].items():
        lines.append(f"{key}: {value}")
    lines.extend(["---", "", skill["body"]])
    return "\n".join(lines)


def install_torque_skills(working_dir: str, relative_root: str) -> bool:
    """Install the canonical Torque skills for one provider workspace."""

    skills_dir = Path(working_dir) / relative_root
    installed = 0
    for name, skill in SKILLS.items():
        try:
            skill_dir = skills_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(render_skill_md(skill))
            installed += 1
        except Exception:
            # Skills are a best-effort convenience. MCP and session startup
            # must remain available when a workspace is read-only.
            continue
    return installed > 0


def uninstall_torque_skills(working_dir: str, relative_root: str) -> None:
    """Remove Torque-managed skill files while preserving user content."""

    skills_dir = Path(working_dir) / relative_root
    if not skills_dir.exists():
        return
    try:
        for child in skills_dir.iterdir():
            if not child.is_dir() or child.name not in SKILLS:
                continue
            skill_md = child / "SKILL.md"
            if skill_md.exists():
                skill_md.unlink()
            try:
                child.rmdir()
            except OSError:
                # Preserve directories containing user-added files.
                pass
    except Exception:
        # Best-effort cleanup mirrors installation semantics.
        pass

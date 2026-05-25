"""GitHub Issues + Projects v2 adapter for Torque board sync."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import urlparse

_TORQUE_MARKER_RE = re.compile(
    r"<!--\s*torque-sync:v(?P<version>\d+)\s+task_id=(?P<task_id>[^\s>]+)"
    r"(?:\s+group=(?P<group>[^>]+?))?\s*-->",
    re.IGNORECASE,
)
_TORQUE_FOOTER_RE = re.compile(
    r"\n*---\nSynced from Torque task [^\n]+\.\n"
    r"<!--\s*torque-sync:v\d+\s+task_id=[^>]+?-->\s*$",
    re.IGNORECASE,
)
_GITHUB_ISSUE_URL_RE = re.compile(
    r"^https?://github\.com/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)"
    r"(?:[/?#].*)?$",
    re.IGNORECASE,
)
_GITHUB_EXTERNAL_ID_RE = re.compile(
    r"^(?P<repo>[^/\s]+/[^/\s#]+)#(?P<number>\d+)$",
    re.IGNORECASE,
)
_CLOSING_KEYWORDS_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
    r"(?P<ref>[\w.-]+/[\w.-]+#\d+|#\d+)",
    re.IGNORECASE,
)

GhRunner = Callable[[Sequence[str], str | None], Awaitable[dict]]


@dataclass
class GitHubSyncSettings:
    repo: str = ""
    project_owner: str = ""
    project_number: int = 0
    project_id: str = ""
    status_field_name: str = "Status"
    lane_status_map: dict[str, str] = field(default_factory=dict)
    sync_default: str = "linked"
    close_issues_via_pr: bool = True
    create_missing_labels: bool = True
    assignee_map: dict[str, str] = field(default_factory=dict)
    enabled: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_json_items(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "fields", "nodes", "projects"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(data.get("data"), dict):
        return _as_json_items(data["data"])
    return []


def _safe_json_loads(raw: str, default):
    try:
        value = json.loads(raw or "")
    except (json.JSONDecodeError, TypeError):
        return default
    return value


def _command_output(result: dict) -> str:
    return str(result.get("stderr") or result.get("stdout") or "").strip()


def _github_external_id(repo: str, number: int) -> str:
    repo = str(repo or "").strip()
    try:
        number = int(number or 0)
    except (TypeError, ValueError):
        number = 0
    return f"{repo}#{number}" if repo and number else ""


def _ok(phase: str, **extra) -> dict:
    payload = {"ok": True, "phase": phase}
    payload.update(extra)
    return payload


def _error(phase: str, error: str, **extra) -> dict:
    payload = {"ok": False, "phase": phase, "error": str(error or "").strip()}
    payload.update(extra)
    return payload


def _external_not_found_error(error: str) -> bool:
    lower = str(error or "").lower()
    return any(
        needle in lower
        for needle in (
            "could not resolve to an issue",
            "could not resolve to a node",
            "not found",
            "no issue matches",
            "could not resolve",
            "404",
        )
    )


def _diff_items(changes: dict) -> list[dict]:
    items = []
    for field, change in (changes or {}).items():
        if not isinstance(change, dict):
            continue
        items.append({
            "field": field,
            "local": change.get("local"),
            "remote": change.get("remote"),
        })
    return items


async def default_gh_runner(args: Sequence[str], cwd: str | None = None) -> dict:
    """Run ``gh`` asynchronously and return captured process metadata."""
    cmd = ["gh", *[str(arg) for arg in args]]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
        )
        stdout, stderr = await proc.communicate()
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "cmd": cmd,
            "cwd": cwd or "",
        }
    except FileNotFoundError as exc:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
            "cmd": cmd,
            "cwd": cwd or "",
            "missing": True,
        }


def github_settings(group_settings) -> GitHubSyncSettings:
    """Extract nested GitHub board-sync settings with flat-key fallback."""
    nested = getattr(group_settings, "board_sync_github", {}) or {}
    if not isinstance(nested, dict):
        nested = {}

    def get(key: str, default=None):
        if key in nested:
            return nested.get(key)
        return getattr(group_settings, key, default)

    try:
        project_number = int(get("github_project_number", 0) or 0)
    except (TypeError, ValueError):
        project_number = 0

    lane_status_map = get("github_lane_status_map", {}) or {}
    assignee_map = get("github_assignee_map", {}) or {}

    return GitHubSyncSettings(
        repo=str(get("github_repo", "") or "").strip(),
        project_owner=str(get("github_project_owner", "") or "").strip(),
        project_number=max(0, project_number),
        project_id=str(get("github_project_id", "") or "").strip(),
        status_field_name=str(
            get("github_project_status_field", "Status") or "Status"
        ).strip() or "Status",
        lane_status_map=lane_status_map if isinstance(lane_status_map, dict) else {},
        sync_default=str(get("github_sync_default", "linked") or "linked").strip()
        or "linked",
        close_issues_via_pr=bool(get("github_close_issues_via_pr", True)),
        create_missing_labels=bool(get("github_create_missing_labels", True)),
        assignee_map=assignee_map if isinstance(assignee_map, dict) else {},
        enabled=bool(getattr(group_settings, "board_sync_enabled", False)),
    )


def parse_github_issue_ref(
    *,
    external_id: str = "",
    external_url: str = "",
    board_sync: dict | None = None,
) -> dict:
    """Return repo/number/url metadata from Torque link or board_sync fields."""
    board_sync = board_sync if isinstance(board_sync, dict) else {}
    github = board_sync.get("github") if isinstance(board_sync.get("github"), dict) else {}
    repo = str(github.get("issue_repo", "") or "").strip()
    number = github.get("issue_number")
    url = str(github.get("issue_url", "") or "").strip()

    if not repo or not number:
        match = _GITHUB_EXTERNAL_ID_RE.match(str(external_id or "").strip())
        if match:
            repo = repo or match.group("repo")
            number = number or int(match.group("number"))
    if (not repo or not number) and external_url:
        match = _GITHUB_ISSUE_URL_RE.match(str(external_url or "").strip())
        if match:
            repo = repo or match.group("repo")
            number = number or int(match.group("number"))
            url = url or external_url
    try:
        number = int(number or 0)
    except (TypeError, ValueError):
        number = 0
    if repo and number and not url:
        url = f"https://github.com/{repo}/issues/{number}"
    return {"issue_repo": repo, "issue_number": number, "issue_url": url}


def parse_torque_sync_marker(body: str) -> dict:
    match = _TORQUE_MARKER_RE.search(body or "")
    if not match:
        return {}
    return {
        "version": int(match.group("version") or 1),
        "task_id": (match.group("task_id") or "").strip(),
        "group": (match.group("group") or "").strip(),
    }


def strip_torque_sync_footer(body: str) -> str:
    return _TORQUE_FOOTER_RE.sub("", body or "").rstrip()


def render_issue_body(task) -> str:
    description = strip_torque_sync_footer(str(getattr(task, "description", "") or ""))
    task_id = str(getattr(task, "id", "") or "").strip()
    group = str(getattr(task, "group", "") or "").strip()
    marker_group = f" group={group}" if group else ""
    footer = (
        f"---\nSynced from Torque task {task_id}.\n"
        f"<!-- torque-sync:v1 task_id={task_id}{marker_group} -->"
    )
    return f"{description}\n\n{footer}".strip() if description else footer


def _label_key(label: str) -> str:
    return str(label or "").strip().casefold()


def _label_already_exists_error(error: str) -> bool:
    lower = str(error or "").casefold()
    return "already exists" in lower or "name already exists" in lower


def _user_labels(task) -> list[str]:
    labels = []
    seen = set()
    for label in getattr(task, "labels", []) or []:
        label = str(label or "").strip()
        key = _label_key(label)
        if not label or key.startswith("torque:") or key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def _assignees(task, settings: GitHubSyncSettings) -> list[str]:
    for key in (getattr(task, "agent_id", ""), getattr(task, "assigned_engineer_id", "")):
        login = settings.assignee_map.get(str(key or "").strip())
        if login:
            return [str(login).strip()]
    return []


def _lane_status(task, settings: GitHubSyncSettings) -> str:
    lane = str(getattr(task, "lane", "") or "").strip()
    return str(settings.lane_status_map.get(lane, lane) or "").strip()


def compute_outbound_hash(task, settings: GitHubSyncSettings | None = None) -> str:
    settings = settings or GitHubSyncSettings()
    payload = {
        "title": str(getattr(task, "task", "") or ""),
        "body": render_issue_body(task),
        "labels": _user_labels(task),
        "assignees": _assignees(task, settings),
        "lane_status": _lane_status(task, settings),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_ref_for_closing(issue: dict, *, default_repo: str = "") -> str:
    repo = str(
        issue.get("issue_repo")
        or issue.get("repo")
        or issue.get("repository")
        or ""
    ).strip()
    number = issue.get("issue_number", issue.get("number", ""))
    try:
        number = int(number or 0)
    except (TypeError, ValueError):
        number = 0
    if not number:
        ref = parse_github_issue_ref(
            external_id=str(issue.get("external_id", "") or ""),
            external_url=str(issue.get("external_url", "") or ""),
            board_sync=issue.get("board_sync", {}) if isinstance(issue.get("board_sync"), dict) else {},
        )
        repo = repo or ref["issue_repo"]
        number = ref["issue_number"]
    if not repo or not number:
        return ""
    base_repo = str(
        issue.get("base_repo")
        or issue.get("pr_repo")
        or issue.get("target_repo")
        or default_repo
        or ""
    ).strip()
    return f"#{number}" if base_repo and repo.lower() == base_repo.lower() else f"{repo}#{number}"


def _closing_refs_already_present(body: str) -> set[str]:
    refs: set[str] = set()
    for match in _CLOSING_KEYWORDS_RE.finditer(body or ""):
        refs.add(match.group("ref").lower())
    return refs


def append_closing_refs_to_body(
    pr_body: str,
    linked_issues: list[dict],
    *,
    enabled: bool = True,
    default_repo: str = "",
) -> str:
    """Append missing GitHub closing-keyword refs to a PR body."""
    body = pr_body or ""
    if not enabled:
        return body
    existing = _closing_refs_already_present(body)
    refs: list[str] = []
    seen: set[str] = set()
    for issue in linked_issues or []:
        if not isinstance(issue, dict):
            continue
        ref = issue_ref_for_closing(issue, default_repo=default_repo)
        if not ref:
            continue
        full_ref = ref.lower()
        short_ref = full_ref[full_ref.rfind("#"):] if "#" in full_ref else full_ref
        if full_ref in existing or short_ref in existing or full_ref in seen:
            continue
        seen.add(full_ref)
        refs.append(ref)
    if not refs:
        return body
    section = "Linked Torque issues:\n" + "\n".join(f"- Closes {ref}" for ref in refs)
    return f"{body.rstrip()}\n\n{section}" if body.strip() else section


class GitHubBoardSyncProvider:
    """GitHub implementation of the BoardSyncProvider protocol."""

    name = "github"

    def __init__(self, runner: GhRunner | None = None, cwd: str | None = None):
        self.runner = runner or default_gh_runner
        self.cwd = cwd

    async def _gh(self, phase: str, *args: str, cwd: str | None = None) -> dict:
        result = await self.runner([str(arg) for arg in args], cwd if cwd is not None else self.cwd)
        if result.get("returncode") != 0:
            err = _command_output(result) or "GitHub CLI command failed"
            return _error(phase, err, command=result.get("cmd", ["gh", *args]))
        return _ok(
            phase,
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            command=result.get("cmd", ["gh", *args]),
        )

    async def _json_gh(self, phase: str, *args: str) -> dict:
        result = await self._gh(phase, *args)
        if not result.get("ok"):
            return result
        data = _safe_json_loads(result.get("stdout", ""), None)
        if data is None:
            return _error(phase, "GitHub CLI returned invalid JSON.")
        result["data"] = data
        return result

    async def preflight(self, group_settings) -> dict:
        settings = github_settings(group_settings)
        version = await self._gh("gh_version", "--version")
        if not version.get("ok"):
            return _error(
                "gh_version",
                "GitHub CLI (gh) is not installed or not executable.",
            )

        auth = await self._gh("auth", "auth", "status")
        if not auth.get("ok"):
            return _error(
                "auth",
                f"GitHub CLI authentication failed: {auth.get('error', '')}",
            )

        repo = settings.repo
        if repo:
            repo_view = await self._json_gh(
                "repo",
                "repo",
                "view",
                repo,
                "--json",
                "nameWithOwner,url",
            )
        else:
            repo_view = await self._json_gh(
                "repo",
                "repo",
                "view",
                "--json",
                "nameWithOwner,url",
            )
        if not repo_view.get("ok"):
            return _error(
                "repo",
                f"Not a GitHub repository or cannot inspect repo: {repo_view.get('error', '')}",
            )
        repo_meta = repo_view.get("data") if isinstance(repo_view.get("data"), dict) else {}
        repo = str(repo_meta.get("nameWithOwner") or repo or "").strip()
        if not repo:
            return _error("repo", "Could not resolve GitHub repository.")

        payload = {
            "provider": self.name,
            "repo": repo,
            "repo_url": str(repo_meta.get("url") or f"https://github.com/{repo}"),
        }

        if settings.project_owner and settings.project_number:
            scope_error = self._missing_project_scope(auth.get("stdout", "") + auth.get("stderr", ""))
            if scope_error:
                return _error("project_scope", scope_error, **payload)
            project = await self._resolve_project(settings)
            if not project.get("ok"):
                project.update(payload)
                return project
            payload.update({
                key: value for key, value in project.items()
                if key not in {"ok", "phase"}
            })

        return _ok("preflight", **payload)

    @staticmethod
    def _missing_project_scope(auth_output: str) -> str:
        lower = (auth_output or "").lower()
        if "token scopes" not in lower and "oauth scopes" not in lower:
            return ""
        scopes = set(re.findall(r"[a-z0-9:_-]+", lower))
        if "project" not in scopes:
            return (
                "GitHub Projects v2 sync requires the 'project' scope. "
                "Run: gh auth refresh -s project"
            )
        return ""

    async def _resolve_project(self, settings: GitHubSyncSettings) -> dict:
        project = await self._json_gh(
            "project",
            "project",
            "view",
            str(settings.project_number),
            "--owner",
            settings.project_owner,
            "--format",
            "json",
        )
        if not project.get("ok"):
            return project
        project_data = project.get("data") if isinstance(project.get("data"), dict) else {}
        project_name = str(
            project_data.get("title")
            or project_data.get("name")
            or ""
        ).strip()
        project_url = str(project_data.get("url") or "").strip()
        project_id = str(
            project_data.get("id")
            or project_data.get("node_id")
            or settings.project_id
            or ""
        ).strip()
        if not project_id:
            return _error("project", "Could not resolve GitHub Project v2 node ID.")

        fields = await self._json_gh(
            "project_fields",
            "project",
            "field-list",
            str(settings.project_number),
            "--owner",
            settings.project_owner,
            "--format",
            "json",
        )
        if not fields.get("ok"):
            return fields
        field = self._find_status_field(fields.get("data"), settings.status_field_name)
        if not field:
            return _error(
                "project_status_field",
                f"GitHub Project Status field '{settings.status_field_name}' was not found.",
            )
        return _ok(
            "project",
            project_owner=settings.project_owner,
            project_number=settings.project_number,
            project_id=project_id,
            project_name=project_name,
            project_url=project_url,
            status_field_id=str(field.get("id") or ""),
            status_field_name=str(field.get("name") or settings.status_field_name),
            status_options=self._status_options(field),
            status_options_list=self._status_option_items(field),
        )

    @staticmethod
    def _find_status_field(data: Any, name: str) -> dict:
        wanted = str(name or "Status").strip().lower()
        for field in _as_json_items(data):
            if str(field.get("name") or "").strip().lower() == wanted:
                return field
        return {}

    @staticmethod
    def _status_options(field: dict) -> dict[str, str]:
        return {
            item["name"]: item["id"]
            for item in GitHubBoardSyncProvider._status_option_items(field)
            if item.get("name") and item.get("id")
        }

    @staticmethod
    def _status_option_items(field: dict) -> list[dict]:
        settings = field.get("settings") or {}
        if isinstance(settings, str):
            settings = _safe_json_loads(settings, {})
        if not isinstance(settings, dict):
            settings = {}
        options = field.get("options") or settings.get("options") or []
        if isinstance(options, str):
            options = _safe_json_loads(options, [])
        out = []
        for option in options if isinstance(options, list) else []:
            if not isinstance(option, dict):
                continue
            name = str(option.get("name") or "").strip()
            oid = str(option.get("id") or option.get("optionId") or "").strip()
            if name and oid:
                out.append({"name": name, "id": oid})
        return out

    async def list_projects(self, owner: str | None = None) -> list[dict]:
        """Return GitHub Projects v2 visible to the authenticated gh user."""
        owner_arg = str(owner or "").strip() or "@me"
        listed = await self._json_gh(
            "list_projects",
            "project",
            "list",
            "--owner",
            owner_arg,
            "--format",
            "json",
            "--limit",
            "100",
        )
        if not listed.get("ok"):
            return [listed]
        projects = []
        for item in _as_json_items(listed.get("data")):
            name = str(item.get("title") or item.get("name") or "").strip()
            try:
                number = int(item.get("number") or 0)
            except (TypeError, ValueError):
                number = 0
            owner_value = item.get("owner")
            if isinstance(owner_value, dict):
                owner_value = (
                    owner_value.get("login")
                    or owner_value.get("name")
                    or owner_value.get("id")
                    or ""
                )
            owner_name = str(
                item.get("owner_login")
                or item.get("ownerLogin")
                or item.get("ownerName")
                or owner_value
                or owner_arg
                or ""
            ).strip()
            project_id = str(
                item.get("id")
                or item.get("node_id")
                or item.get("nodeId")
                or ""
            ).strip()
            url = str(item.get("url") or "").strip()
            if not number and not project_id and not name:
                continue
            projects.append({
                "ok": True,
                "provider": self.name,
                "number": number,
                "name": name,
                "title": name,
                "owner": owner_name,
                "id": project_id,
                "url": url,
            })
        return projects

    async def push_task(self, task, group_settings) -> dict:
        settings = github_settings(group_settings)
        existing = dict(getattr(task, "board_sync", {}) or {})
        if not settings.enabled:
            return self._sync_error(
                task,
                "disabled",
                "Board sync is disabled for push + manual reconcile.",
            )
        issue = parse_github_issue_ref(
            external_id=getattr(task, "external_id", ""),
            external_url=getattr(task, "external_url", ""),
            board_sync=existing,
        )
        repo = settings.repo or issue["issue_repo"]
        if not repo:
            return self._sync_error(task, "repo", "GitHub repository is not configured.")

        labels = _user_labels(task)
        label_check = await self._ensure_labels(repo, labels, settings)
        if not label_check.get("ok"):
            return self._sync_error(task, label_check.get("phase", "labels"), label_check.get("error", ""))

        outbound_hash = compute_outbound_hash(task, settings)
        github_sync = existing.get("github") if isinstance(existing.get("github"), dict) else {}
        issue_number = int(issue["issue_number"] or github_sync.get("issue_number") or 0)
        if issue_number and existing.get("last_synced_hash") == outbound_hash \
                and existing.get("sync_state") != "error":
            skipped = dict(existing)
            skipped.update({"sync_state": "idle", "last_error": "", "skipped": True})
            return skipped

        if issue_number:
            pushed = await self._update_issue(repo, issue_number, task, labels, settings)
        else:
            pushed = await self._create_issue(repo, task, labels, settings)
        if not pushed.get("ok"):
            return self._sync_error(task, pushed.get("phase", "issue"), pushed.get("error", ""))
        issue_data = pushed.get("issue") or {}
        issue_number = int(issue_data.get("number") or issue_number or 0)
        issue_url = str(issue_data.get("url") or issue.get("issue_url") or "").strip()
        issue_node_id = str(issue_data.get("id") or issue_data.get("node_id") or "").strip()

        project_payload = {}
        if settings.project_owner and settings.project_number:
            project = await self._push_project_status(
                settings,
                task,
                issue_url,
                issue_number,
                dict(github_sync),
            )
            if not project.get("ok"):
                return self._sync_error(task, project.get("phase", "project"), project.get("error", ""))
            project_payload = project

        sync = dict(existing)
        github = dict(github_sync)
        github.update({
            "issue_repo": repo,
            "issue_number": issue_number,
            "issue_node_id": issue_node_id,
            "issue_url": issue_url or f"https://github.com/{repo}/issues/{issue_number}",
        })
        for key in (
            "project_owner",
            "project_number",
            "project_id",
            "project_item_id",
            "status_field_id",
            "status_option_id",
        ):
            if project_payload.get(key):
                github[key] = project_payload[key]
        sync.update({
            "version": 1,
            "provider": self.name,
            "enabled": True,
            "github": github,
            "last_push_at": _now_iso(),
            "last_seen_provider_updated_at": str(issue_data.get("updatedAt") or ""),
            "last_synced_hash": outbound_hash,
            "sync_state": "idle",
            "last_error": "",
        })
        sync.pop("skipped", None)
        return sync

    def _sync_error(self, task, phase: str, error: str) -> dict:
        sync = dict(getattr(task, "board_sync", {}) or {})
        github = sync.get("github") if isinstance(sync.get("github"), dict) else {}
        sync.update({
            "version": 1,
            "provider": self.name,
            "enabled": bool(sync.get("enabled", True)),
            "github": github,
            "sync_state": "error",
            "last_error": str(error or "").strip(),
            "phase": phase,
        })
        return sync

    async def _ensure_labels(
        self,
        repo: str,
        labels: list[str],
        settings: GitHubSyncSettings,
    ) -> dict:
        if not labels:
            return _ok("labels", labels=[])
        listed = await self._json_gh(
            "labels",
            "label",
            "list",
            "--repo",
            repo,
            "--json",
            "name",
            "--limit",
            "1000",
        )
        if not listed.get("ok"):
            return listed
        existing = {
            _label_key(str(item.get("name") or ""))
            for item in _as_json_items(listed.get("data"))
        }
        missing = []
        seen_missing = set()
        for label in labels:
            key = _label_key(label)
            if not key or key in existing or key in seen_missing:
                continue
            missing.append(label)
            seen_missing.add(key)
        if missing and not settings.create_missing_labels:
            return _error(
                "labels",
                "GitHub labels missing: " + ", ".join(missing),
                missing_labels=missing,
                skipped=True,
            )
        for label in missing:
            created = await self._gh(
                "labels",
                "label",
                "create",
                label,
                "--repo",
                repo,
                "--color",
                "C0C0C0",
                "--description",
                "Created by Torque board sync",
            )
            if not created.get("ok"):
                if _label_already_exists_error(created.get("error", "")):
                    continue
                return created
        return _ok("labels", labels=labels, missing_created=missing)

    async def _create_issue(
        self,
        repo: str,
        task,
        labels: list[str],
        settings: GitHubSyncSettings,
    ) -> dict:
        args = [
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            str(getattr(task, "task", "") or ""),
            "--body",
            render_issue_body(task),
        ]
        if labels:
            args.extend(["--label", ",".join(labels)])
        assignees = _assignees(task, settings)
        if assignees:
            args.extend(["--assignee", ",".join(assignees)])
        created = await self._gh("issue_create", *args)
        if not created.get("ok"):
            return created
        url = (created.get("stdout") or "").splitlines()[-1].strip()
        ref = parse_github_issue_ref(external_url=url)
        if not ref["issue_number"]:
            return _error("issue_create", "Could not parse created GitHub issue URL.")
        viewed = await self._view_issue(repo, ref["issue_number"])
        if not viewed.get("ok"):
            return viewed
        return _ok("issue_create", issue=viewed.get("issue", {}))

    async def _update_issue(
        self,
        repo: str,
        issue_number: int,
        task,
        labels: list[str],
        settings: GitHubSyncSettings,
    ) -> dict:
        args = [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repo,
            "--title",
            str(getattr(task, "task", "") or ""),
            "--body",
            render_issue_body(task),
        ]
        if labels:
            args.extend(["--add-label", ",".join(labels)])
        assignees = _assignees(task, settings)
        if assignees:
            args.extend(["--add-assignee", ",".join(assignees)])
        edited = await self._gh("issue_update", *args)
        if not edited.get("ok"):
            return edited
        viewed = await self._view_issue(repo, issue_number)
        if not viewed.get("ok"):
            return viewed
        return _ok("issue_update", issue=viewed.get("issue", {}))

    async def _view_issue(self, repo: str, issue_number: int) -> dict:
        viewed = await self._json_gh(
            "issue_view",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repo,
            "--json",
            "id,number,title,body,url,labels,assignees,state,updatedAt,repository",
        )
        if not viewed.get("ok"):
            return viewed
        data = viewed.get("data") if isinstance(viewed.get("data"), dict) else {}
        if not data:
            return _error("issue_view", "GitHub issue view returned no data.")
        return _ok("issue_view", issue=data)

    async def _push_project_status(
        self,
        settings: GitHubSyncSettings,
        task,
        issue_url: str,
        issue_number: int,
        github_sync: dict,
    ) -> dict:
        project = await self._resolve_project(settings)
        if not project.get("ok"):
            return project
        status_name = _lane_status(task, settings)
        options = project.get("status_options") or {}
        option_id = str(options.get(status_name) or "").strip()
        if status_name and not option_id:
            return _error(
                "project_status_option",
                f"GitHub Project Status option '{status_name}' was not found.",
            )
        item_id = str(github_sync.get("project_item_id") or "").strip()
        if not item_id:
            added = await self._json_gh(
                "project_item_add",
                "project",
                "item-add",
                str(settings.project_number),
                "--owner",
                settings.project_owner,
                "--url",
                issue_url or f"https://github.com/{settings.repo}/issues/{issue_number}",
                "--format",
                "json",
            )
            if not added.get("ok"):
                return added
            added_data = added.get("data") if isinstance(added.get("data"), dict) else {}
            item_id = str(added_data.get("id") or added_data.get("itemId") or "").strip()
            if not item_id:
                resolved = await self._resolve_project_item(settings, issue_url, issue_number)
                if not resolved.get("ok"):
                    return resolved
                item_id = resolved.get("project_item_id", "")
        if option_id:
            edited = await self._gh(
                "project_status_update",
                "project",
                "item-edit",
                "--id",
                item_id,
                "--project-id",
                project["project_id"],
                "--field-id",
                project["status_field_id"],
                "--single-select-option-id",
                option_id,
            )
            if not edited.get("ok"):
                return edited
        return _ok(
            "project_status_update",
            project_owner=settings.project_owner,
            project_number=settings.project_number,
            project_id=project["project_id"],
            project_item_id=item_id,
            status_field_id=project["status_field_id"],
            status_option_id=option_id,
        )

    async def _resolve_project_item(
        self,
        settings: GitHubSyncSettings,
        issue_url: str,
        issue_number: int,
    ) -> dict:
        items = await self._json_gh(
            "project_item_list",
            "project",
            "item-list",
            str(settings.project_number),
            "--owner",
            settings.project_owner,
            "--format",
            "json",
        )
        if not items.get("ok"):
            return items
        for item in _as_json_items(items.get("data")):
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            try:
                content_number = int(content.get("number") or 0)
            except (TypeError, ValueError):
                content_number = 0
            if str(content.get("url") or "") == issue_url \
                    or content_number == int(issue_number or 0):
                item_id = str(item.get("id") or item.get("itemId") or "").strip()
                if item_id:
                    return _ok("project_item_list", project_item_id=item_id)
        return _error("project_item_list", "Could not resolve GitHub Project item ID.")

    async def pull_task(self, task, group_settings) -> dict:
        settings = github_settings(group_settings)
        issue = parse_github_issue_ref(
            external_id=getattr(task, "external_id", ""),
            external_url=getattr(task, "external_url", ""),
            board_sync=getattr(task, "board_sync", {}) or {},
        )
        repo = settings.repo or issue["issue_repo"]
        if not repo or not issue["issue_number"]:
            return _error(
                "pull_preview",
                "Task is not linked to a GitHub issue.",
                provider=self.name,
                task_id=str(getattr(task, "id", "") or ""),
                error_code="not_linked",
            )
        viewed = await self._view_issue(repo, issue["issue_number"])
        if not viewed.get("ok"):
            error = str(viewed.get("error") or "GitHub issue could not be loaded.")
            return _error(
                "pull_preview",
                error,
                provider=self.name,
                task_id=str(getattr(task, "id", "") or ""),
                issue_repo=repo,
                issue_number=issue["issue_number"],
                issue_url=issue["issue_url"],
                command=viewed.get("command", []),
                error_code="external_not_found"
                if _external_not_found_error(error)
                else "provider_error",
                provider_phase=viewed.get("phase", ""),
            )
        issue_data = viewed.get("issue") or {}
        changes = {}
        title = str(issue_data.get("title") or "")
        if title != str(getattr(task, "task", "") or ""):
            changes["task"] = {"local": getattr(task, "task", ""), "remote": title}
        body = strip_torque_sync_footer(str(issue_data.get("body") or ""))
        if body != strip_torque_sync_footer(str(getattr(task, "description", "") or "")):
            changes["description"] = {
                "local": getattr(task, "description", ""),
                "remote": body,
            }
        remote_labels = sorted(
            str(item.get("name") or "")
            for item in issue_data.get("labels", [])
            if isinstance(item, dict) and str(item.get("name") or "")
        )
        local_labels = sorted(_user_labels(task))
        if remote_labels != local_labels:
            changes["labels"] = {"local": local_labels, "remote": remote_labels}
        diff = _diff_items(changes)
        return _ok(
            "pull_preview",
            provider=self.name,
            task_id=str(getattr(task, "id", "") or ""),
            issue=issue_data,
            external_id=_github_external_id(repo, issue["issue_number"]),
            external_url=str(issue_data.get("url") or issue["issue_url"] or ""),
            changes=changes,
            diff=diff,
            diff_count=len(diff),
        )

    async def apply_pull(self, task, group_settings, fields: list[str]) -> dict:
        preview = await self.pull_task(task, group_settings)
        if not preview.get("ok"):
            return preview
        wanted = {str(field or "").strip() for field in fields or [] if field}
        changes = preview.get("changes") if isinstance(preview.get("changes"), dict) else {}
        apply_fields = {
            field: change.get("remote")
            for field, change in changes.items()
            if field in wanted and isinstance(change, dict)
        }
        return _ok(
            "apply_pull",
            provider=self.name,
            task_id=str(getattr(task, "id", "") or ""),
            requested_fields=sorted(wanted),
            applied_fields=sorted(apply_fields.keys()),
            fields=apply_fields,
        )

    async def list_external_items(self, group_settings) -> list[dict]:
        settings = github_settings(group_settings)
        if not settings.project_owner or not settings.project_number:
            return [
                _error(
                    "list_external_items",
                    "GitHub Project owner/number are not configured.",
                )
            ]
        items = await self._json_gh(
            "list_external_items",
            "project",
            "item-list",
            str(settings.project_number),
            "--owner",
            settings.project_owner,
            "--format",
            "json",
        )
        if not items.get("ok"):
            return [items]
        out = []
        for item in _as_json_items(items.get("data")):
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            url = str(content.get("url") or "")
            ref = parse_github_issue_ref(external_url=url)
            repo = ref["issue_repo"]
            if not repo:
                repository = content.get("repository")
                if isinstance(repository, dict):
                    repo = str(repository.get("nameWithOwner") or "").strip()
                elif repository:
                    repo = str(repository or "").strip()
            number = ref["issue_number"]
            if not number:
                try:
                    number = int(content.get("number") or 0)
                except (TypeError, ValueError):
                    number = 0
            if repo and number and not url:
                url = f"https://github.com/{repo}/issues/{number}"
            detail = {}
            if repo and number and not str(content.get("body") or item.get("body") or ""):
                try:
                    viewed = await self._view_issue(repo, number)
                except Exception:
                    viewed = {}
                if viewed.get("ok"):
                    detail = viewed.get("issue") if isinstance(viewed.get("issue"), dict) else {}
            body = str(content.get("body") or item.get("body") or detail.get("body") or "")
            marker = parse_torque_sync_marker(body)
            raw_labels = (
                content.get("labels")
                or item.get("labels")
                or detail.get("labels")
                or []
            )
            labels = sorted(
                str(label.get("name") or label)
                for label in raw_labels
                if (
                    (isinstance(label, dict) and str(label.get("name") or ""))
                    or (not isinstance(label, dict) and str(label or ""))
                )
            )
            title = str(content.get("title") or item.get("title") or detail.get("title") or "")
            url = url or str(detail.get("url") or "")
            external_id = _github_external_id(repo, number)
            out.append({
                "provider": self.name,
                "project_item_id": str(item.get("id") or item.get("itemId") or ""),
                "title": title,
                "description": strip_torque_sync_footer(body),
                "body": body,
                "labels": labels,
                "state": str(
                    content.get("state")
                    or item.get("state")
                    or detail.get("state")
                    or ""
                ),
                "status": str(
                    item.get("status")
                    or item.get(settings.status_field_name)
                    or ""
                ),
                "content_type": str(content.get("type") or item.get("type") or ""),
                "issue_repo": repo,
                "issue_number": number,
                "issue_url": url,
                "external_id": external_id,
                "external_url": url,
                "torque_marker": marker,
                "matched_task_id": marker.get("task_id", ""),
            })
        return out

    async def append_closing_refs(
        self,
        pr_body: str,
        linked_issues: list[dict],
        group_settings=None,
    ) -> str:
        settings = github_settings(group_settings)
        default_repo = ""
        for issue in linked_issues or []:
            if isinstance(issue, dict) and issue.get("base_repo"):
                default_repo = str(issue.get("base_repo") or "")
                break
        return append_closing_refs_to_body(
            pr_body,
            linked_issues,
            enabled=settings.close_issues_via_pr,
            default_repo=default_repo,
        )


__all__ = [
    "GitHubBoardSyncProvider",
    "GitHubSyncSettings",
    "append_closing_refs_to_body",
    "compute_outbound_hash",
    "default_gh_runner",
    "github_settings",
    "issue_ref_for_closing",
    "parse_github_issue_ref",
    "parse_torque_sync_marker",
    "render_issue_body",
    "strip_torque_sync_footer",
]

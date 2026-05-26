"""Helpers for linking Torque tasks to external tickets."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


class ExternalTicketError(RuntimeError):
    """Raised when an external ticket operation cannot be completed."""


@dataclass
class ImportedTicket:
    title: str
    description: str = ""
    provider: str = ""
    external_id: str = ""
    external_url: str = ""


def normalize_provider(provider: str) -> str:
    return (provider or "").strip().lower()


def parse_ref(ref: str, provider: str = "") -> tuple[str, str, str]:
    """Return (provider, external_id, external_url)."""
    ref = (ref or "").strip()
    provider = normalize_provider(provider)
    if not ref:
        return provider, "", ""

    if ref.startswith(("http://", "https://")):
        gh = re.match(
            r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)(?:[/?#].*)?$",
            ref,
            re.IGNORECASE,
        )
        if gh:
            return ("github",
                    f"{gh.group(1)}/{gh.group(2)}#{gh.group(3)}",
                    ref)
        return provider, "", ref

    if ":" in ref:
        maybe_provider, maybe_id = ref.split(":", 1)
        maybe_provider = normalize_provider(maybe_provider)
        if maybe_provider and maybe_id.strip():
            return maybe_provider, maybe_id.strip(), ""

    if re.match(r"^[^/\s]+/[^/\s]+#\d+$", ref):
        return ("github", ref, build_external_url("github", ref, ""))

    return provider, ref, ""


def build_external_url(provider: str, external_id: str,
                       external_url: str) -> str:
    if external_url:
        return external_url
    provider = normalize_provider(provider)
    external_id = (external_id or "").strip()
    if provider == "github":
        gh = re.match(r"^([^/\s]+)/([^/\s]+)#(\d+)$", external_id)
        if gh:
            return (f"https://github.com/{gh.group(1)}/{gh.group(2)}"
                    f"/issues/{gh.group(3)}")
    return ""


def _github_repo_from_url(url: str) -> str:
    gh = re.match(
        r"^https?://github\.com/([^/]+)/([^/]+)/issues/\d+(?:[/?#].*)?$",
        (url or "").strip(),
        re.IGNORECASE,
    )
    return f"{gh.group(1)}/{gh.group(2)}" if gh else ""


def _github_repo_from_external_id(external_id: str) -> str:
    gh = re.match(r"^([^/\s]+/[^/\s#]+)#\d+$", (external_id or "").strip())
    return gh.group(1) if gh else ""


def normalize_link(provider: str = "", external_id: str = "",
                   external_url: str = "", ref: str = "") -> dict:
    ref_provider, ref_id, ref_url = parse_ref(ref, provider)
    provider = normalize_provider(ref_provider or provider)
    external_id = (ref_id or external_id or "").strip()
    external_url = (ref_url or external_url or "").strip()
    external_url = build_external_url(provider, external_id, external_url)
    return {
        "provider": provider,
        "external_id": external_id,
        "external_url": external_url,
    }


def build_status_comment(task_title: str, lane: str = "",
                         status: str = "", note: str = "") -> str:
    parts = [f"Torque status update: {task_title}"]
    if status:
        parts.append(f"Status: {status}")
    elif lane:
        parts.append(f"Lane: {lane}")
    if note:
        parts.append(note.strip())
    return "\n\n".join(parts)


def build_completion_comment(task_title: str, summary: str = "") -> str:
    summary = (summary or "").strip()
    if summary:
        return f"Torque completed: {task_title}\n\nSummary: {summary}"
    return f"Torque completed: {task_title}"


class ExternalTicketAdapter:
    name = ""

    def import_ticket(self, ref: str, *, provider: str = "",
                      title: str = "", description: str = "",
                      external_id: str = "", external_url: str = "") -> ImportedTicket:
        raise ExternalTicketError(
            f"Provider '{provider or self.name or 'unknown'}' does not "
            "support import"
        )

    def open_url(self, *, provider: str = "", external_id: str = "",
                 external_url: str = "") -> str:
        url = build_external_url(provider or self.name, external_id,
                                 external_url)
        if not url:
            raise ExternalTicketError("No external URL is linked to this task")
        return url

    def push_status(self, _task, *, status: str = "", note: str = "") -> str:
        raise ExternalTicketError(
            f"Provider '{self.name or 'unknown'}' does not support status push"
        )

    def post_comment(self, _task, *, comment: str) -> str:
        raise ExternalTicketError(
            f"Provider '{self.name or 'unknown'}' does not support comments"
        )


class ManualExternalTicketAdapter(ExternalTicketAdapter):
    name = "manual"

    def import_ticket(self, ref: str, *, provider: str = "",
                      title: str = "", description: str = "",
                      external_id: str = "", external_url: str = "") -> ImportedTicket:
        link = normalize_link(
            provider or self.name,
            external_id=external_id,
            external_url=external_url,
            ref=ref,
        )
        derived_title = (title or "").strip()
        if not derived_title:
            derived_title = (link["external_id"] or link["external_url"] or ref).strip()
        if not derived_title:
            raise ExternalTicketError(
                "Manual import requires a title, reference, or URL"
            )
        return ImportedTicket(
            title=derived_title,
            description=(description or "").strip(),
            provider=link["provider"] or self.name,
            external_id=link["external_id"],
            external_url=link["external_url"],
        )


class GitHubExternalTicketAdapter(ExternalTicketAdapter):
    name = "github"

    def _issue_ref(self, provider: str, external_id: str,
                   external_url: str) -> str:
        provider = normalize_provider(provider or self.name)
        if provider != "github":
            raise ExternalTicketError(
                f"GitHub adapter cannot handle provider '{provider}'"
            )
        url = build_external_url(provider, external_id, external_url)
        return url or external_id

    def _run_gh(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["gh", *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ExternalTicketError(
                "GitHub CLI 'gh' is required for GitHub ticket sync"
            ) from exc
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or exc.stdout or "").strip()
            raise ExternalTicketError(err or "GitHub CLI command failed") from exc
        return (proc.stdout or "").strip()

    def import_ticket(self, ref: str, *, provider: str = "",
                      title: str = "", description: str = "",
                      external_id: str = "", external_url: str = "") -> ImportedTicket:
        link = normalize_link(provider or self.name, external_id, external_url,
                              ref=ref)
        if title:
            return ImportedTicket(
                title=title.strip(),
                description=(description or "").strip(),
                provider=link["provider"] or self.name,
                external_id=link["external_id"],
                external_url=link["external_url"],
            )
        issue_ref = self._issue_ref(
            link["provider"], link["external_id"], link["external_url"]
        )
        raw = self._run_gh([
            "issue", "view", issue_ref,
            "--json", "number,title,body,url",
        ])
        data = json.loads(raw or "{}")
        repo = (
            _github_repo_from_url(data.get("url") or "")
            or _github_repo_from_external_id(link["external_id"])
        )
        number = data.get("number")
        gh_id = link["external_id"]
        if repo and number:
            gh_id = f"{repo}#{number}"
        return ImportedTicket(
            title=(data.get("title") or "").strip() or issue_ref,
            description=(data.get("body") or "").strip(),
            provider=self.name,
            external_id=gh_id,
            external_url=(data.get("url") or "").strip()
            or link["external_url"],
        )

    def push_status(self, task, *, status: str = "", note: str = "") -> str:
        comment = build_status_comment(
            task.task,
            lane=task.lane,
            status=status,
            note=note,
        )
        return self.post_comment(task, comment=comment)

    def post_comment(self, task, *, comment: str) -> str:
        issue_ref = self._issue_ref(task.provider, task.external_id,
                                    task.external_url)
        self._run_gh(["issue", "comment", issue_ref, "--body", comment])
        return comment


_ADAPTERS: dict[str, ExternalTicketAdapter] = {
    "manual": ManualExternalTicketAdapter(),
    "github": GitHubExternalTicketAdapter(),
}


def register_adapter(name: str, adapter: ExternalTicketAdapter) -> None:
    _ADAPTERS[normalize_provider(name)] = adapter


def get_adapter(provider: str) -> ExternalTicketAdapter:
    provider = normalize_provider(provider)
    if not provider:
        return _ADAPTERS["manual"]
    return _ADAPTERS.get(provider, _ADAPTERS["manual"])


def import_ticket(ref: str, *, provider: str = "", title: str = "",
                  description: str = "", external_id: str = "",
                  external_url: str = "") -> ImportedTicket:
    parsed = normalize_link(provider, external_id, external_url, ref=ref)
    adapter = get_adapter(parsed["provider"] or provider)
    return adapter.import_ticket(
        ref,
        provider=parsed["provider"] or provider,
        title=title,
        description=description,
        external_id=parsed["external_id"],
        external_url=parsed["external_url"],
    )


def open_ticket_url(provider: str = "", external_id: str = "",
                    external_url: str = "") -> str:
    parsed = normalize_link(provider, external_id, external_url)
    adapter = get_adapter(parsed["provider"])
    return adapter.open_url(
        provider=parsed["provider"],
        external_id=parsed["external_id"],
        external_url=parsed["external_url"],
    )


def push_ticket_status(task, *, status: str = "", note: str = "") -> str:
    provider = normalize_provider(getattr(task, "provider", ""))
    if not provider:
        raise ExternalTicketError("Task is not linked to an external provider")
    adapter = get_adapter(provider)
    return adapter.push_status(task, status=status, note=note)


def post_ticket_comment(task, *, comment: str) -> str:
    provider = normalize_provider(getattr(task, "provider", ""))
    if not provider:
        raise ExternalTicketError("Task is not linked to an external provider")
    adapter = get_adapter(provider)
    return adapter.post_comment(task, comment=comment)

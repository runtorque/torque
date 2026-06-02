"""Best-effort local embedding index pipeline for Torque AI calls.

This module is intentionally import-light.  Optional sqlite-vec loading happens
only inside ``_load_sqlite_vec`` on fresh SQLite connections, and local ML work
is delegated to ``LocalEmbeddingService`` which performs inference off the event
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Literal

from torque.ai_embeddings import (
    EmbeddingDimsResult,
    EmbeddingFailure,
    EmbeddingResult,
    LocalEmbeddingService,
)
from torque.config import DATA_DIR
from torque.db import TorqueDB
from torque.state import AI_DEFAULT_EMBEDDING_MODEL, default_ai_index_corpus

log = logging.getLogger("torque")

AI_INDEX_SOURCE_SCHEMA_VERSION = "ai-index-source-v1"
AI_INDEX_CHUNK_SCHEMA_VERSION = "ai-index-chunk-v1"
DEFAULT_CHUNK_TARGET_CHARS = 3600
DEFAULT_CHUNK_OVERLAP_CHARS = 240
DEFAULT_DEBOUNCE_SECONDS = 1.5

IndexMode = Literal["incremental", "rebuild"]
SQLiteVecLoader = Callable[[sqlite3.Connection], None]
BroadcastCallback = Callable[[], Awaitable[None]]


class AIIndexDependencyMissing(RuntimeError):
    """sqlite-vec is unavailable for the requested index operation."""


@dataclass(frozen=True)
class HarvestedSource:
    source_key: str
    source_type: str
    source_id: str
    text: str
    content_hash: str
    source_sub_id: str = ""
    group_name: str = ""
    owner_kind: str = ""
    owner_id: str = ""
    participant_ids: list[str] = field(default_factory=list)
    participant_kinds: dict[str, str] = field(default_factory=dict)
    visibility_json: dict = field(default_factory=dict)
    title: str = ""
    source_updated_at: str = ""

    def source_row(self) -> dict:
        return {
            "source_key": self.source_key,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_sub_id": self.source_sub_id,
            "group_name": self.group_name,
            "owner_kind": self.owner_kind,
            "owner_id": self.owner_id,
            "participant_ids": list(self.participant_ids),
            "participant_kinds": dict(self.participant_kinds),
            "visibility_json": dict(self.visibility_json),
            "title": self.title,
            "source_updated_at": self.source_updated_at,
            "content_hash": self.content_hash,
        }


def stable_content_hash(source_type: str, source_id: str, fields: dict) -> str:
    payload = {
        "schema_version": AI_INDEX_SOURCE_SCHEMA_VERSION,
        "source_type": str(source_type or ""),
        "source_id": str(source_id or ""),
        "fields": fields,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_chunk_hash(text: str) -> str:
    return hashlib.sha256(
        (AI_INDEX_CHUNK_SCHEMA_VERSION + "\0" + str(text or "")).encode("utf-8")
    ).hexdigest()


def normalize_participants(
    ids: Iterable[str],
    *,
    agents: dict[str, dict] | None = None,
) -> tuple[list[str], dict[str, str]]:
    seen: set[str] = set()
    out: list[str] = []
    kinds: dict[str, str] = {}
    agents = agents or {}
    for raw in ids:
        aid = str(raw or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        out.append(aid)
        kind = str((agents.get(aid) or {}).get("kind", "") or "").strip()
        if kind:
            kinds[aid] = kind
    return out, kinds


def chunk_text(
    text: str,
    *,
    target_chars: int = DEFAULT_CHUNK_TARGET_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Split source text deterministically while preserving exact chunk text."""

    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    target = max(256, int(target_chars or DEFAULT_CHUNK_TARGET_CHARS))
    overlap = max(0, min(int(overlap_chars or 0), target // 2))
    if len(normalized) <= target:
        return [normalized]

    paragraphs = re.split(r"(\n{2,})", normalized)
    units: list[str] = []
    i = 0
    while i < len(paragraphs):
        unit = paragraphs[i]
        if i + 1 < len(paragraphs) and re.fullmatch(r"\n{2,}", paragraphs[i + 1] or ""):
            unit += paragraphs[i + 1]
            i += 2
        else:
            i += 1
        if unit:
            units.append(unit)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > target:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(unit):
                end = min(len(unit), start + target)
                chunks.append(unit[start:end].strip())
                if end >= len(unit):
                    break
                start = max(end - overlap, start + 1)
            continue
        if current and len(current) + len(unit) > target:
            chunks.append(current.strip())
            carry = current[-overlap:] if overlap else ""
            current = (carry + unit) if carry else unit
        else:
            current += unit
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def _json_loads(value, default):
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, TypeError):
        decoded = default
    if isinstance(default, dict):
        return decoded if isinstance(decoded, dict) else dict(default)
    if isinstance(default, list):
        return decoded if isinstance(decoded, list) else list(default)
    return decoded if decoded is not None else default


def _isoish(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(float(value))
    return str(value or "")


def _agent_rows(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT id, name, group_name, kind, owner_engineer_id, "
        "hired_by_architect_id FROM agents"
    ).fetchall()
    agents: dict[str, dict] = {}
    for row in rows:
        agents[str(row[0] or "")] = {
            "id": str(row[0] or ""),
            "name": str(row[1] or ""),
            "group_name": str(row[2] or ""),
            "kind": str(row[3] or ""),
            "owner_engineer_id": str(row[4] or ""),
            "hired_by_architect_id": str(row[5] or ""),
        }
    return agents


def _source_text(header: dict, sections: list[tuple[str, object]]) -> str:
    lines: list[str] = []
    for key, value in header.items():
        text = str(value or "").strip()
        if text:
            lines.append(f"{key}: {text}")
    for title, value in sections:
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        text = str(text or "").strip()
        if not text:
            continue
        if lines:
            lines.append("")
        lines.append(f"{title}:")
        lines.append(text)
    return "\n".join(lines).strip()


def harvest_corpus(
    db_path: Path | str,
    data_dir: Path | str = DATA_DIR,
    *,
    corpus_config: dict | None = None,
) -> list[HarvestedSource]:
    """Harvest all enabled AI index sources using a fresh SQLite connection."""

    corpus = dict(default_ai_index_corpus())
    if isinstance(corpus_config, dict):
        for key in corpus:
            if key in corpus_config:
                corpus[key] = bool(corpus_config[key])
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        agents = _agent_rows(conn)
        sources: list[HarvestedSource] = []
        if corpus.get("architect_journals", True):
            sources.extend(_harvest_architect_journals(Path(data_dir), agents))
        if corpus.get("engineer_journals", True):
            sources.extend(_harvest_engineer_journals(conn, agents))
        if corpus.get("decisions", True):
            sources.extend(_harvest_decisions(conn, agents))
        if corpus.get("tasks", True):
            sources.extend(_harvest_tasks(conn, agents))
        if corpus.get("engineer_peer_threads", True):
            sources.extend(_harvest_engineer_peer_threads(conn, agents))
        return sorted(sources, key=lambda item: item.source_key)
    finally:
        conn.close()


def _harvest_architect_journals(data_dir: Path, agents: dict[str, dict]) -> list[HarvestedSource]:
    out: list[HarvestedSource] = []
    journal_dir = data_dir / "architect_journals"
    if not journal_dir.exists():
        return out
    for path in sorted(journal_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            raw = line.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(entry, dict):
                continue
            architect_id = str(entry.get("architect_id", "") or "").strip()
            entry_id = str(entry.get("id", "") or "").strip()
            if not architect_id or not entry_id:
                continue
            agent = agents.get(architect_id, {})
            participants, participant_kinds = normalize_participants([architect_id], agents=agents)
            fields = {
                "architect_id": architect_id,
                "id": entry_id,
                "timestamp": entry.get("timestamp", 0),
                "type": str(entry.get("type", "") or ""),
                "entry": str(entry.get("entry", "") or ""),
            }
            text = _source_text(
                {
                    "Source": f"architect_journal {architect_id}/{entry_id}",
                    "Architect": architect_id,
                    "Type": fields["type"],
                    "Timestamp": _isoish(fields["timestamp"]),
                },
                [("Entry", fields["entry"])],
            )
            out.append(HarvestedSource(
                source_key=f"architect_journal:{architect_id}:{entry_id}",
                source_type="architect_journal",
                source_id=architect_id,
                source_sub_id=entry_id,
                group_name=str(agent.get("group_name", "") or ""),
                owner_kind="architect",
                owner_id=architect_id,
                participant_ids=participants,
                participant_kinds=participant_kinds,
                visibility_json={"architect_id": architect_id, "participants": participants},
                title=f"Architect journal {fields['type'] or 'entry'}",
                source_updated_at=_isoish(fields["timestamp"]),
                text=text,
                content_hash=stable_content_hash("architect_journal", architect_id, fields),
            ))
    return out


def _harvest_engineer_journals(conn: sqlite3.Connection, agents: dict[str, dict]) -> list[HarvestedSource]:
    rows = conn.execute(
        "SELECT id, group_name, timestamp, entry_type, entry, author_cell_id "
        "FROM engineer_journal ORDER BY id ASC"
    ).fetchall()
    out: list[HarvestedSource] = []
    for row in rows:
        entry_id = str(row[0])
        group = str(row[1] or "")
        author_id = str(row[5] or "")
        participants, participant_kinds = normalize_participants([author_id], agents=agents)
        fields = {
            "id": int(row[0] or 0),
            "group_name": group,
            "timestamp": row[2] or 0,
            "entry_type": str(row[3] or ""),
            "entry": str(row[4] or ""),
            "author_cell_id": author_id,
        }
        text = _source_text(
            {
                "Source": f"engineer_journal {entry_id}",
                "Group": group,
                "Author": author_id,
                "Type": fields["entry_type"],
                "Timestamp": _isoish(fields["timestamp"]),
            },
            [("Entry", fields["entry"])],
        )
        out.append(HarvestedSource(
            source_key=f"engineer_journal:{entry_id}",
            source_type="engineer_journal",
            source_id=entry_id,
            group_name=group,
            owner_kind="engineer" if author_id else "group",
            owner_id=author_id or group,
            participant_ids=participants,
            participant_kinds=participant_kinds,
            visibility_json={"author_engineer_id": author_id, "participants": participants},
            title=f"Engineer journal {fields['entry_type'] or 'entry'}",
            source_updated_at=_isoish(fields["timestamp"]),
            text=text,
            content_hash=stable_content_hash("engineer_journal", entry_id, fields),
        ))
    return out


def _harvest_decisions(conn: sqlite3.Connection, agents: dict[str, dict]) -> list[HarvestedSource]:
    rows = conn.execute(
        "SELECT id, architect_id, title, rationale, status, supersedes, "
        "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at "
        "FROM decisions ORDER BY id ASC"
    ).fetchall()
    out: list[HarvestedSource] = []
    for row in rows:
        decision_id = str(row[0] or "")
        architect_id = str(row[1] or "")
        linked_tasks = _json_loads(row[6], [])
        linked_engineers = _json_loads(row[7], [])
        participants, participant_kinds = normalize_participants([architect_id, *linked_engineers], agents=agents)
        agent = agents.get(architect_id, {})
        fields = {
            "id": decision_id,
            "architect_id": architect_id,
            "title": str(row[2] or ""),
            "rationale": str(row[3] or ""),
            "status": str(row[4] or ""),
            "supersedes": str(row[5] or ""),
            "linked_task_ids": linked_tasks,
            "linked_engineer_ids": linked_engineers,
            "archived": bool(row[8]),
            "created_at": int(row[9] or 0),
            "updated_at": int(row[10] or 0),
        }
        text = _source_text(
            {
                "Source": f"decision {decision_id}",
                "Status": fields["status"],
                "Title": fields["title"],
                "Architect": architect_id,
                "Archived": str(fields["archived"]).lower(),
                "Updated": _isoish(fields["updated_at"]),
            },
            [
                ("Linked tasks", ", ".join(linked_tasks)),
                ("Linked engineers", ", ".join(linked_engineers)),
                ("Supersedes", fields["supersedes"]),
                ("Rationale", fields["rationale"]),
            ],
        )
        out.append(HarvestedSource(
            source_key=f"decision:{decision_id}",
            source_type="decision",
            source_id=decision_id,
            group_name=str(agent.get("group_name", "") or ""),
            owner_kind="architect",
            owner_id=architect_id,
            participant_ids=participants,
            participant_kinds=participant_kinds,
            visibility_json={
                "architect_id": architect_id,
                "linked_task_ids": linked_tasks,
                "linked_engineer_ids": linked_engineers,
                "participants": participants,
            },
            title=fields["title"],
            source_updated_at=_isoish(fields["updated_at"]),
            text=text,
            content_hash=stable_content_hash("decision", decision_id, fields),
        ))
    return out


def _harvest_tasks(conn: sqlite3.Connection, agents: dict[str, dict]) -> list[HarvestedSource]:
    cursor = conn.execute("SELECT * FROM board_tasks ORDER BY id ASC")
    cols = [d[0] for d in cursor.description]
    out: list[HarvestedSource] = []
    for row in cursor.fetchall():
        task = dict(zip(cols, row))
        task_id = str(task.get("id", "") or "")
        group = str(task.get("group_name", "") or "")
        assigned_engineer_id = str(task.get("assigned_engineer_id", "") or "")
        created_by_architect_id = str(task.get("created_by_architect_id", "") or "")
        created_by_engineer_id = str(task.get("created_by_engineer_id", "") or "")
        agent_id = str(task.get("agent_id", "") or "")
        reply_agent_id = str(task.get("reply_agent_id", "") or "")
        participants, participant_kinds = normalize_participants(
            [assigned_engineer_id, created_by_architect_id, created_by_engineer_id, agent_id, reply_agent_id],
            agents=agents,
        )
        owner_kind = ""
        owner_id = ""
        if created_by_architect_id:
            owner_kind = "architect"
            owner_id = created_by_architect_id
        elif assigned_engineer_id or created_by_engineer_id:
            owner_kind = "engineer"
            owner_id = assigned_engineer_id or created_by_engineer_id
        fields = {
            "id": task_id,
            "task": str(task.get("task", "") or ""),
            "description": str(task.get("description", "") or ""),
            "group_name": group,
            "lane": str(task.get("lane", "") or ""),
            "status": str(task.get("status", "") or ""),
            "labels": _json_loads(task.get("labels", "[]"), []),
            "action_name": str(task.get("action_name", "") or ""),
            "agent_id": agent_id,
            "assigned_engineer_id": assigned_engineer_id,
            "created_by_architect_id": created_by_architect_id,
            "created_by_engineer_id": created_by_engineer_id,
            "parent_task_id": str(task.get("parent_task_id", "") or ""),
            "pipeline_root_id": str(task.get("pipeline_root_id", "") or ""),
            "updated_at": str(task.get("updated_at", "") or ""),
            "archived_at": str(task.get("archived_at", "") or ""),
            "instructions": str(task.get("instructions", "") or ""),
            "context": str(task.get("context", "") or ""),
            "criteria": str(task.get("criteria", "") or ""),
            "messages_thread": _json_loads(task.get("messages_thread", "[]"), []),
        }
        text = _source_text(
            {
                "Source": f"task {task_id}",
                "Group": group,
                "Lane": fields["lane"],
                "Status": fields["status"],
                "Title": fields["task"],
                "Updated": fields["updated_at"],
            },
            [
                ("Description", fields["description"]),
                ("Instructions", fields["instructions"]),
                ("Context", fields["context"]),
                ("Criteria", fields["criteria"]),
                ("Labels", ", ".join(fields["labels"])),
                ("Messages", fields["messages_thread"]),
            ],
        )
        out.append(HarvestedSource(
            source_key=f"task:{task_id}",
            source_type="task",
            source_id=task_id,
            group_name=group,
            owner_kind=owner_kind,
            owner_id=owner_id,
            participant_ids=participants,
            participant_kinds=participant_kinds,
            visibility_json={
                "assigned_engineer_id": assigned_engineer_id,
                "created_by_architect_id": created_by_architect_id,
                "created_by_engineer_id": created_by_engineer_id,
                "agent_id": agent_id,
                "reply_agent_id": reply_agent_id,
                "participants": participants,
            },
            title=fields["task"],
            source_updated_at=fields["updated_at"],
            text=text,
            content_hash=stable_content_hash("task", task_id, fields),
        ))
    return out


def _harvest_engineer_peer_threads(conn: sqlite3.Connection, agents: dict[str, dict]) -> list[HarvestedSource]:
    cursor = conn.execute(
        "SELECT id, thread_id, group_name, sender_id, sender_kind, sender_name, "
        "recipient_id, recipient_kind, recipient_name, message, message_type, "
        "created_at, source_task_id, context_task_ids, context_engineer_ids, "
        "context_decision_ids, context_summary, context_snapshot "
        "FROM agent_peer_messages WHERE sender_kind='engineer' "
        "AND recipient_kind='engineer' AND message_type='message' "
        "AND blocking=0 AND archived_at=0 "
        "ORDER BY thread_id ASC, created_at ASC, id ASC"
    )
    threads: dict[str, list[dict]] = {}
    for row in cursor.fetchall():
        item = {
            "id": str(row[0] or ""),
            "thread_id": str(row[1] or ""),
            "group_name": str(row[2] or ""),
            "sender_id": str(row[3] or ""),
            "sender_kind": str(row[4] or ""),
            "sender_name": str(row[5] or ""),
            "recipient_id": str(row[6] or ""),
            "recipient_kind": str(row[7] or ""),
            "recipient_name": str(row[8] or ""),
            "message": str(row[9] or ""),
            "message_type": str(row[10] or ""),
            "created_at": float(row[11] or 0),
            "source_task_id": str(row[12] or ""),
            "context_task_ids": _json_loads(row[13], []),
            "context_engineer_ids": _json_loads(row[14], []),
            "context_decision_ids": _json_loads(row[15], []),
            "context_summary": str(row[16] or ""),
            "context_snapshot": _json_loads(row[17], {}),
        }
        threads.setdefault(item["thread_id"], []).append(item)
    out: list[HarvestedSource] = []
    for thread_id, messages in sorted(threads.items()):
        if not thread_id or not messages:
            continue
        participant_seed: list[str] = []
        for msg in messages:
            participant_seed.extend([msg["sender_id"], msg["recipient_id"]])
            participant_seed.extend(msg.get("context_engineer_ids", []) or [])
        participants, participant_kinds = normalize_participants(participant_seed, agents=agents)
        groups = [msg["group_name"] for msg in messages if msg.get("group_name")]
        group = groups[0] if groups else ""
        hired_architect_ids = sorted({
            str((agents.get(pid) or {}).get("hired_by_architect_id", "") or "")
            for pid in participants
            if str((agents.get(pid) or {}).get("hired_by_architect_id", "") or "")
        })
        all_context_task_ids: list[str] = []
        all_context_decision_ids: list[str] = []
        for msg in messages:
            for tid in msg.get("context_task_ids", []) or []:
                if tid not in all_context_task_ids:
                    all_context_task_ids.append(tid)
            for did in msg.get("context_decision_ids", []) or []:
                if did not in all_context_decision_ids:
                    all_context_decision_ids.append(did)
        fields = {
            "thread_id": thread_id,
            "participants": participants,
            "participant_kinds": participant_kinds,
            "participant_hired_by_architect_ids": hired_architect_ids,
            "messages": messages,
            "context_task_ids": all_context_task_ids,
            "context_decision_ids": all_context_decision_ids,
        }
        message_lines = []
        for msg in messages:
            sender = msg.get("sender_name") or msg.get("sender_id")
            recipient = msg.get("recipient_name") or msg.get("recipient_id")
            message_lines.append(
                f"[{_isoish(msg.get('created_at'))}] {sender} → {recipient}: {msg.get('message', '')}"
            )
            if msg.get("context_summary"):
                message_lines.append(f"Context summary: {msg['context_summary']}")
        text = _source_text(
            {
                "Source": f"engineer_peer_thread {thread_id}",
                "Group": group,
                "Participants": ", ".join(participants),
                "Messages": str(len(messages)),
            },
            [
                ("Context tasks", ", ".join(all_context_task_ids)),
                ("Context decisions", ", ".join(all_context_decision_ids)),
                ("Thread", "\n".join(message_lines)),
            ],
        )
        out.append(HarvestedSource(
            source_key=f"engineer_peer_thread:{thread_id}",
            source_type="engineer_peer_thread",
            source_id=thread_id,
            source_sub_id=thread_id,
            group_name=group,
            owner_kind="engineer_peer_thread",
            owner_id=thread_id,
            participant_ids=participants,
            participant_kinds=participant_kinds,
            visibility_json={
                "participants": participants,
                "participant_hired_by_architect_ids": hired_architect_ids,
                "context_task_ids": all_context_task_ids,
                "context_decision_ids": all_context_decision_ids,
            },
            title=f"Engineer peer thread {thread_id}",
            source_updated_at=_isoish(max(msg.get("created_at", 0) for msg in messages)),
            text=text,
            content_hash=stable_content_hash("engineer_peer_thread", thread_id, fields),
        ))
    return out


class AIIndexService:
    """Debounced background sqlite-vec index builder."""

    def __init__(
        self,
        *,
        db: TorqueDB,
        state=None,
        embedding_service: LocalEmbeddingService | None = None,
        data_dir: Path | str = DATA_DIR,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        chunk_target_chars: int = DEFAULT_CHUNK_TARGET_CHARS,
        chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
        sqlite_vec_loader: SQLiteVecLoader | None = None,
        broadcast_callback: BroadcastCallback | None = None,
    ) -> None:
        self.db = db
        self.state = state
        self.data_dir = Path(data_dir)
        self.embedding_service = embedding_service or LocalEmbeddingService(data_dir=self.data_dir)
        self.debounce_seconds = max(0.05, float(debounce_seconds))
        self.chunk_target_chars = int(chunk_target_chars)
        self.chunk_overlap_chars = int(chunk_overlap_chars)
        self._sqlite_vec_loader = sqlite_vec_loader
        self._broadcast_callback = broadcast_callback
        self._debounce_task: asyncio.Task | None = None
        self._job_task: asyncio.Task | None = None
        self._closed = False

    def schedule_incremental(self, reason: str = "source_mutation") -> None:
        """Cheap trailing-edge signal for source mutations."""

        if self._closed or not self._ai_enabled():
            return
        with contextlib.suppress(Exception):
            loop = asyncio.get_running_loop()
            if self._debounce_task and not self._debounce_task.done():
                self._debounce_task.cancel()
            self._debounce_task = loop.create_task(
                self._debounced_incremental(str(reason or "source_mutation"))
            )

    def schedule_rebuild(self, *, job_id: str, reason: str = "manual") -> None:
        if self._closed:
            return
        with contextlib.suppress(Exception):
            loop = asyncio.get_running_loop()
            self._schedule_job_task(loop, job_id, "rebuild", str(reason or "manual"), True)

    async def start(
        self,
        *,
        mode: IndexMode = "incremental",
        confirm: bool = False,
        reason: str = "manual",
    ) -> dict:
        mode = str(mode or "incremental").strip()
        if mode not in {"incremental", "rebuild"}:
            return {"type": "error", "message": "mode must be incremental or rebuild"}
        if mode == "rebuild" and not confirm:
            counts = await asyncio.to_thread(
                self._db_call_sync,
                "ai_get_index_counts",
            )
            return {
                "type": "ai_index_requires_confirmation",
                "reason": "manual_rebuild",
                "message": "Rebuilding the AI vector index re-embeds all indexed entries. Continue?",
                "estimated_entries": int(counts.get("chunks", 0) or 0),
            }
        if self._job_task and not self._job_task.done():
            job = await asyncio.to_thread(
                self._db_call_sync,
                "ai_get_current_index_job",
            )
            return {"type": "ai_index_job", "job": job}
        job = await asyncio.to_thread(
            self._db_call_sync,
            "ai_create_index_job",
            _write=True,
            mode=mode,
            reason=reason,
        )
        self._schedule_job_task(asyncio.get_running_loop(), job["id"], mode, reason, confirm)
        await self._publish_status(job=job)
        return {"type": "ai_index_job", "job": job}

    async def shutdown(self) -> None:
        self._closed = True
        for task in (self._debounce_task, self._job_task):
            if task and not task.done():
                task.cancel()
        tasks = [task for task in (self._debounce_task, self._job_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self.embedding_service.shutdown()

    def _ai_enabled(self) -> bool:
        gs = getattr(self.state, "global_settings", None)
        if gs is None:
            return True
        return bool(getattr(gs, "ai_enabled", False))

    def _desired_model_id(self) -> str:
        gs = getattr(self.state, "global_settings", None)
        if gs is None:
            return AI_DEFAULT_EMBEDDING_MODEL
        return str(getattr(gs, "ai_embedding_model", "") or "").strip() or AI_DEFAULT_EMBEDDING_MODEL

    def _corpus_config(self) -> dict:
        corpus = dict(default_ai_index_corpus())
        gs = getattr(self.state, "global_settings", None)
        persisted = getattr(gs, "ai_index_corpus", {}) if gs is not None else {}
        if isinstance(persisted, dict):
            for key in corpus:
                if key in persisted:
                    corpus[key] = bool(persisted[key])
        return corpus

    async def _debounced_incremental(self, reason: str) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            await self.start(mode="incremental", confirm=False, reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("AI index debounced incremental failed")

    def _schedule_job_task(
        self,
        loop: asyncio.AbstractEventLoop,
        job_id: str,
        mode: str,
        reason: str,
        confirm: bool,
    ) -> None:
        if self._job_task and not self._job_task.done():
            return
        self._job_task = loop.create_task(
            self._run_job(job_id=job_id, mode=mode, reason=reason, confirm=confirm)
        )

    def _db_call_sync(self, method_name: str, *args, _write: bool = False, **kwargs):
        conn = self.db.open_ai_index_connection()
        try:
            result = getattr(self.db, method_name)(*args, conn=conn, **kwargs)
            if _write:
                conn.commit()
            return result
        finally:
            conn.close()

    def _status_payload_sync(self) -> dict:
        conn = self.db.open_ai_index_connection()
        try:
            state = self.db.ai_get_index_state(conn)
            counts = self.db.ai_get_index_counts(conn)
            job = self.db.ai_get_current_index_job(conn=conn)
            indexed_rows = int(counts.get("chunks", 0) or 0)
            desired = str(state.get("desired_model_id", "") or "")
            active = str(state.get("active_model_id", "") or "")
            rebuild_required = bool(state.get("rebuild_required"))
            return {
                "state": state,
                "counts": counts,
                "current_job": job,
                "rebuild_warning": {
                    "required": bool(
                        rebuild_required
                        or (indexed_rows > 0 and desired and active and desired != active)
                    ),
                    "reason": (
                        str(state.get("rebuild_reason", "") or "")
                        or ("embedding_model_change" if desired and active and desired != active else "")
                    ),
                    "estimated_entries": indexed_rows,
                },
            }
        finally:
            conn.close()

    async def _run_job(self, *, job_id: str, mode: str, reason: str, confirm: bool) -> None:
        totals = {"sources": 0, "chunks": 0, "indexed": 0, "errors": 0, "deleted": 0}
        desired_model_id = self._desired_model_id()
        corpus_config = self._corpus_config()
        try:
            await asyncio.to_thread(
                self._db_call_sync,
                "ai_update_index_job",
                job_id,
                _write=True,
                status="running",
                totals=totals,
            )
            await asyncio.to_thread(
                self._db_call_sync,
                "ai_update_index_state",
                _write=True,
                desired_model_id=desired_model_id,
                corpus_config=corpus_config,
                status="building",
                last_error="",
            )
            await self._publish_status(job_id=job_id)

            if not self._ai_enabled():
                await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_update_index_state",
                    _write=True,
                    status="disabled",
                    desired_model_id=desired_model_id,
                )
                await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_update_index_job",
                    job_id,
                    _write=True,
                    status="complete",
                    totals=totals,
                )
                await self._publish_status(job_id=job_id)
                return

            sources = await asyncio.to_thread(
                harvest_corpus,
                self.db.db_path,
                self.data_dir,
                corpus_config=corpus_config,
            )
            source_by_key = {source.source_key: source for source in sources}
            totals["sources"] = len(sources)

            await asyncio.to_thread(
                self._apply_scan_sync,
                sources,
                desired_model_id,
                corpus_config,
                totals,
            )
            await self._publish_status(job_id=job_id)

            dims_response = await self.embedding_service.probe_dims(desired_model_id)
            if isinstance(dims_response, EmbeddingFailure):
                await self._fail_job(
                    job_id,
                    totals,
                    status=("dependency_missing" if dims_response.kind == "dependency_missing" else "error"),
                    error=dims_response.message,
                )
                return
            if not isinstance(dims_response, EmbeddingDimsResult) or dims_response.dims <= 0:
                await self._fail_job(job_id, totals, status="error", error="Embedding dimensions could not be probed.")
                return
            dims = int(dims_response.dims)

            state_payload = await asyncio.to_thread(
                self._db_call_sync,
                "ai_get_index_state",
            )
            counts = await asyncio.to_thread(
                self._db_call_sync,
                "ai_get_index_counts",
            )
            active_model = str(state_payload.get("active_model_id", "") or "")
            active_dims = int(state_payload.get("active_dims", 0) or 0)
            indexed_rows = int(counts.get("chunks", 0) or 0)
            mismatch = bool(indexed_rows and (active_model != desired_model_id or active_dims != dims))
            rebuild_required = bool(state_payload.get("rebuild_required"))
            full_rebuild = mode == "rebuild" or rebuild_required
            if mismatch and not full_rebuild:
                await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_update_index_state",
                    _write=True,
                    desired_model_id=desired_model_id,
                    status="rebuild_pending",
                    rebuild_required=True,
                    rebuild_reason="embedding_model_change",
                )
                await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_update_index_job",
                    job_id,
                    _write=True,
                    status="complete",
                    totals=totals,
                )
                await self._publish_status(job_id=job_id)
                return

            await asyncio.to_thread(
                self._prepare_vector_table_sync,
                desired_model_id,
                dims,
                full_rebuild,
            )
            await self._publish_status(job_id=job_id)

            if full_rebuild:
                pending_sources = list(source_by_key.values())
            else:
                pending_rows = await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_list_embedding_sources",
                    ["pending", "stale", "error"],
                )
                pending_sources = [source_by_key[row["source_key"]] for row in pending_rows if row.get("source_key") in source_by_key]

            for source in pending_sources:
                await self._index_one_source(job_id, source, desired_model_id, dims, totals)
                await self._publish_status(job_id=job_id)
                await asyncio.sleep(0)

            await asyncio.to_thread(
                self._db_call_sync,
                "ai_update_index_state",
                _write=True,
                desired_model_id=desired_model_id,
                active_model_id=desired_model_id,
                active_dims=dims,
                status="ready",
                rebuild_required=False,
                rebuild_reason="",
                last_built_at=time.time(),
                last_error="",
            )
            await asyncio.to_thread(
                self._db_call_sync,
                "ai_update_index_job",
                job_id,
                _write=True,
                status="complete",
                totals=totals,
            )
            await self._publish_status(job_id=job_id)
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self._db_call_sync,
                "ai_update_index_job",
                job_id,
                _write=True,
                status="cancelled",
                totals=totals,
                error="cancelled",
            )
            await self._publish_status(job_id=job_id)
            raise
        except AIIndexDependencyMissing as exc:
            await self._fail_job(job_id, totals, status="dependency_missing", error=str(exc))
        except Exception as exc:
            log.exception("AI index job failed")
            await self._fail_job(job_id, totals, status="error", error=str(exc) or "AI index job failed")

    async def _index_one_source(
        self,
        job_id: str,
        source: HarvestedSource,
        model_id: str,
        dims: int,
        totals: dict,
    ) -> None:
        texts = chunk_text(
            source.text,
            target_chars=self.chunk_target_chars,
            overlap_chars=self.chunk_overlap_chars,
        )
        if not texts:
            texts = [source.text]
        vectors: list[list[float]] = []
        batch_size = max(1, int(getattr(self.embedding_service, "max_batch_size", 32) or 32))
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            response = await self.embedding_service.embed_texts(model_id, batch)
            if isinstance(response, EmbeddingFailure):
                totals["errors"] = int(totals.get("errors", 0) or 0) + 1
                await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_mark_source_error",
                    source.source_key,
                    response.message,
                    _write=True,
                )
                return
            if not isinstance(response, EmbeddingResult):
                totals["errors"] = int(totals.get("errors", 0) or 0) + 1
                await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_mark_source_error",
                    source.source_key,
                    "Embedding worker returned an invalid response.",
                    _write=True,
                )
                return
            if int(response.dims or 0) != int(dims):
                totals["errors"] = int(totals.get("errors", 0) or 0) + 1
                await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_mark_source_error",
                    source.source_key,
                    "Embedding dimensions changed during indexing.",
                    _write=True,
                )
                return
            vectors.extend(response.vectors)
            await asyncio.sleep(0)
        chunks = [
            {
                "chunk_index": index,
                "text": text,
                "chunk_hash": stable_chunk_hash(text),
                "vector": vectors[index],
            }
            for index, text in enumerate(texts)
        ]
        await asyncio.to_thread(
            self._write_source_chunks_sync,
            source,
            chunks,
            model_id,
            dims,
        )
        totals["chunks"] = int(totals.get("chunks", 0) or 0) + len(chunks)
        totals["indexed"] = int(totals.get("indexed", 0) or 0) + 1
        await asyncio.to_thread(
            self._db_call_sync,
            "ai_update_index_job",
            job_id,
            _write=True,
            totals=totals,
        )

    def _apply_scan_sync(
        self,
        sources: list[HarvestedSource],
        desired_model_id: str,
        corpus_config: dict,
        totals: dict,
    ) -> None:
        conn = self._open_vector_conn(load_if_table_exists=True)
        try:
            now = time.time()
            with conn:
                self.db.ai_update_index_state(
                    conn,
                    commit=False,
                    desired_model_id=desired_model_id,
                    corpus_config=corpus_config,
                    last_scan_at=now,
                )
                seen: set[str] = set()
                for source in sources:
                    self.db.ai_upsert_embedding_source(
                        source.source_row(),
                        conn=conn,
                        now=now,
                        commit=False,
                    )
                    seen.add(source.source_key)
                deleted = self.db.ai_mark_sources_deleted_not_seen(
                    seen,
                    conn=conn,
                    now=now,
                    commit=False,
                )
                totals["deleted"] = len(deleted)
        finally:
            conn.close()

    def _prepare_vector_table_sync(self, model_id: str, dims: int, full_rebuild: bool) -> None:
        conn = self._open_vector_conn(load_if_table_exists=False)
        try:
            with conn:
                state = self.db.ai_get_index_state(conn)
                active_dims = int(state.get("active_dims", 0) or 0)
                active_model = str(state.get("active_model_id", "") or "")
                table_exists = self.db.ai_index_vector_table_exists(conn)
                recreate = bool(full_rebuild or not table_exists or active_dims != dims or (active_model and active_model != model_id))
                if recreate:
                    self.db.ai_drop_embedding_vec_table(conn)
                    self.db.ai_create_embedding_vec_table(conn, dims, recreate=False)
                    self.db.ai_clear_all_chunks_and_vectors(conn=conn, commit=False)
                else:
                    self.db.ai_create_embedding_vec_table(conn, dims, recreate=False)
        finally:
            conn.close()

    def _write_source_chunks_sync(
        self,
        source: HarvestedSource,
        chunks: list[dict],
        model_id: str,
        dims: int,
    ) -> None:
        conn = self._open_vector_conn(load_if_table_exists=False)
        try:
            with conn:
                self.db.ai_replace_source_chunks(
                    source.source_key,
                    chunks,
                    conn=conn,
                    model_id=model_id,
                    dims=dims,
                    content_hash=source.content_hash,
                    indexed_at=time.time(),
                )
        finally:
            conn.close()

    def _open_vector_conn(self, *, load_if_table_exists: bool) -> sqlite3.Connection:
        conn = self.db.open_ai_index_connection()
        try:
            if load_if_table_exists:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='ai_embedding_vec' LIMIT 1"
                ).fetchone()
                if row:
                    self._load_sqlite_vec(conn)
            else:
                self._load_sqlite_vec(conn)
            return conn
        except Exception:
            conn.close()
            raise

    def _load_sqlite_vec(self, conn: sqlite3.Connection) -> None:
        try:
            if self._sqlite_vec_loader is not None:
                self._sqlite_vec_loader(conn)
                return
            import sqlite_vec  # type: ignore  # lazy optional dependency

            conn.enable_load_extension(True)
            try:
                sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
        except Exception as exc:
            raise AIIndexDependencyMissing(
                "sqlite-vec is required for the AI vector index. Run `make ai-deps`."
            ) from exc

    async def _fail_job(self, job_id: str, totals: dict, *, status: str, error: str) -> None:
        await asyncio.to_thread(
            self._db_call_sync,
            "ai_update_index_state",
            _write=True,
            status=status,
            last_error=str(error or "")[:1000],
        )
        await asyncio.to_thread(
            self._db_call_sync,
            "ai_update_index_job",
            job_id,
            _write=True,
            status="error",
            totals=totals,
            error=str(error or "")[:1000],
        )
        await self._publish_status(job_id=job_id)

    async def _publish_status(self, *, job_id: str | None = None, job: dict | None = None) -> None:
        try:
            payload = await asyncio.to_thread(self._status_payload_sync)
            if job_id and not job:
                job = await asyncio.to_thread(
                    self._db_call_sync,
                    "ai_get_index_job",
                    job_id,
                )
            if job:
                payload["current_job"] = job
            state = self.state
            if state is not None:
                state_payload = dict(payload.get("state", {}) or {})
                counts = dict(payload.get("counts", {}) or {})
                rebuild_warning = dict(payload.get("rebuild_warning", {}) or {})
                state._emit(
                    "ai_index_status_update",
                    schema_version=1,
                    index={
                        "status": str(state_payload.get("status", "") or "not_built"),
                        "counts": counts,
                        "last_built_at": float(state_payload.get("last_built_at", 0) or 0),
                        "last_error": str(state_payload.get("last_error", "") or ""),
                        "current_job": payload.get("current_job"),
                        "rebuild_warning": rebuild_warning,
                    },
                    embeddings={
                        "active_model_id": str(state_payload.get("active_model_id", "") or ""),
                        "active_dims": int(state_payload.get("active_dims", 0) or 0),
                        "desired_model_id": str(state_payload.get("desired_model_id", "") or ""),
                    },
                )
            if self._broadcast_callback is not None:
                await self._broadcast_callback()
        except Exception:
            log.exception("Failed to publish AI index status update")

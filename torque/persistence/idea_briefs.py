"""SQLite persistence for Idea Briefs and their proposal lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone

from ..idea_briefs import (
    IDEA_BRIEF_BODY_FIELDS,
    IDEA_BRIEF_DEFAULT_STATUS,
    IDEA_BRIEF_TEXT_FIELDS,
    IDEA_BRIEF_PROPOSAL_SCOPE,
    idea_brief_contract_metadata,
    idea_brief_is_archived,
    normalize_idea_brief_status,
)
from ..task_ids import format_idea_brief_id, normalize_group_prefix
from .common import (
    json_payload as _json_payload,
    json_payload_text as _json_payload_text,
    normalize_actor_kind as _normalize_actor_kind,
    slugify as _slugify,
    snapshot_db_payload as _snapshot_db_payload,
    unique_value as _unique_value,
)


IDEA_BRIEF_COLUMNS = (
    "id",
    "slug",
    "group_name",
    "title",
    "status",
    "problem_opportunity",
    "why_it_matters",
    "proposed_shape",
    "smallest_useful_version",
    "risks_tradeoffs",
    "open_questions",
    "thinking_links_json",
    "source_context_json",
    "proposal_json",
    "refinement_log_json",
    "created_by_kind",
    "created_by_id",
    "updated_by_kind",
    "updated_by_id",
    "parked_by_kind",
    "parked_by_id",
    "archived_by_kind",
    "archived_by_id",
    "created_at",
    "updated_at",
    "parked_at",
    "archived_at",
)


def _decode_idea_brief_row(row, cols=None) -> dict | None:
    if not row:
        return None
    item = dict(zip(cols or IDEA_BRIEF_COLUMNS, row))
    item["group"] = item.get("group_name", "")
    item["thinking_links"] = _json_payload(
        item.get("thinking_links_json", "[]"), []
    )
    item["source_context"] = _json_payload(
        item.get("source_context_json", "{}"), {}
    )
    item["proposal"] = _json_payload(item.get("proposal_json", "{}"), {})
    item["refinement_log"] = _json_payload(
        item.get("refinement_log_json", "[]"), []
    )
    item["archived"] = idea_brief_is_archived(item)
    item["proposal_only"] = True
    item["contract"] = "torque.idea_brief.v1"
    return item


class IdeaBriefPersistenceMixin:
    """TorqueDB API for Idea Brief persistence."""

    def next_idea_brief_id(self, group_name: str) -> str:
        """Allocate the next durable Idea Brief ID for a group."""

        group_prefix = normalize_group_prefix(group_name)
        row = self._conn.execute(
            "SELECT next_brief_number FROM idea_brief_id_counters "
            "WHERE group_prefix=?",
            (group_prefix,),
        ).fetchone()
        number = int(row[0] if row else 1)
        self._conn.execute(
            "INSERT OR REPLACE INTO idea_brief_id_counters "
            "(group_prefix, next_brief_number) VALUES (?, ?)",
            (group_prefix, number + 1),
        )
        self._conn.commit()
        return format_idea_brief_id(group_prefix, number)

    def _idea_brief_slug_for_group(self, group_name: str, title: str,
                                   existing_id: str = "") -> str:
        base = _slugify(title or "idea-brief")
        existing_id = str(existing_id or "").strip()
        rows = self._conn.execute(
            "SELECT id, slug FROM idea_briefs "
            "WHERE group_name=? AND archived_at=''",
            (str(group_name or "").strip(),),
        ).fetchall()
        existing = {
            str(slug or "")
            for item_id, slug in rows
            if str(slug or "") and str(item_id or "") != existing_id
        }
        return _unique_value(base, existing)

    @staticmethod
    def _idea_brief_title(row: dict) -> str:
        title = str(row.get("title", "") or "").strip()
        if title:
            return title
        problem = str(row.get("problem_opportunity", "") or "").strip()
        if problem:
            first_line = problem.splitlines()[0].strip()
            return first_line[:96] or "Idea Brief"
        return "Idea Brief"

    def load_idea_brief(self, brief_id: str) -> dict | None:
        brief_id = str(brief_id or "").strip()
        if not brief_id:
            return None
        cursor = self._conn.execute(
            "SELECT " + ", ".join(IDEA_BRIEF_COLUMNS)
            + " FROM idea_briefs WHERE id=?",
            (brief_id,),
        )
        row = cursor.fetchone()
        return _decode_idea_brief_row(row, [d[0] for d in cursor.description])

    def list_idea_briefs(self, *, group: str = "",
                         status: str = "",
                         include_archived: bool = False,
                         created_by_id: str = "",
                         limit: int = 200) -> list[dict]:
        query = (
            "SELECT " + ", ".join(IDEA_BRIEF_COLUMNS)
            + " FROM idea_briefs"
        )
        filters = []
        params = []
        group = str(group or "").strip()
        if group:
            filters.append("group_name=?")
            params.append(group)
        status = str(status or "").strip().lower()
        if status:
            normalize_idea_brief_status(status)
            filters.append("status=?")
            params.append(status)
        if not include_archived:
            filters.append("archived_at=''")
            filters.append("status!='archived'")
        created_by_id = str(created_by_id or "").strip()
        if created_by_id:
            filters.append("created_by_id=?")
            params.append(created_by_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT ?"
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 200
        params.append(max(1, min(limit_value, 1000)))
        cursor = self._conn.execute(query, tuple(params))
        cols = [d[0] for d in cursor.description]
        return [_decode_idea_brief_row(row, cols) for row in cursor.fetchall()]

    def create_idea_brief(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        group = str(row.get("group", row.get("group_name", "")) or "").strip()
        if not group:
            raise ValueError("group is required")
        problem = str(row.get("problem_opportunity", "") or "").strip()
        if not problem:
            raise ValueError("problem_opportunity is required")
        title = self._idea_brief_title(row)
        status = normalize_idea_brief_status(
            row.get("status", IDEA_BRIEF_DEFAULT_STATUS)
        )
        if status != IDEA_BRIEF_DEFAULT_STATUS:
            raise ValueError(
                "Idea Briefs are created as drafts; use idea_brief_park, "
                "idea_brief_archive, or idea_brief_propose for lifecycle changes"
            )
        brief_id = str(row.get("id", "") or "").strip()
        if not brief_id:
            brief_id = self.next_idea_brief_id(group)
        if self.load_idea_brief(brief_id):
            raise ValueError(f"idea brief already exists: {brief_id}")
        now = datetime.now(timezone.utc).isoformat()
        created_by_kind = _normalize_actor_kind(row.get("created_by_kind", "user"))
        slug = str(row.get("slug", "") or "").strip()
        if not slug:
            slug = self._idea_brief_slug_for_group(group, title, brief_id)
        values = {
            "id": brief_id,
            "slug": slug,
            "group_name": group,
            "title": title,
            "status": status,
            "problem_opportunity": problem,
            "why_it_matters": str(row.get("why_it_matters", "") or ""),
            "proposed_shape": str(row.get("proposed_shape", "") or ""),
            "smallest_useful_version": str(row.get("smallest_useful_version", "") or ""),
            "risks_tradeoffs": str(row.get("risks_tradeoffs", "") or ""),
            "open_questions": str(row.get("open_questions", "") or ""),
            "thinking_links_json": _json_payload_text(
                row.get("thinking_links", row.get("thinking_links_json", [])), []
            ),
            "source_context_json": _json_payload_text(
                row.get("source_context", row.get("source_context_json", {})), {}
            ),
            "proposal_json": _json_payload_text(
                row.get("proposal", row.get("proposal_json", {})), {}
            ),
            "refinement_log_json": _json_payload_text(
                row.get("refinement_log", row.get("refinement_log_json", [])), []
            ),
            "created_by_kind": created_by_kind,
            "created_by_id": str(row.get("created_by_id", "") or "").strip(),
            "updated_by_kind": _normalize_actor_kind(
                row.get("updated_by_kind", created_by_kind),
                default=created_by_kind,
            ),
            "updated_by_id": str(
                row.get("updated_by_id", row.get("created_by_id", "")) or ""
            ).strip(),
            "parked_by_kind": _normalize_actor_kind(
                row.get("parked_by_kind", ""),
                default="",
            ) if str(row.get("parked_by_kind", "") or "").strip() else "",
            "parked_by_id": str(row.get("parked_by_id", "") or "").strip(),
            "archived_by_kind": _normalize_actor_kind(
                row.get("archived_by_kind", ""),
                default="",
            ) if str(row.get("archived_by_kind", "") or "").strip() else "",
            "archived_by_id": str(row.get("archived_by_id", "") or "").strip(),
            "created_at": str(row.get("created_at", "") or now),
            "updated_at": str(row.get("updated_at", "") or now),
            "parked_at": str(row.get("parked_at", "") or ""),
            "archived_at": str(row.get("archived_at", "") or ""),
        }
        if values["archived_at"]:
            values["status"] = "archived"
        self._conn.execute(
            "INSERT INTO idea_briefs ("
            + ", ".join(IDEA_BRIEF_COLUMNS)
            + ") VALUES ("
            + ",".join(["?"] * len(IDEA_BRIEF_COLUMNS))
            + ")",
            tuple(values[col] for col in IDEA_BRIEF_COLUMNS),
        )
        self._conn.commit()
        saved = self.load_idea_brief(brief_id)
        if not saved:
            raise RuntimeError(f"failed to load saved idea brief {brief_id}")
        return saved

    async def create_idea_brief_async(self, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "idea_briefs", "create_idea_brief",
            _snapshot_db_payload(row_dict or {}),
        )
    def update_idea_brief(self, brief_id: str, patch: dict) -> dict | None:
        brief_id = str(brief_id or "").strip()
        existing = self.load_idea_brief(brief_id)
        if not existing:
            return None
        if idea_brief_is_archived(existing):
            raise ValueError("Idea Brief is archived")
        patch = dict(patch or {})
        values = {}
        for key in IDEA_BRIEF_TEXT_FIELDS + (
                "slug", "status", "updated_by_kind", "updated_by_id"):
            if key in patch:
                values[key] = patch[key]
        if "status" in values:
            values["status"] = normalize_idea_brief_status(
                values["status"],
                default=existing.get("status", IDEA_BRIEF_DEFAULT_STATUS),
            )
            if values["status"] == "archived":
                raise ValueError("Use idea_brief_archive to archive a brief")
            if (
                    values["status"] == "proposed"
                    and str(existing.get("status", "") or "").strip() != "proposed"):
                raise ValueError("Use idea_brief_propose to propose a brief for review")
        if "title" in values:
            values["title"] = str(values["title"] or "").strip()
            if not values["title"]:
                values["title"] = self._idea_brief_title({
                    **existing,
                    **patch,
                })
            if "slug" not in patch:
                values["slug"] = self._idea_brief_slug_for_group(
                    existing["group_name"],
                    values["title"],
                    brief_id,
                )
        if "slug" in values:
            values["slug"] = str(values["slug"] or "").strip()
        for key in IDEA_BRIEF_BODY_FIELDS:
            if key in values:
                values[key] = str(values[key] or "")
        if "problem_opportunity" in values and not values["problem_opportunity"].strip():
            raise ValueError("problem_opportunity is required")
        if "thinking_links" in patch or "thinking_links_json" in patch:
            values["thinking_links_json"] = _json_payload_text(
                patch.get("thinking_links", patch.get("thinking_links_json", [])), []
            )
        if "source_context" in patch or "source_context_json" in patch:
            values["source_context_json"] = _json_payload_text(
                patch.get("source_context", patch.get("source_context_json", {})), {}
            )
        if "proposal" in patch or "proposal_json" in patch:
            values["proposal_json"] = _json_payload_text(
                patch.get("proposal", patch.get("proposal_json", {})), {}
            )
        if "refinement_log" in patch or "refinement_log_json" in patch:
            values["refinement_log_json"] = _json_payload_text(
                patch.get("refinement_log", patch.get("refinement_log_json", [])), []
            )
        if "updated_by_kind" in values:
            values["updated_by_kind"] = _normalize_actor_kind(values["updated_by_kind"])
        if "updated_by_id" in values:
            values["updated_by_id"] = str(values["updated_by_id"] or "").strip()
        values["updated_at"] = str(
            patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        )
        assignments = ", ".join(f"{key}=?" for key in values)
        self._conn.execute(
            f"UPDATE idea_briefs SET {assignments} WHERE id=?",
            tuple(values.values()) + (brief_id,),
        )
        self._conn.commit()
        return self.load_idea_brief(brief_id)

    async def update_idea_brief_async(self, brief_id: str,
                                      patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "idea_briefs", "update_idea_brief", str(brief_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def refine_idea_brief(self, brief_id: str, patch: dict) -> dict | None:
        existing = self.load_idea_brief(brief_id)
        if not existing:
            return None
        patch = dict(patch or {})
        refinement_note = str(patch.pop("refinement_note", "") or "").strip()
        if refinement_note:
            log_entries = list(existing.get("refinement_log", []) or [])
            timestamp = str(
                patch.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
            )
            log_entries.append({
                "at": timestamp,
                "note": refinement_note,
                "actor_kind": str(patch.get("updated_by_kind", "") or ""),
                "actor_id": str(patch.get("updated_by_id", "") or ""),
            })
            patch["refinement_log"] = log_entries
        return self.update_idea_brief(brief_id, patch)

    async def refine_idea_brief_async(self, brief_id: str,
                                      patch: dict) -> dict | None:
        return await self._enqueue_async_write(
            "idea_briefs", "refine_idea_brief", str(brief_id or ""),
            _snapshot_db_payload(patch or {}),
        )

    def park_idea_brief(self, brief_id: str, *,
                        parked_by_kind: str = "user",
                        parked_by_id: str = "",
                        parked_at: str = "",
                        reason: str = "") -> dict | None:
        existing = self.load_idea_brief(brief_id)
        if not existing:
            return None
        if idea_brief_is_archived(existing):
            raise ValueError("Idea Brief is archived")
        timestamp = str(parked_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(parked_by_kind)
        actor_id = str(parked_by_id or "").strip()
        source_context = dict(existing.get("source_context", {}) or {})
        if reason:
            source_context["park_reason"] = str(reason or "")
        self._conn.execute(
            "UPDATE idea_briefs SET status='parked', parked_at=?, "
            "parked_by_kind=?, parked_by_id=?, source_context_json=?, "
            "updated_at=?, updated_by_kind=?, updated_by_id=? WHERE id=?",
            (
                timestamp,
                actor_kind,
                actor_id,
                _json_payload_text(source_context, {}),
                timestamp,
                actor_kind,
                actor_id,
                str(brief_id or "").strip(),
            ),
        )
        self._conn.commit()
        return self.load_idea_brief(brief_id)

    async def park_idea_brief_async(self, brief_id: str, **kwargs
                                    ) -> dict | None:
        return await self._enqueue_async_write(
            "idea_briefs", "park_idea_brief", str(brief_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )
    def archive_idea_brief(self, brief_id: str, *,
                           archived_by_kind: str = "user",
                           archived_by_id: str = "",
                           archived_at: str = "",
                           reason: str = "") -> dict | None:
        existing = self.load_idea_brief(brief_id)
        if not existing:
            return None
        timestamp = str(archived_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(archived_by_kind)
        actor_id = str(archived_by_id or "").strip()
        source_context = dict(existing.get("source_context", {}) or {})
        if reason:
            source_context["archive_reason"] = str(reason or "")
        self._conn.execute(
            "UPDATE idea_briefs SET status='archived', archived_at=?, "
            "archived_by_kind=?, archived_by_id=?, source_context_json=?, "
            "updated_at=?, updated_by_kind=?, updated_by_id=? WHERE id=?",
            (
                timestamp,
                actor_kind,
                actor_id,
                _json_payload_text(source_context, {}),
                timestamp,
                actor_kind,
                actor_id,
                str(brief_id or "").strip(),
            ),
        )
        self._conn.commit()
        return self.load_idea_brief(brief_id)

    async def archive_idea_brief_async(self, brief_id: str, **kwargs
                                       ) -> dict | None:
        return await self._enqueue_async_write(
            "idea_briefs", "archive_idea_brief", str(brief_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

    def propose_idea_brief(self, brief_id: str, *,
                           proposed_by_kind: str = "user",
                           proposed_by_id: str = "",
                           proposed_at: str = "",
                           note: str = "",
                           review_target: str = "") -> dict | None:
        existing = self.load_idea_brief(brief_id)
        if not existing:
            return None
        if idea_brief_is_archived(existing):
            raise ValueError("Idea Brief is archived")
        timestamp = str(proposed_at or datetime.now(timezone.utc).isoformat())
        actor_kind = _normalize_actor_kind(proposed_by_kind)
        actor_id = str(proposed_by_id or "").strip()
        proposal = dict(existing.get("proposal", {}) or {})
        proposal.update({
            "state": "proposed_for_review",
            "review_scope": IDEA_BRIEF_PROPOSAL_SCOPE,
            "proposed_at": timestamp,
            "proposed_by_kind": actor_kind,
            "proposed_by_id": actor_id,
            "review_target": str(review_target or "").strip(),
            "note": str(note or ""),
            "product_safe": True,
            "proposal_only": True,
            "created_task_id": "",
            "created_decision_id": "",
            "dispatch_state": "none",
            "assignment_state": "none",
            "auto_dispatch": False,
            "auto_assign": False,
            **idea_brief_contract_metadata(),
        })
        self._conn.execute(
            "UPDATE idea_briefs SET status='proposed', proposal_json=?, "
            "updated_at=?, updated_by_kind=?, updated_by_id=? WHERE id=?",
            (
                _json_payload_text(proposal, {}),
                timestamp,
                actor_kind,
                actor_id,
                str(brief_id or "").strip(),
            ),
        )
        self._conn.commit()
        return self.load_idea_brief(brief_id)

    async def propose_idea_brief_async(self, brief_id: str, **kwargs
                                       ) -> dict | None:
        return await self._enqueue_async_write(
            "idea_briefs", "propose_idea_brief", str(brief_id or ""),
            **_snapshot_db_payload(kwargs or {}),
        )

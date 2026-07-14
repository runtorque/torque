"""Playbook candidate and published-playbook persistence."""

from __future__ import annotations

import json
from typing import Optional


class PlaybookPersistenceMixin:
    def replace_playbook_candidates(self, candidates: list[dict],
                                    group_name: str = ""):
        """Replace persisted draft playbook candidates."""
        if group_name:
            self._conn.execute(
                "DELETE FROM playbook_candidates WHERE group_name=?",
                (group_name,))
        else:
            self._conn.execute("DELETE FROM playbook_candidates")

        for candidate in candidates:
            self._conn.execute("""
                INSERT OR REPLACE INTO playbook_candidates
                    (id, group_name, family_key, status, created_at,
                     updated_at, name, root_action, labels,
                     normalized_task_family, entry_action, agent_template,
                     workflow, workflow_shape, dispatch_sequence,
                     action_combination, constraints, evidence,
                     supporting_runs, counterexamples)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                candidate["id"],
                candidate.get("group", ""),
                candidate.get("family_key", ""),
                candidate.get("status", "draft"),
                candidate.get("created_at", 0.0),
                candidate.get("updated_at", candidate.get("created_at", 0.0)),
                candidate.get("name", ""),
                candidate.get("root_action", ""),
                json.dumps(candidate.get("labels", [])),
                candidate.get("normalized_task_family", ""),
                candidate.get("entry_action", ""),
                candidate.get("agent_template", ""),
                json.dumps(candidate.get("workflow", [])),
                json.dumps(candidate.get("workflow_shape",
                                         candidate.get("workflow", []))),
                json.dumps(candidate.get("dispatch_sequence", [])),
                json.dumps(candidate.get("action_combination", [])),
                json.dumps(candidate.get("constraints", {})),
                json.dumps(candidate.get("evidence", {})),
                json.dumps(candidate.get("supporting_runs", [])),
                json.dumps(candidate.get("counterexamples", [])),
            ))
        self._conn.commit()

    def load_playbook_candidates(self, group_name: str = "",
                                 limit: int = 50) -> list[dict]:
        """Load persisted draft playbook candidates."""
        params: list = [limit]
        sql = (
            "SELECT id, group_name, family_key, status, created_at, "
            "updated_at, name, root_action, labels, "
            "normalized_task_family, entry_action, agent_template, "
            "workflow, workflow_shape, dispatch_sequence, "
            "action_combination, constraints, evidence, "
            "supporting_runs, counterexamples "
            "FROM playbook_candidates"
        )
        if group_name:
            sql += " WHERE group_name=?"
            params = [group_name, limit]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "group", "family_key", "status", "created_at",
                "updated_at", "name", "root_action", "labels",
                "normalized_task_family", "entry_action", "agent_template",
                "workflow", "workflow_shape", "dispatch_sequence",
                "action_combination", "constraints", "evidence",
                "supporting_runs", "counterexamples"]
        decoded = []
        for row in rows:
            item = dict(zip(cols, row))
            for key, default in (
                ("labels", []),
                ("workflow", []),
                ("workflow_shape", []),
                ("dispatch_sequence", []),
                ("action_combination", []),
                ("constraints", {}),
                ("evidence", {}),
                ("supporting_runs", []),
                ("counterexamples", []),
            ):
                try:
                    item[key] = json.loads(item.get(key, json.dumps(default)))
                except (json.JSONDecodeError, TypeError):
                    item[key] = default.copy() if isinstance(default, dict) else list(default)
            decoded.append(item)
        return decoded

    def load_playbook_candidate(self, candidate_id: str) -> Optional[dict]:
        """Load one persisted draft playbook candidate by ID."""
        rows = self.load_playbook_candidates(limit=1000)
        for row in rows:
            if row["id"] == candidate_id:
                return row
        return None

    def save_playbook(self, playbook: dict):
        """Insert or replace a generated or published playbook record."""
        self._conn.execute("""
            INSERT OR REPLACE INTO playbooks
                (id, group_name, source_candidate_id, status, generated,
                 review_required, created_at, updated_at, published_at,
                 discarded_at, name, description, match_data, entry_action,
                 agent_template, workflow, constraints, evidence,
                 publication_preview)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            playbook["id"],
            playbook.get("group", ""),
            playbook.get("source_candidate_id", ""),
            playbook.get("status", "draft"),
            1 if playbook.get("generated", True) else 0,
            1 if playbook.get("review_required", True) else 0,
            playbook.get("created_at", 0.0),
            playbook.get("updated_at", playbook.get("created_at", 0.0)),
            playbook.get("published_at"),
            playbook.get("discarded_at"),
            playbook.get("name", ""),
            playbook.get("description", ""),
            json.dumps(playbook.get("match", {})),
            playbook.get("entry_action", ""),
            playbook.get("agent_template", ""),
            json.dumps(playbook.get("workflow", [])),
            json.dumps(playbook.get("constraints", {})),
            json.dumps(playbook.get("evidence", {})),
            json.dumps(playbook.get("publication_preview", {})),
        ))
        self._conn.commit()

    def load_playbooks(self, group_name: str = "", status_filter: str = "",
                       limit: int = 50) -> list[dict]:
        """Load persisted playbook drafts or published recipes."""
        sql = (
            "SELECT id, group_name, source_candidate_id, status, generated, "
            "review_required, created_at, updated_at, published_at, "
            "discarded_at, name, description, match_data, entry_action, "
            "agent_template, workflow, constraints, evidence, "
            "publication_preview FROM playbooks"
        )
        clauses = []
        params: list = []
        if group_name:
            clauses.append("group_name=?")
            params.append(group_name)
        if status_filter:
            clauses.append("status=?")
            params.append(status_filter)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "group", "source_candidate_id", "status", "generated",
                "review_required", "created_at", "updated_at",
                "published_at", "discarded_at", "name", "description",
                "match", "entry_action", "agent_template", "workflow",
                "constraints", "evidence", "publication_preview"]
        decoded = []
        for row in rows:
            item = dict(zip(cols, row))
            item["generated"] = bool(item.get("generated", 1))
            item["review_required"] = bool(item.get("review_required", 1))
            for key, default in (
                ("match", {}),
                ("workflow", []),
                ("constraints", {}),
                ("evidence", {}),
                ("publication_preview", {}),
            ):
                try:
                    item[key] = json.loads(item.get(key, json.dumps(default)))
                except (json.JSONDecodeError, TypeError):
                    item[key] = default.copy() if isinstance(default, dict) else list(default)
            decoded.append(item)
        return decoded

    def load_playbook(self, playbook_id: str) -> Optional[dict]:
        """Load one persisted playbook draft or published recipe."""
        rows = self.load_playbooks(limit=1000)
        for row in rows:
            if row["id"] == playbook_id:
                return row
        return None

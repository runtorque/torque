"""Agent visibility, health projections, and persisted UI selection behavior."""

from __future__ import annotations

from .state import AgentCell, Optional, _safe_float


class StateCoreViewsMixin:
    @staticmethod
    def agent_is_tombstoned(cell) -> bool:
        """Return True when ``cell`` is inside the soft-delete window."""
        return bool(cell and _safe_float(getattr(cell, "deleted_at", 0.0)) > 0)

    def iter_agents(self, *, include_tombstoned: bool = False):
        """Iterate cells, excluding tombstones unless explicitly requested."""
        for cell in self.agents.values():
            if not include_tombstoned and self.agent_is_tombstoned(cell):
                continue
            yield cell

    def iter_active_agents(self):
        """Iterate non-tombstoned cells."""
        return self.iter_agents(include_tombstoned=False)

    def get_active_agent(self, agent_id: str) -> Optional[AgentCell]:
        """Return a non-tombstoned cell by id, or None."""
        cell = self.agents.get(str(agent_id or "").strip())
        if self.agent_is_tombstoned(cell):
            return None
        return cell

    def system_health_metrics(
        self, window: str = "24h", group: str = "", *,
        now: float | None = None,
    ) -> dict:
        return self._metrics_service.system_health_metrics(window, group, now=now)

    def metrics_history(
        self, window: str = "24h", group: str = "", *,
        now: float | None = None,
    ) -> dict:
        return self._metrics_service.metrics_history(window, group, now=now)

    def _metrics_perf_history(
        self, *, since: float, until: float, buckets: list[dict],
        bucket_seconds: int, notes: list[str],
    ) -> dict:
        return self._metrics_service._metrics_perf_history(
            since=since, until=until, buckets=buckets,
            bucket_seconds=bucket_seconds, notes=notes,
        )

    def _metrics_workflow_history(
        self, *, window: str, group: str, now: float
    ) -> tuple[dict, dict, list[str]]:
        return self._metrics_service._metrics_workflow_history(
            window=window, group=group, now=now
        )

    def _system_health_reviews(
        self, tasks: list, *, since: float, until: float,
        bucket_seconds: int, bucket_count: int, review_series: list[int],
    ) -> tuple[dict, list[dict]]:
        return self._metrics_service._system_health_reviews(
            tasks, since=since, until=until, bucket_seconds=bucket_seconds,
            bucket_count=bucket_count, review_series=review_series,
        )

    def _system_health_merges(
        self, tasks: list, *, since: float, until: float,
        bucket_seconds: int, bucket_count: int, merge_series: list[int],
        window_seconds: int,
    ) -> dict:
        return self._metrics_service._system_health_merges(
            tasks, since=since, until=until, bucket_seconds=bucket_seconds,
            bucket_count=bucket_count, merge_series=merge_series,
            window_seconds=window_seconds,
        )

    def _system_health_task_age(self, tasks: list, *, now: float) -> dict:
        return self._metrics_service._system_health_task_age(tasks, now=now)

    def _system_health_utilization(
        self, agent_tasks: list[dict], *, group: str, since: float,
        until: float, bucket_seconds: int, buckets: list[dict],
        busy_series: list[float], capacity_series: list[float],
        utilization_series: list[float],
    ) -> dict:
        return self._metrics_service._system_health_utilization(
            agent_tasks, group=group, since=since, until=until,
            bucket_seconds=bucket_seconds, buckets=buckets,
            busy_series=busy_series, capacity_series=capacity_series,
            utilization_series=utilization_series,
        )

    def selected_agent_id_for_session(self, session_id: str) -> str:
        """Return the selectable parent agent for a terminal session."""
        sid = str(session_id or "").strip()
        if not sid:
            return ""
        for cell in self.iter_active_agents():
            if cell.session_id != sid:
                continue
            if cell.cell_type == "terminal":
                parent = self.get_active_agent(cell.parent_id)
                return parent.id if parent else ""
            return cell.id
        return ""

    def sync_ui_selection_to_session(
        self,
        session_id: str,
        *,
        emit: bool = True,
        persist: bool = True,
    ) -> str:
        """Mirror a known focused terminal session into persisted UI state."""
        selected_id = self.selected_agent_id_for_session(session_id)
        if not selected_id:
            return ""
        cell = self.get_active_agent(selected_id)
        if not cell:
            return ""
        if self.selected_agent_id != selected_id:
            self.selected_agent_id = selected_id
            if emit:
                self._emit(
                    "ui_update",
                    key="selected_agent_id",
                    value=self.selected_agent_id,
                )
            if persist:
                self._db_save_ui("selected_agent_id", self.selected_agent_id)
        if cell.group and self.active_group != cell.group:
            self.active_group = cell.group
            if emit:
                self._emit(
                    "ui_update",
                    key="active_group",
                    value=self.active_group,
                )
            if persist:
                self._db_save_ui("active_group", self.active_group)
        return selected_id

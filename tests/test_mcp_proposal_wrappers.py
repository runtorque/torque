import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import FakeRequest, install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import FakeRequest, install_aiohttp_stub


class MCPProposalWrapperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_mod = importlib.import_module("torque.db")
        self.state_mod = importlib.import_module("torque.state")
        self.mcp_mod = importlib.import_module("torque.mcp")
        self.mcp_mod = importlib.reload(self.mcp_mod)
        self.db = self.db_mod.TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        self.state.groups["g"] = []
        self.state.groups["other"] = []
        self.calls = []

        self.architect = self._add_agent("architect-1", "Architect", kind="architect")
        self.peer = self._add_agent("architect-2", "Productmind", kind="architect")
        self.torqly = self._add_agent("a5a7fc9e", "Torqly", kind="architect")
        self.full_peer = self._add_agent("architect-3", "Full Peer", kind="architect")
        self.cross_group_architect = self._add_agent(
            "architect-other",
            "Other Group Architect",
            kind="architect",
            group="other",
        )
        self.engineer = self._add_agent(
            "engineer-1",
            "Engineer",
            kind="engineer",
            hired_by_architect_id=self.architect.id,
        )
        self.torqly_engineer = self._add_agent(
            "engineer-torqly",
            "Torqly Engineer",
            kind="engineer",
            hired_by_architect_id=self.torqly.id,
        )
        self.worker = self._add_agent("worker-1", "Worker", kind="worker")
        self.state.assign_agent_class(
            self.architect.id,
            "product-manager",
            actor_kind="user",
            base_dir=self.tmp.name,
        )
        self.state.apply_effective_agent_class_for_launch(
            self.architect,
            base_dir=self.tmp.name,
        )

    def _add_agent(self, agent_id, name, *, kind="architect", group="g", **kwargs):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            group=group,
            cell_type="agent",
            kind=kind,
            **kwargs,
        )
        self.state.agents[cell.id] = cell
        self.state.groups.setdefault(group, []).append(cell.id)
        self.state._db_save_agent(cell)
        return cell

    async def _handle_command(self, payload):
        self.calls.append(dict(payload))
        if payload.get("cmd") == "list_actions":
            return {
                "type": "actions",
                "group": payload.get("group", ""),
                "actions": [
                    {"name": "feature/implement"},
                    {"name": "oneshot/fix"},
                ],
            }
        if payload.get("cmd") == "board_update_task":
            task_id = payload.get("id", "")
            self.state.board_update_task(
                task_id,
                **{
                    key: value for key, value in payload.items()
                    if key not in {"cmd", "id"}
                },
            )
            return {"type": "ok", "task_id": task_id}
        if payload.get("cmd") == "board_move_task":
            task_id = payload.get("id", "")
            self.state.board_move_task(task_id, payload.get("lane", ""))
            return {"type": "ok", "task_id": task_id}
        if payload.get("cmd") == "inject_mcp_message":
            return {"type": "ok", "delivered": True}
        if payload.get("cmd") == "board_pickup_architect_task":
            try:
                return self.state.board_pickup_architect_task(
                    payload.get("id", ""),
                    architect_id=payload.get("architect_id", ""),
                    actor_name=payload.get("actor_name", ""),
                    actor_kind=payload.get("actor_kind", ""),
                    reason=payload.get("reason", ""),
                    source=payload.get("source", ""),
                    authorization=payload.get("authorization", {}),
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}
        if payload.get("cmd") == "board_mark_task_covered":
            try:
                return self.state.board_mark_task_covered(
                    payload.get("id", ""),
                    covering_task_id=payload.get("covering_task_id", ""),
                    pr_url=payload.get("pr_url", ""),
                    sha=payload.get("sha", ""),
                    tests_run=payload.get("tests_run", ""),
                    evidence=payload.get("evidence", ""),
                    notes=payload.get("notes", ""),
                    actor_name=payload.get("actor_name", ""),
                    actor_id=payload.get("actor_id", ""),
                    actor_kind=payload.get("actor_kind", ""),
                    authorization=payload.get("authorization", {}),
                    move_to_done=bool(payload.get("move_to_done", False)),
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}
        return {"type": "ok"}

    def _handler(self):
        return self.mcp_mod.create_mcp_handler(self._handle_command, self.state)

    def _freeze_default_architect(self, cell):
        """Apply the real built-in default Architect authority at launch."""
        self.state.assign_agent_class(
            cell.id,
            "default-architect",
            actor_kind="user",
            base_dir=self.tmp.name,
        )
        self.state.apply_effective_agent_class_for_launch(
            cell,
            base_dir=self.tmp.name,
        )
        self.assertEqual("default-architect", cell.effective_agent_class_id)
        self.assertTrue(cell.effective_agent_class_snapshot)

    async def _operator_agent_class_status(self, state, agent_id):
        """Exercise the browser/server class-status command, not its helper."""
        commands_mod = importlib.import_module("torque.commands.agent_classes")

        async def resolve_base_dir(_group):
            return self.tmp.name

        return await commands_mod._handle_agent_class_command(
            {"cmd": "agent_class_status", "agent_id": agent_id},
            state,
            self.db,
            resolve_base_dir,
        )

    async def _call(self, tool_name, arguments=None, *, req_id=1, agent_id=None):
        handler = self._handler()
        return await handler(
            FakeRequest(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments or {}},
                },
                headers={"X-Torque-Cell-Id": agent_id or self.architect.id},
            )
        )

    async def _list_tools(self, *, agent_id=None):
        handler = self._handler()
        response = await handler(
            FakeRequest(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Torque-Cell-Id": agent_id or self.architect.id},
            )
        )
        return {tool["name"] for tool in response.payload["result"]["tools"]}

    def _result_payload(self, response):
        self.assertNotIn("error", response.payload)
        result = response.payload["result"]
        self.assertFalse(result["isError"], result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def _error_text(self, response):
        if "error" in response.payload:
            return response.payload["error"]["message"]
        result = response.payload["result"]
        self.assertTrue(result["isError"], result["content"][0]["text"])
        return result["content"][0]["text"]

    async def test_pm_class_projects_every_tool_with_matching_capabilities(self):
        self.assertEqual(self.architect.effective_agent_class_id, "product-manager")
        # The persisted snapshot stays at legacy catalog scopes and must
        # rehydrate cleanly before the live trusted-registry extension adds
        # Product Manager's group board authority for projection/enforcement.
        persisted = self.architect.effective_agent_class_snapshot["effective_authority"]
        restored = self.mcp_mod.effective_authority_from_snapshot(
            persisted, capabilities=self.mcp_mod.CAPABILITY_CATALOG,
        )
        runtime = self.mcp_mod._effective_class_authority_for_cell(self.architect)
        for capability in (
                "task.update", "task.move", "task.mark_covered", "task.verify",
                "task.reassign", "task.report"):
            self.assertNotEqual("group", restored.capabilities[capability])
            self.assertEqual("group", runtime.capabilities[capability])
        self.assertNotIn("task.dispatch", runtime.capabilities)
        invalid_snapshot = copy.deepcopy(persisted)
        invalid_snapshot["capabilities"]["task.move"] = "group"
        with self.assertRaises(self.mcp_mod.AuthorityValidationError):
            self.mcp_mod.effective_authority_from_snapshot(
                invalid_snapshot, capabilities=self.mcp_mod.CAPABILITY_CATALOG,
            )
        original_snapshot = self.architect.effective_agent_class_snapshot
        self.architect.effective_agent_class_snapshot = dict(original_snapshot)
        self.architect.effective_agent_class_snapshot["effective_authority"] = invalid_snapshot
        self.assertEqual(
            {}, self.mcp_mod._effective_class_authority_for_cell(
                self.architect).capabilities,
        )
        self.architect.effective_agent_class_snapshot = original_snapshot
        self.assertNotIn(
            "effective_agent_class_platform_authority",
            self.architect.effective_agent_class_snapshot,
        )
        self.assertNotIn(
            "effective_agent_class_platform_authority",
            self.db_mod._AGENT_PERSISTED_COLS,
        )
        # Daemon restart restoration recreates the ephemeral pin before a
        # persisted stopped cell can serve its next MCP request, but only for
        # the exact launch-frozen definition version.  The extension itself
        # remains ephemeral and the normal frozen authority still owns the
        # task.dispatch=self reservation.
        frozen_version = self.architect.effective_agent_class_version
        frozen_snapshot = copy.deepcopy(self.architect.effective_agent_class_snapshot)
        self.state._db_save_agent(self.architect)
        restored_state = self.state_mod.MatrixState(db=self.db)
        restored_state.load()
        restored_cell = restored_state.agents[self.architect.id]
        self.assertEqual(frozen_version, restored_cell.effective_agent_class_version)
        self.assertTrue(
            restored_cell.effective_agent_class_platform_authority[
                "group_board_authority"
            ]
        )
        restored_runtime = self.mcp_mod._effective_class_authority_for_cell(
            restored_cell,
        )
        for capability in (
                "task.update", "task.move", "task.mark_covered", "task.verify",
                "task.reassign", "task.report"):
            self.assertEqual("group", restored_runtime.capabilities[capability])
        self.assertNotIn("task.dispatch", restored_runtime.capabilities)

        # A stale v5 snapshot predates the current v8 grants.  Restore
        # must not use the v8 registry to grant it to the stopped v5 session;
        # the status still explicitly tells the operator that relaunch is
        # pending.
        legacy_snapshot = copy.deepcopy(frozen_snapshot)
        legacy_snapshot["version"] = "5"
        self.architect.effective_agent_class_version = "5"
        self.architect.effective_agent_class_snapshot = legacy_snapshot
        self.state._db_save_agent(self.architect)
        restored_state = self.state_mod.MatrixState(db=self.db)
        restored_state.load()
        restored_cell = restored_state.agents[self.architect.id]
        self.assertEqual("5", restored_cell.effective_agent_class_version)
        self.assertEqual("5", restored_cell.effective_agent_class_snapshot["version"])
        self.assertEqual({}, restored_cell.effective_agent_class_platform_authority)
        restored_runtime = self.mcp_mod._effective_class_authority_for_cell(
            restored_cell,
        )
        self.assertEqual("self", restored_runtime.capabilities["task.move"])
        self.assertNotIn("task.dispatch", restored_runtime.capabilities)
        status_response = await self._operator_agent_class_status(
            restored_state, restored_cell.id,
        )
        self.assertEqual("agent_class_status", status_response["type"])
        self.assertTrue(status_response["status"]["pending_next_launch"])
        self.assertTrue(status_response["status"]["apply_state"]["relaunch_required"])
        self.assertEqual("5", status_response["status"]["effective_class_version"])
        self.assertEqual("8", status_response["status"]["next_launch_class_version"])
        self.architect.effective_agent_class_version = frozen_version
        self.architect.effective_agent_class_snapshot = frozen_snapshot
        self.state._db_save_agent(self.architect)
        class_path = Path(self.mcp_mod.__file__).resolve().parent / "builtin_agent_classes" / "product-manager.yaml"
        original_class_text = class_path.read_text(encoding="utf-8")
        agent_classes_mod = importlib.import_module("torque.agent_classes")
        try:
            class_path.write_text(
                original_class_text.replace("version: '8'", "version: '9'").replace(
                    "group_board_authority: true", "group_board_authority: false",
                ),
                encoding="utf-8",
            )
            agent_classes_mod._valid_class_lookup.cache_clear()
            # A v8 true grant likewise cannot survive into a v9 false
            # definition through restore.  It is pending next launch rather
            # than silently retaining or dropping authority by current YAML.
            restored_state = self.state_mod.MatrixState(db=self.db)
            restored_state.load()
            restored_cell = restored_state.agents[self.architect.id]
            self.assertEqual({}, restored_cell.effective_agent_class_platform_authority)
            restored_runtime = self.mcp_mod._effective_class_authority_for_cell(
                restored_cell,
            )
            self.assertEqual("self", restored_runtime.capabilities["task.move"])
            self.assertNotIn("task.dispatch", restored_runtime.capabilities)
            status_response = await self._operator_agent_class_status(
                restored_state, restored_cell.id,
            )
            self.assertEqual("agent_class_status", status_response["type"])
            self.assertTrue(status_response["status"]["pending_next_launch"])
            self.assertTrue(status_response["status"]["apply_state"]["relaunch_required"])
            self.assertEqual("8", status_response["status"]["effective_class_version"])
            self.assertEqual("9", status_response["status"]["next_launch_class_version"])
            # Existing session remains frozen despite on-disk mutation.
            unchanged_runtime = self.mcp_mod._effective_class_authority_for_cell(self.architect)
            self.assertEqual("group", unchanged_runtime.capabilities["task.move"])
            # Relaunch reruns registry resolution and observes the changed file.
            agent_classes_mod._valid_class_lookup.cache_clear()
            self.state.apply_effective_agent_class_for_launch(
                self.architect, base_dir=self.tmp.name,
            )
            relaunched_runtime = self.mcp_mod._effective_class_authority_for_cell(self.architect)
            self.assertEqual("self", relaunched_runtime.capabilities["task.move"])
        finally:
            class_path.write_text(original_class_text, encoding="utf-8")
            agent_classes_mod._valid_class_lookup.cache_clear()
            self.state.apply_effective_agent_class_for_launch(
                self.architect, base_dir=self.tmp.name,
            )
        self.assertEqual(
            "group",
            self.mcp_mod._effective_class_authority_for_cell(
                self.architect).capabilities["task.move"],
        )
        tool_names = await self._list_tools()

        expected_task_surface = {
            "task_list", "task_get", "task_chain", "task_create",
            "task_claim", "task_update", "task_move", "task_mark_covered",
            "task_coverage_reconcile", "task_artifact_upload", "task_verify",
            "task_reassign", "task_derive", "task_progress",
            "task_complete", "task_blocked", "task_error",
        }
        self.assertTrue(expected_task_surface <= tool_names)
        # The PM prohibition is enforced in its frozen projection; a default
        # Engineer retains dispatch so this cannot fail open by removing it
        # everywhere.
        self.assertNotIn("task_dispatch", tool_names)
        self.state.assign_agent_class(
            self.engineer.id,
            "default-engineer",
            actor_kind="user",
            base_dir=self.tmp.name,
        )
        self.state.apply_effective_agent_class_for_launch(
            self.engineer,
            base_dir=self.tmp.name,
        )
        engineer_tool_names = await self._list_tools(agent_id=self.engineer.id)
        self.assertIn("task_dispatch", engineer_tool_names)
        for name in {
            "context", "tool_search", "peer_list", "peer_message",
            "peer_inbox", "peer_reply", "user_message", "raise", "journal_write",
        }:
            self.assertIn(name, tool_names)

        denied = {
            "engineer_hire",
            "engineer_lifecycle",
            "engineer_specializations_update",
            "agent_message",
            "feedback_request",
            "behavior_overlay_admin",
            "deploy_get",
            "settings_get",
            "telemetry_query",
            "agent_ask_answer",
            "architect_engineer_hire",
            "architect_engineer_rehire",
            "architect_engineer_dismiss",
            "architect_engineer_set_specializations",
            "architect_pending_hire_list",
            "architect_task_create",
            "architect_engineer_message",
            "architect_engineer_feedback_request",
            "architect_behavior_overlay_propose_for_engineer",
            "architect_behavior_overlay_propose_for_role",
            "architect_behavior_overlay_approve",
            "architect_behavior_overlay_reject",
            "architect_behavior_overlay_rollback_role",
            "architect_deploy_state",
            "architect_get_architect_settings",
            "architect_mcp_calls",
            "architect_engineer_reply",
            "architect_decision_create",
            "architect_decision_update",
        }
        self.assertFalse(denied & tool_names)

        for index, tool_name in enumerate(sorted(denied), start=10):
            response = await self._call(
                tool_name,
                {"architect_id": self.peer.id, "message": "raw", "title": "raw"},
                req_id=index,
            )
            self.assertIn("Unknown tool", self._error_text(response), tool_name)
        self.assertEqual([], self.calls)

    async def test_pm_can_read_peer_idea_brief_within_group_only(self):
        peer_brief = self.db.create_idea_brief({
            "group": "g",
            "title": "Peer-authored brief",
            "problem_opportunity": "A same-group PM must be able to review this.",
            "created_by_kind": "architect",
            "created_by_id": self.peer.id,
        })
        second_peer_brief = self.db.create_idea_brief({
            "group": "g",
            "title": "Second peer brief",
            "problem_opportunity": "A second visible brief proves the limit is real.",
            "created_by_kind": "architect",
            "created_by_id": self.peer.id,
        })
        own_brief = self.db.create_idea_brief({
            "group": "g",
            "title": "Caller-authored brief",
            "problem_opportunity": "The full get response keeps every body field.",
            "why_it_matters": "List probes must not pay for this content.",
            "proposed_shape": "Use a summary row and named full retrieval.",
            "smallest_useful_version": "Bounded list serialization.",
            "risks_tradeoffs": "Avoid changing read authority.",
            "open_questions": "None.",
            "source_context": {"task": "TORQUE:1289"},
            "created_by_kind": "architect",
            "created_by_id": self.architect.id,
        })
        other_group_brief = self.db.create_idea_brief({
            "group": "other",
            "title": "Other-group brief",
            "problem_opportunity": "This must stay outside the PM's reach.",
            "created_by_kind": "architect",
            "created_by_id": self.cross_group_architect.id,
        })

        tool_names = await self._list_tools()
        self.assertTrue({"idea_brief_list", "idea_brief_get"} <= tool_names)
        self.assertFalse({
            "idea_brief_create", "idea_brief_update", "idea_brief_transition",
        } & tool_names)

        listed = self._result_payload(await self._call(
            "idea_brief_list", {"limit": 2},
        ))
        self.assertEqual(3, listed["idea_briefs_total"])
        self.assertEqual(2, listed["idea_briefs_returned"])
        self.assertTrue(listed["idea_briefs_capped"])
        self.assertEqual(2, len(listed["idea_briefs"]))
        self.assertEqual(
            {
                "id", "slug", "title", "status", "group", "created_by_id",
                "created_by_kind", "updated_at", "archived", "caller_owned",
            },
            set(listed["idea_briefs"][0]),
        )
        listed_ids = {brief["id"] for brief in listed["idea_briefs"]}
        self.assertNotIn(other_group_brief["id"], listed_ids)
        self.assertTrue({peer_brief["id"], second_peer_brief["id"], own_brief["id"]} - listed_ids)

        peer_payload = self._result_payload(await self._call(
            "idea_brief_get", {"idea_brief": peer_brief["id"]},
        ))
        self.assertEqual(peer_brief["id"], peer_payload["id"])
        self.assertFalse(peer_payload["caller_owned"])

        own_payload = self._result_payload(await self._call(
            "idea_brief_get", {"idea_brief": own_brief["id"]},
        ))
        self.assertEqual(own_brief["proposed_shape"], own_payload["proposed_shape"])
        self.assertEqual({"task": "TORQUE:1289"}, own_payload["source_context"])
        self.assertTrue(own_payload["caller_owned"])
        for raw_field in (
                "thinking_links_json", "source_context_json", "proposal_json",
                "refinement_log_json"):
            self.assertNotIn(raw_field, own_payload)

        outside_group = await self._call(
            "idea_brief_get", {"idea_brief": other_group_brief["id"]},
        )
        self.assertIn("outside Product Manager group scope", self._error_text(outside_group))

    async def test_pm_has_full_same_group_board_authority(self):
        # Revoking task.dispatch must not remove the independently granted
        # PM board surface.  Keep these retained-authority assertions direct
        # so a future projection change fails closed rather than silently
        # shrinking PM's permitted product-follow-through operations.
        tool_names = await self._list_tools()
        self.assertTrue({
            "task_create", "task_reassign", "task_update", "task_move",
            "task_get",
        } <= tool_names)
        self.assertNotIn("task_dispatch", tool_names)
        dispatch_denied = await self._call(
            "task_dispatch", {"task": "not-a-task"}, req_id=1,
        )
        self.assertIn("Unknown tool", self._error_text(dispatch_denied))

        created = self._result_payload(await self._call(
            "task_create",
            {
                "title": "PM queued product intake",
                "group": "g",
                "labels": ["product-proposal", "proposal-only"],
            },
            req_id=2,
        ))
        created_task = self.state.board_tasks[created["id"]]
        self.assertEqual("product_task_proposal_created", created["type"])
        self.assertEqual("queued", created["dispatch_state"])
        self.assertEqual("queued", created_task.dispatch_state)
        self.assertEqual("", created_task.assigned_engineer_id)
        self.assertEqual(self.architect.id, created_task.created_by_architect_id)

        product_task = self.state.board_add_task(
            "Product-label retention", "g", lane="Backlog",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        engineer_task = self.state.board_add_task(
            "Engineer-created execution", "g", lane="To Do",
            labels=["execution"], created_by_engineer_id=self.engineer.id,
        )
        system_task = self.state.board_add_task(
            "System-derived execution", "g", lane="In Progress",
            labels=["pipeline"],
        )
        done_task = self.state.board_add_task(
            "Completed non-product task", "g", lane="Done", labels=["ops"],
        )
        archived_task = self.state.board_add_task(
            "Archived task", "g", lane="Archived", labels=["ops"],
        )
        other_task = self.state.board_add_task(
            "Other-group task", "other", lane="To Do", labels=["ops"],
        )

        pm_list = self._result_payload(await self._call("task_list", {"limit": 100}))
        full_architect_list = self._result_payload(await self._call(
            "task_list", {"limit": 100}, agent_id=self.full_peer.id,
        ))
        pm_ids = {item["id"] for item in pm_list["tasks"]}
        full_ids = {item["id"] for item in full_architect_list["tasks"]}
        expected_ids = {
            task.id for task in self.state.board_tasks.values()
            if task.group == "g" and task.lane != "Archived"
        }
        self.assertEqual(expected_ids, pm_ids)
        full_non_archived_ids = {
            item["id"] for item in full_architect_list["tasks"]
            if item["lane"] != "Archived"
        }
        self.assertEqual(full_non_archived_ids, pm_ids)
        self.assertNotIn(archived_task.id, pm_ids)
        self.assertNotIn(other_task.id, pm_ids)
        self.assertTrue({"Backlog", "To Do", "In Progress", "Done"} <= {
            item["lane"] for item in pm_list["tasks"]
        })
        self.assertEqual(len(expected_ids), pm_list["count"])

        shown_non_product = self._result_payload(await self._call(
            "task_get", {"task": engineer_task.id}, req_id=20,
        ))
        self.assertEqual(shown_non_product["id"], engineer_task.id)
        cross_group_show = await self._call(
            "task_get", {"task": other_task.id}, req_id=21,
        )
        self.assertIn("outside Product Manager group scope", self._error_text(cross_group_show))

        retained = await self._call(
            "task_update",
            {
                "task": product_task.id,
                "labels": ["retained"],
                "suggested_action": "oneshot/fix",
            },
            req_id=2,
        )
        self.assertIn("must retain", self._error_text(retained))
        self.assertTrue({"product-proposal", "proposal-only"} <= set(product_task.labels))
        self.assertEqual("", product_task.suggested_action)

        updated = await self._call(
            "task_update", {"task": engineer_task.id, "title": "PM-updated engineer task"},
            req_id=3,
        )
        self.assertEqual("ok", self._result_payload(updated)["type"])
        self.assertEqual("PM-updated engineer task", engineer_task.task)
        reassigned = await self._call(
            "task_reassign", {"task": engineer_task.id, "new_engineer_id": self.engineer.id},
            req_id=4,
        )
        self.assertEqual(self.engineer.id, self._result_payload(reassigned)["assigned_engineer_id"])
        moved = await self._call(
            "task_move", {"task": engineer_task.id, "new_lane": "Done"},
            req_id=5,
        )
        self.assertEqual("task_moved", self._result_payload(moved)["type"])
        self.assertEqual("Done", engineer_task.lane)
        covered = await self._call(
            "task_mark_covered", {"task": system_task.id, "notes": "PM board hygiene"},
            req_id=6,
        )
        self.assertEqual("task_marked_covered", self._result_payload(covered)["type"])
        verified = await self._call(
            "task_verify", {"task": system_task.id, "state": "passed"}, req_id=7,
        )
        self.assertEqual("ok", self._result_payload(verified)["type"])
        self.assertEqual("board_verify_task", self.calls[-1]["cmd"])

        user_message = await self._call(
            "user_message",
            {"message": "Execution task is now visible.", "context_task_ids": [system_task.id]},
            req_id=8,
        )
        self.assertEqual("ok", self._result_payload(user_message)["type"])

        before = (other_task.task, other_task.lane, list(other_task.messages))
        denied = await self._call(
            "task_move", {"task": other_task.id, "new_lane": "Done"}, req_id=9,
        )
        self.assertIn("error", denied.payload)
        self.assertEqual(-32003, denied.payload["error"]["code"])
        self.assertIn("Product Manager group scope", denied.payload["error"]["message"])
        self.assertNotIn("Unknown tool", denied.payload["error"]["message"])
        self.assertEqual(before, (other_task.task, other_task.lane, list(other_task.messages)))

    async def test_pm_task_update_refuses_dispatched_same_group_task(self):
        self.worker.status = "running"
        self.state._db_save_agent(self.worker)
        task = self.state.board_add_task(
            "Live execution task",
            "g",
            lane="In Progress",
            description="Original description",
            labels=["execution", "keep"],
            action_name="feature/implement",
            action_vars={"original": "value"},
            agent_id=self.worker.id,
            created_by_engineer_id=self.engineer.id,
            dispatch_state="live",
        )
        task.updated_at = "2000-01-01T00:00:00+00:00"
        self.state._db_save_task(task)
        before = (
            task.task,
            task.description,
            list(task.labels),
            task.action_name,
            dict(task.action_vars),
            task.updated_at,
            len(self.calls),
        )

        refused = await self._call(
            "task_update",
            {
                "task": task.id,
                "title": "Unsafe amendment",
                "description": "Unsafe amended description",
                "labels": ["unsafe"],
                "action_name": "oneshot/fix",
                "action_vars": {"unsafe": "value"},
            },
        )

        error = json.loads(self._error_text(refused))
        self.assertEqual(error["reason"], "task_dispatched")
        self.assertEqual(error["dispatch_state"], "live")
        self.assertEqual(
            "Task has active dispatched work. Stop the active execution "
            "stream before editing it.",
            error["message"],
        )
        self.assertEqual(
            (
                task.task,
                task.description,
                task.labels,
                task.action_name,
                task.action_vars,
                task.updated_at,
                len(self.calls),
            ),
            before,
        )

    async def test_custom_metadata_cannot_activate_product_manager_exceptions(self):
        # Custom classes may author metadata.  A custom Architect that copies
        # the PM marker must not receive the PM-only same-group reassignment
        # route or current-task reporter/derive projection.
        custom_snapshot = copy.deepcopy(self.architect.effective_agent_class_snapshot)
        custom_snapshot["id"] = "custom-productish"
        custom_snapshot["builtin"] = False
        custom_snapshot["metadata"]["task_authority_mode"] = "creator-proposal-only"
        custom_snapshot["effective_authority"]["capabilities"].pop(
            "task.dispatch", None
        )
        self.architect.effective_agent_class_id = "custom-productish"
        self.architect.effective_agent_class_snapshot = custom_snapshot

        tool_names = await self._list_tools()
        self.assertNotIn("task_progress", tool_names)
        self.assertNotIn("task_derive", tool_names)
        self.assertNotIn("task_dispatch", tool_names)

        peer_engineer = self._add_agent(
            "engineer-peer",
            "Peer Engineer",
            kind="engineer",
            hired_by_architect_id=self.peer.id,
        )
        own_task = self.state.board_add_task(
            "Custom-class task", "g", created_by_architect_id=self.architect.id
        )
        denied = await self._call(
            "task_reassign",
            {"task": own_task.id, "new_engineer_id": peer_engineer.id},
            req_id=2,
        )
        self.assertIn("error", denied.payload)
        self.assertEqual(-32602, denied.payload["error"]["code"])
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_reassign.",
            denied.payload["error"]["message"],
        )
        self.assertEqual("", own_task.assigned_engineer_id)

    async def test_routed_product_proposal_task_can_be_picked_up_by_same_group_architect(self):
        # Exercise the public JSON-RPC route under a frozen default Architect
        # snapshot.  A plain unclassified test caller skips the class gate and
        # would not catch a pre-handler target-scope denial.
        self._freeze_default_architect(self.torqly)
        proposal = await self._call(
            "architect_task_propose",
            {
                "title": "Direct pickup root",
                "description": "PM proposal that should remain the root.",
                "suggested_action": "feature/implement",
            },
            req_id=2,
        )
        task_id = self._result_payload(proposal)["id"]
        task = self.state.board_tasks[task_id]
        self.assertEqual("", task.assigned_architect_id)
        self.assertEqual("", task.assigned_engineer_id)

        routed = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.torqly.id,
                "message": "Please pick up this product-proposal root directly.",
                "context_task_ids": [task_id],
                "context_summary": "Direct Architect pickup request.",
            },
            req_id=3,
        )
        route_id = self._result_payload(routed)["message_id"]

        pickup = await self._call(
            "task_claim",
            {
                "task": task_id,
                "reason": "Accepted as original implementation root.",
                "source": f"Blueprint peer message {route_id}",
            },
            req_id=4,
            agent_id=self.torqly.id,
        )
        payload = self._result_payload(pickup)
        self.assertEqual("task_picked_up", payload["type"])
        self.assertEqual(self.torqly.id, payload["assigned_architect_id"])

        refreshed = self.state.board_tasks[task_id]
        self.assertEqual(self.torqly.id, refreshed.assigned_architect_id)
        self.assertEqual(self.architect.id, refreshed.created_by_architect_id)
        evidence = refreshed.completion_evidence
        self.assertIn("architect_pickup", evidence["sources"])
        pickup_evidence = evidence["architect_pickup"]
        self.assertEqual(
            "",
            pickup_evidence["previous_assignment"]["assigned_architect_id"],
        )
        self.assertEqual(
            "routed_product_proposal_root_pickup",
            pickup_evidence["authorization"]["scope"],
        )
        self.assertEqual(route_id, pickup_evidence["authorization"]["route_message_id"])
        self.assertTrue(pickup_evidence["authorization"]["route_thread_id"])
        self.assertEqual(f"Blueprint peer message {route_id}", pickup_evidence["source"])
        self.assertEqual("architect_pickup", refreshed.messages[-1]["action"])

        default_proposal = await self._call(
            "architect_task_propose", {"title": "Default product pickup source"},
            req_id=5,
        )
        default_task_id = self._result_payload(default_proposal)["id"]
        default_route = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.torqly.id,
                "message": "Claim with the default product route source.",
                "context_task_ids": [default_task_id],
            },
            req_id=6,
        )
        default_route_id = self._result_payload(default_route)["message_id"]
        default_pickup = await self._call(
            "task_claim", {"task": default_task_id}, req_id=7,
            agent_id=self.torqly.id,
        )
        self.assertEqual("task_picked_up", self._result_payload(default_pickup)["type"])
        self.assertEqual(
            f"product-peer route {default_route_id}",
            self.state.board_tasks[default_task_id].completion_evidence[
                "architect_pickup"
            ]["source"],
        )

    async def test_hired_engineer_task_claim_action_bind_assign_and_dispatch(self):
        """The hiring Architect has a bounded no-recreation Engineer handoff."""
        self._freeze_default_architect(self.torqly)
        task = self.state.board_add_task(
            "Engineer-filed dispatch handoff",
            "g",
            assigned_engineer_id=self.torqly_engineer.id,
            created_by_engineer_id=self.torqly_engineer.id,
            dispatch_state="queued",
        )
        original = (
            task.assigned_architect_id,
            task.action_name,
            task.dispatch_state,
        )

        before_claim_update = await self._call(
            "task_update",
            {"task": task.id, "action_name": "feature/implement"},
            req_id=80,
            agent_id=self.torqly.id,
        )
        self.assertIn("engineer provenance", self._error_text(before_claim_update))
        self.assertEqual(original, (
            task.assigned_architect_id, task.action_name, task.dispatch_state,
        ))

        before_claim_dispatch = await self._call(
            "agent_message",
            {
                "agent": self.torqly_engineer.id,
                "task": task.id,
                "message": "Dispatch this engineer-filed task.",
            },
            req_id=81,
            agent_id=self.torqly.id,
        )
        self.assertIn("engineer provenance", self._error_text(before_claim_dispatch))
        self.assertEqual("queued", task.dispatch_state)

        inferred_decline = await self._call(
            "agent_message",
            {
                "agent": self.torqly_engineer.id,
                "message": f"Please pick up {task.id}.",
            },
            req_id=82,
            agent_id=self.torqly.id,
        )
        advisory = self._result_payload(inferred_decline)["dispatch_advisory"]
        self.assertIn("No task was dispatched", advisory)
        self.assertIn("engineer provenance", advisory)
        self.assertEqual("queued", task.dispatch_state)

        self._freeze_default_architect(self.full_peer)
        unrelated_claim = await self._call(
            "task_claim", {"task": task.id}, req_id=83,
            agent_id=self.full_peer.id,
        )
        self.assertIn("engineer provenance", self._error_text(unrelated_claim))
        self.assertEqual("", task.assigned_architect_id)

        claim = await self._call(
            "task_claim",
            {"task": task.id, "reason": "Accept Engineer finding."},
            req_id=84,
            agent_id=self.torqly.id,
        )
        claim_payload = self._result_payload(claim)
        self.assertEqual("task_picked_up", claim_payload["type"])
        self.assertEqual(self.torqly.id, task.assigned_architect_id)
        self.assertEqual(
            "engineer_created_task_handoff",
            task.completion_evidence["architect_pickup"]["authorization"]["scope"],
        )
        self.assertEqual(self.torqly_engineer.id, task.created_by_engineer_id)
        self.assertEqual("", task.created_by_architect_id)
        pickup = task.completion_evidence["architect_pickup"]
        self.assertEqual("hired-engineer provenance", pickup["source"])
        self.assertIn("Source: hired-engineer provenance", task.messages[-1]["message"])

        action_bound = await self._call(
            "task_update",
            {"task": task.id, "action_name": "feature/implement"},
            req_id=85,
            agent_id=self.torqly.id,
        )
        self.assertEqual("ok", self._result_payload(action_bound)["type"])
        self.assertEqual("feature/implement", task.action_name)

        reassigned = await self._call(
            "task_reassign",
            {"task": task.id, "new_engineer_id": self.torqly_engineer.id},
            req_id=86,
            agent_id=self.torqly.id,
        )
        self.assertEqual(self.torqly_engineer.id,
                         self._result_payload(reassigned)["assigned_engineer_id"])

        dispatched = await self._call(
            "agent_message",
            {
                "agent": self.torqly_engineer.id,
                "task": task.id,
                "message": "Dispatch the accepted task now.",
            },
            req_id=87,
            agent_id=self.torqly.id,
        )
        self.assertEqual("live", self._result_payload(dispatched)["dispatch_state"])
        self.assertEqual("live", task.dispatch_state)
        # TORQUE:1215 protects an actually active execution stream, not the
        # durable historical ``live`` marker after a worker has exited.
        self.worker.status = "running"
        self.state._db_save_agent(self.worker)
        self.state.board_update_task(task.id, agent_id=self.worker.id)

        protected = await self._call(
            "task_update",
            {"task": task.id, "action_name": "oneshot/fix"},
            req_id=88,
            agent_id=self.torqly.id,
        )
        protected_error = json.loads(self._error_text(protected))
        self.assertEqual("task_dispatched", protected_error["reason"])
        self.assertEqual("feature/implement", task.action_name)

    async def test_frozen_default_architect_reassigns_claimed_product_root_canonically(self):
        # The product manager creates and routes a peer-owned proposal root;
        # the frozen default Architect claims it through the public route.
        # This exercises tools/list followed by canonical tools/call rather
        # than directly invoking the scoped compatibility handler.
        self._freeze_default_architect(self.torqly)
        proposal = await self._call(
            "architect_task_propose",
            {"title": "Canonical claimed-root reassignment"},
            req_id=10,
        )
        task_id = self._result_payload(proposal)["id"]
        routed = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.torqly.id,
                "message": "Please claim and route this product root.",
                "context_task_ids": [task_id],
            },
            req_id=11,
        )
        route_id = self._result_payload(routed)["message_id"]
        claimed = await self._call(
            "task_claim",
            {"task": task_id, "source": f"Blueprint peer route {route_id}"},
            req_id=12,
            agent_id=self.torqly.id,
        )
        self.assertEqual("task_picked_up", self._result_payload(claimed)["type"])

        tool_names = await self._list_tools(agent_id=self.torqly.id)
        self.assertIn("task_reassign", tool_names)
        self.assertNotIn("architect_task_reassign", tool_names)

        response = await self._call(
            "task_reassign",
            {"task": task_id, "new_engineer_id": self.torqly_engineer.id},
            req_id=13,
            agent_id=self.torqly.id,
        )
        payload = self._result_payload(response)
        self.assertEqual("ok", payload["type"])
        self.assertEqual(task_id, payload["task_id"])
        self.assertEqual(self.torqly_engineer.id, payload["assigned_engineer_id"])
        task = self.state.board_tasks[task_id]
        self.assertEqual(self.torqly.id, task.assigned_architect_id)
        self.assertEqual(self.torqly_engineer.id, task.assigned_engineer_id)
        self.assertEqual("Backlog", task.lane)
        self.assertEqual("", task.agent_id)
        self.assertEqual("queued", task.dispatch_state)

    async def test_frozen_default_architect_reassign_denials_do_not_mutate(self):
        self._freeze_default_architect(self.torqly)
        owned = self.state.board_add_task(
            "Frozen reassign denial root",
            "g",
            created_by_architect_id=self.torqly.id,
        )
        before = (owned.assigned_engineer_id, owned.updated_at)

        non_hired = await self._call(
            "task_reassign",
            {"task": owned.id, "new_engineer_id": self.engineer.id},
            req_id=14,
            agent_id=self.torqly.id,
        )
        self.assertIn("error", non_hired.payload)
        self.assertEqual(-32602, non_hired.payload["error"]["code"])
        self.assertEqual(before, (owned.assigned_engineer_id, owned.updated_at))

        unclaimed_peer_root = self.state.board_add_task(
            "Unclaimed peer proposal root",
            "g",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        unclaimed_before = (
            unclaimed_peer_root.assigned_architect_id,
            unclaimed_peer_root.assigned_engineer_id,
            unclaimed_peer_root.updated_at,
        )
        unclaimed = await self._call(
            "task_reassign",
            {"task": unclaimed_peer_root.id, "new_engineer_id": self.torqly_engineer.id},
            req_id=141,
            agent_id=self.torqly.id,
        )
        self.assertIn("error", unclaimed.payload)
        self.assertEqual(-32602, unclaimed.payload["error"]["code"])
        self.assertEqual(
            unclaimed_before,
            (
                unclaimed_peer_root.assigned_architect_id,
                unclaimed_peer_root.assigned_engineer_id,
                unclaimed_peer_root.updated_at,
            ),
        )

        closed = self.state.board_add_task(
            "Closed frozen reassign root",
            "g",
            lane="Done",
            created_by_architect_id=self.torqly.id,
        )
        closed_before = (closed.assigned_engineer_id, closed.updated_at)
        closed_response = await self._call(
            "task_reassign",
            {"task": closed.id, "new_engineer_id": self.torqly_engineer.id},
            req_id=15,
            agent_id=self.torqly.id,
        )
        self.assertEqual("Task is already closed", self._error_text(closed_response))
        self.assertEqual(
            closed_before,
            (closed.assigned_engineer_id, closed.updated_at),
        )

        cross_group = self.state.board_add_task(
            "Cross-group frozen reassign root",
            "other",
            created_by_architect_id=self.torqly.id,
        )
        cross_group_before = (cross_group.assigned_engineer_id, cross_group.updated_at)
        cross_group_response = await self._call(
            "task_reassign",
            {"task": cross_group.id, "new_engineer_id": self.torqly_engineer.id},
            req_id=151,
            agent_id=self.torqly.id,
        )
        self.assertEqual("Task not found", self._error_text(cross_group_response))
        self.assertEqual(
            cross_group_before,
            (cross_group.assigned_engineer_id, cross_group.updated_at),
        )

        self.torqly_engineer.dismissed_at = 1.0
        dismissed = await self._call(
            "task_reassign",
            {"task": owned.id, "new_engineer_id": self.torqly_engineer.id},
            req_id=152,
            agent_id=self.torqly.id,
        )
        dismissed_text = self._error_text(dismissed)
        self.assertIn("dismissed", dismissed_text)
        self.assertEqual(before, (owned.assigned_engineer_id, owned.updated_at))
        self.torqly_engineer.dismissed_at = 0.0

        malformed = await self._call(
            "task_reassign",
            {"task": "not-a-task", "new_engineer_id": self.torqly_engineer.id},
            req_id=16,
            agent_id=self.torqly.id,
        )
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_reassign.",
            self._error_text(malformed),
        )
        self.assertEqual(before, (owned.assigned_engineer_id, owned.updated_at))

        # The Product Manager may now reassign any in-group task to a
        # same-group Engineer; this is distinct from the default Architect
        # denials exercised above.
        pm_reassign = await self._call(
            "task_reassign",
            {"task": owned.id, "new_engineer_id": self.torqly_engineer.id},
            req_id=17,
            agent_id=self.architect.id,
        )
        self.assertEqual(
            self.torqly_engineer.id,
            self._result_payload(pm_reassign)["assigned_engineer_id"],
        )

    async def test_architect_task_pickup_denies_unrouted_non_pm_or_already_claimed_tasks(self):
        self._freeze_default_architect(self.torqly)
        unrouted = self.state.board_add_task(
            "Unrouted product task",
            "g",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        no_route = await self._call(
            "task_claim",
            {"task": unrouted.id},
            req_id=20,
            agent_id=self.torqly.id,
        )
        no_route_text = self._error_text(no_route)
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_claim.",
            no_route_text,
        )
        self.assertEqual("", unrouted.assigned_architect_id)
        self.assertEqual({}, unrouted.completion_evidence)
        self.assertEqual([], unrouted.messages)

        wrong_group = self.state.board_add_task(
            "Wrong-group product task",
            "other",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.cross_group_architect.id,
        )
        wrong_group_denied = await self._call(
            "task_claim",
            {"task": wrong_group.id},
            req_id=201,
            agent_id=self.torqly.id,
        )
        wrong_group_text = self._error_text(wrong_group_denied)
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_claim.",
            wrong_group_text,
        )
        self.assertEqual("", wrong_group.assigned_architect_id)
        self.assertEqual({}, wrong_group.completion_evidence)

        ineligible = self.state.board_add_task(
            "Ineligible non-product task",
            "g",
            created_by_architect_id=self.peer.id,
        )
        ineligible_denied = await self._call(
            "task_claim",
            {"task": ineligible.id},
            req_id=202,
            agent_id=self.torqly.id,
        )
        ineligible_text = self._error_text(ineligible_denied)
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_claim.",
            ineligible_text,
        )
        self.assertEqual("", ineligible.assigned_architect_id)
        self.assertEqual({}, ineligible.completion_evidence)

        normal_architect_task = self.state.board_add_task(
            "Normal architect task",
            "g",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.torqly.id,
        )
        normal = await self._call(
            "task_claim",
            {"task": normal_architect_task.id},
            req_id=21,
            agent_id=self.full_peer.id,
        )
        self.assertIn("product proposal", self._error_text(normal))

        proposal = await self._call(
            "architect_task_propose",
            {"title": "Already claimed proposal root"},
            req_id=22,
        )
        claimed_id = self._result_payload(proposal)["id"]
        self.state.board_update_task(claimed_id, assigned_architect_id=self.peer.id)
        await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.torqly.id,
                "message": "Route to Torqly but already claimed elsewhere.",
                "context_task_ids": [claimed_id],
            },
            req_id=23,
        )
        claimed = await self._call(
            "task_claim",
            {"task": claimed_id},
            req_id=24,
            agent_id=self.torqly.id,
        )
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_claim.",
            self._error_text(claimed),
        )
        self.assertEqual(self.peer.id, self.state.board_tasks[claimed_id].assigned_architect_id)

    async def test_frozen_default_architect_claim_rejects_invalid_route_evidence_without_mutation(self):
        self._freeze_default_architect(self.torqly)
        proposal = await self._call(
            "architect_task_propose",
            {"title": "Invalid-route frozen claim root"},
            req_id=30,
        )
        task_id = self._result_payload(proposal)["id"]
        task = self.state.board_tasks[task_id]
        # It is inbound and names the task, but lacks the durable product-peer
        # marker required by the claim handler.  The class gate must not hide
        # this typed handler denial as an unknown public tool.
        self.db.save_agent_peer_message({
            "id": "invalid-route-evidence",
            "thread_id": "invalid-route-thread",
            "group_name": "g",
            "sender_id": self.architect.id,
            "sender_kind": "architect",
            "recipient_id": self.torqly.id,
            "recipient_kind": "architect",
            "message": "This is not a product-peer route.",
            "created_at": 1.0,
            "context_task_ids": [task_id],
            "context_summary": "Invalid route fixture.",
            "context_snapshot": {},
        })
        before_assignment = task.assigned_architect_id
        before_evidence = dict(task.completion_evidence)
        before_messages = list(task.messages)

        denied = await self._call(
            "task_claim",
            {"task": task_id, "source": "invalid-route-evidence"},
            req_id=31,
            agent_id=self.torqly.id,
        )

        text = self._error_text(denied)
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_claim.",
            text,
        )
        self.assertEqual(before_assignment, task.assigned_architect_id)
        self.assertEqual(before_evidence, task.completion_evidence)
        self.assertEqual(before_messages, task.messages)

    async def test_public_task_mark_covered_routes_to_existing_handler(self):
        self._freeze_default_architect(self.torqly)
        covered = self.state.board_add_task(
            "Canonical covered root",
            "g",
            created_by_architect_id=self.torqly.id,
        )
        covering = self.state.board_add_task(
            "Canonical covering task",
            "g",
            created_by_architect_id=self.torqly.id,
        )

        response = await self._call(
            "task_mark_covered",
            {
                "task": covered.id,
                "covering_task": covering.id,
                "notes": "Canonical public handler registration coverage.",
                "move_to_done": True,
            },
            req_id=40,
            agent_id=self.torqly.id,
        )
        payload = self._result_payload(response)
        self.assertEqual("task_marked_covered", payload["type"])
        self.assertEqual("Done", self.state.board_tasks[covered.id].lane)
        evidence = self.state.board_tasks[covered.id].completion_evidence
        self.assertEqual(covering.id, evidence["covered_by"]["task_id"])
        self.assertEqual("covered_by", self.state.board_tasks[covered.id].messages[-1]["action"])

        user_root = self.state.board_add_task(
            "Canonical user-owned covered root",
            "g",
        )
        peer_covering = self.state.board_add_task(
            "Peer-owned covering task",
            "g",
            created_by_architect_id=self.peer.id,
        )
        before_lane = user_root.lane
        before_evidence = dict(user_root.completion_evidence)
        before_messages = list(user_root.messages)
        denied_peer_covering = await self._call(
            "task_mark_covered",
            {
                "task": user_root.id,
                "covering_task": peer_covering.id,
                "notes": "Must remain blocked by frozen self scope.",
            },
            req_id=401,
            agent_id=self.torqly.id,
        )
        # The public canonical name is projected to this caller, so its
        # target-scope refusal must be truthful rather than non-disclosing.
        denied_text = self._error_text(denied_peer_covering)
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_mark_covered.",
            denied_text,
        )
        self.assertNotIn("Unknown tool", denied_text)
        self.assertEqual(before_lane, user_root.lane)
        self.assertEqual(before_evidence, user_root.completion_evidence)
        self.assertEqual(before_messages, user_root.messages)

        denied_legacy_alias = await self._call(
            "architect_task_mark_covered",
            {
                "task": user_root.id,
                "covering_task_id": peer_covering.id,
                "notes": "Legacy alias must retain the same frozen gate.",
            },
            req_id=402,
            agent_id=self.torqly.id,
        )
        self.assertIn("Unknown tool", self._error_text(denied_legacy_alias))
        self.assertEqual(before_lane, user_root.lane)
        self.assertEqual(before_evidence, user_root.completion_evidence)
        self.assertEqual(before_messages, user_root.messages)

        other_owned = self.state.board_add_task(
            "Other Architect task",
            "g",
            created_by_architect_id=self.peer.id,
        )
        before_evidence = dict(other_owned.completion_evidence)
        denied = await self._call(
            "task_mark_covered",
            {
                "task": other_owned.id,
                "covering_task": covering.id,
                "notes": "Must remain denied.",
            },
            req_id=41,
            agent_id=self.torqly.id,
        )
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_mark_covered.",
            self._error_text(denied),
        )
        self.assertEqual(before_evidence, other_owned.completion_evidence)
        self.assertEqual("Backlog", other_owned.lane)

    async def test_frozen_default_architect_marks_routed_proposal_root_covered(self):
        self._freeze_default_architect(self.torqly)
        root = self.state.board_add_task(
            "Frozen canonical covered proposal root",
            "g",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        covering = self.state.board_add_task(
            "Frozen canonical covering task",
            "g",
            labels=[f"covers:{root.id}"],
            created_by_architect_id=self.torqly.id,
        )
        self.db.save_agent_peer_message({
            "id": "frozen-cover-route",
            "thread_id": "frozen-cover-thread",
            "group_name": "g",
            "sender_id": self.architect.id,
            "sender_kind": "architect",
            "recipient_id": self.torqly.id,
            "recipient_kind": "architect",
            "message": "Please cover this routed product proposal root.",
            "created_at": 1.0,
            "context_task_ids": [root.id],
            "context_summary": "Canonical frozen coverage route.",
            "context_snapshot": {
                "proposal_peer": {"marker": "torque.proposal_peer.v1"},
            },
        })

        response = await self._call(
            "task_mark_covered",
            {
                "task": root.id,
                "covering_task": covering.id,
                "notes": "Covered through the canonical frozen route.",
            },
            req_id=50,
            agent_id=self.torqly.id,
        )

        payload = self._result_payload(response)
        self.assertEqual("task_marked_covered", payload["type"])
        evidence = root.completion_evidence["covered_by"]
        self.assertEqual(covering.id, evidence["task_id"])
        self.assertEqual(
            "routed_product_proposal_root",
            evidence["authorization"]["scope"],
        )
        self.assertEqual(
            "frozen-cover-route",
            evidence["authorization"]["route_message_id"],
        )

    async def test_pm_engineer_and_worker_do_not_get_architect_pickup_surface(self):
        for agent_id in (self.architect.id, self.engineer.id, self.worker.id):
            response = await self._call(
                "architect_task_pickup",
                {"task": "TORQUE:1130"},
                req_id=30,
                agent_id=agent_id,
            )
            self.assertRegex(
                self._error_text(response),
                "Unknown tool|architect tools are only available",
            )

    async def test_task_proposal_is_queued_unassigned_and_rejects_dispatch_fields(self):
        response = await self._call(
            "architect_task_propose",
            {
                "title": "Draft onboarding problem statement",
                "description": "Product proposal only.",
                "labels": ["discovery"],
                "suggested_action": "feature/implement",
                "suggested_specialization": "prompts-config",
            },
        )
        payload = self._result_payload(response)
        task_id = payload["id"]
        task = self.state.board_tasks[task_id]

        self.assertEqual("queued", task.dispatch_state)
        self.assertEqual("", task.assigned_engineer_id)
        self.assertEqual("", task.assigned_architect_id)
        self.assertEqual("", task.agent_id)
        self.assertEqual("", task.action_name)
        self.assertEqual({}, task.action_vars)
        self.assertEqual("", task.scheduled_at)
        self.assertEqual(self.architect.id, task.created_by_architect_id)
        self.assertEqual("feature/implement", task.suggested_action)
        self.assertEqual("prompts-config", task.suggested_specialization)
        self.assertIn("product-proposal", task.labels)
        self.assertIn("proposal-only", task.labels)
        self.assertIn("normal queued Board task", payload["caveat"])
        self.assertEqual(
            [{"cmd": "list_actions", "group": "g"}],
            self.calls,
        )

        before = set(self.state.board_tasks)
        rejected = await self._call(
            "architect_task_propose",
            {"title": "Unsafe", "assigned_engineer_id": self.engineer.id},
            req_id=2,
        )
        self.assertIn("assigned_engineer_id", self._error_text(rejected))
        self.assertEqual(before, set(self.state.board_tasks))

        rejected_architect = await self._call(
            "architect_task_propose",
            {"title": "Unsafe architect", "assigned_architect_id": self.architect.id},
            req_id=3,
        )
        self.assertIn("assigned_architect_id", self._error_text(rejected_architect))
        self.assertEqual(before, set(self.state.board_tasks))

    async def test_product_decisions_are_proposed_only_owned_and_no_engineer_links(self):
        task_resp = await self._call("architect_task_propose", {"title": "Proposal task"})
        task_id = self._result_payload(task_resp)["id"]

        create = await self._call(
            "architect_decision_propose",
            {
                "title": "Propose prioritization model",
                "rationale": "Need PM review before acceptance.",
                "linked_task_ids": [task_id],
            },
            req_id=2,
        )
        decision = self._result_payload(create)["decision"]
        self.assertEqual("proposed", decision["status"])
        self.assertEqual([task_id], decision["linked_task_ids"])
        self.assertEqual([], decision["linked_engineer_ids"])
        self.assertEqual(
            "torque.decision_proposal.v1",
            decision["metadata"]["product_proposal"]["marker"],
        )

        accepted = await self._call(
            "architect_decision_propose",
            {"title": "Bad", "rationale": "No", "status": "accepted"},
            req_id=3,
        )
        self.assertIn("must remain proposed", self._error_text(accepted))

        update_bad = await self._call(
            "architect_decision_proposal_update",
            {"id": decision["id"], "status": "accepted"},
            req_id=4,
        )
        self.assertIn("must remain proposed", self._error_text(update_bad))

        link_bad = await self._call(
            "architect_decision_proposal_link",
            {"id": decision["id"], "engineer_id": self.engineer.id, "task_id": task_id},
            req_id=5,
        )
        self.assertIn("cannot link engineers", self._error_text(link_bad))

        other = self.state.save_decision({
            "id": "decision-other",
            "architect_id": self.peer.id,
            "title": "Other",
            "rationale": "Other architect",
            "status": "proposed",
            "metadata": {"product_proposal": {"marker": "torque.decision_proposal.v1"}},
        })
        self.assertIsNotNone(other)
        update_other = await self._call(
            "architect_decision_proposal_update",
            {"id": "decision-other", "title": "steal"},
            req_id=6,
        )
        self.assertIn("Unknown tool", self._error_text(update_other))

    async def test_proposal_area_show_reads_all_same_group_task_and_decision_links(self):
        hidden_task = self.state.board_add_task(
            "Hidden task",
            "g",
            lane="Backlog",
            labels=["internal"],
        )
        product_task = self.state.board_add_task(
            "Product task",
            "g",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
        )
        raw_decision = self.state.save_decision({
            "id": "decision-raw",
            "architect_id": self.architect.id,
            "title": "Accepted secret",
            "rationale": "Not product-scoped",
            "status": "accepted",
        })
        self.assertIsNotNone(raw_decision)
        product_decision_resp = await self._call(
            "architect_decision_propose",
            {
                "title": "Visible product decision",
                "rationale": "Proposed-only product decision.",
                "linked_task_ids": [product_task.id],
            },
            req_id=2,
        )
        product_decision = self._result_payload(product_decision_resp)["decision"]
        area = self.db.create_area({
            "group": "g",
            "title": "PM scoped area",
            "created_by_kind": "user",
            "owner_kind": "user",
        })
        self.db.save_area_link(area["id"], "task", hidden_task.id)
        self.db.save_area_link(area["id"], "task", product_task.id)
        self.db.save_area_link(area["id"], "decision", "decision-raw")
        self.db.save_area_link(area["id"], "decision", product_decision["id"])

        response = await self._call(
            "architect_proposal_area_show",
            {"area": area["id"]},
            req_id=3,
        )
        response_text = response.payload["result"]["content"][0]["text"]
        payload = self._result_payload(response)

        self.assertIn(hidden_task.id, response_text)
        self.assertIn("Hidden task", response_text)
        self.assertIn("decision-raw", response_text)
        self.assertIn("Accepted secret", response_text)
        self.assertEqual(payload["links"]["tasks"], [hidden_task.id, product_task.id])
        self.assertEqual(payload["hidden_link_counts"]["tasks"], 0)
        self.assertEqual(payload["linked_tasks"]["count"], 2)
        self.assertEqual(payload["linked_tasks"]["hidden_count"], 0)
        self.assertEqual(payload["links"]["decisions"], ["decision-raw", product_decision["id"]])
        self.assertEqual(payload["hidden_link_counts"]["decisions"], 0)
        self.assertEqual(payload["linked_decisions"]["count"], 2)
        self.assertEqual(payload["linked_decisions"]["hidden_count"], 0)
        self.assertEqual(payload["linked_decisions"]["ids"], ["decision-raw", product_decision["id"]])
        self.assertEqual(
            [item["title"] for item in payload["linked_decisions"]["items"]],
            ["Accepted secret", "Visible product decision"],
        )

        list_response = await self._call(
            "architect_proposal_area_list",
            {"include_links": True},
            req_id=4,
        )
        list_text = list_response.payload["result"]["content"][0]["text"]
        list_payload = self._result_payload(list_response)
        listed_area = next(item for item in list_payload["areas"] if item["id"] == area["id"])
        self.assertIn(hidden_task.id, list_text)
        self.assertIn("Hidden task", list_text)
        self.assertIn("decision-raw", list_text)
        self.assertIn("Accepted secret", list_text)
        self.assertEqual(listed_area["links"]["tasks"], [hidden_task.id, product_task.id])
        self.assertEqual(listed_area["hidden_link_counts"]["tasks"], 0)
        self.assertEqual(listed_area["links"]["decisions"], ["decision-raw", product_decision["id"]])
        self.assertEqual(listed_area["hidden_link_counts"]["decisions"], 0)

    async def test_proposal_initiative_show_reads_all_same_group_task_and_decision_links(self):
        hidden_task = self.state.board_add_task(
            "Hidden initiative task",
            "g",
            lane="Backlog",
            labels=["internal"],
        )
        product_task = self.state.board_add_task(
            "Product initiative task",
            "g",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
        )
        raw_decision = self.state.save_decision({
            "id": "decision-raw-initiative",
            "architect_id": self.architect.id,
            "title": "Accepted initiative secret",
            "rationale": "Not product-scoped",
            "status": "accepted",
        })
        self.assertIsNotNone(raw_decision)
        product_decision_resp = await self._call(
            "architect_decision_propose",
            {
                "title": "Visible initiative product decision",
                "rationale": "Proposed-only product decision.",
                "linked_task_ids": [product_task.id],
            },
            req_id=2,
        )
        product_decision = self._result_payload(product_decision_resp)["decision"]
        initiative = self.db.create_initiative({
            "group": "g",
            "title": "PM scoped initiative",
            "created_by_kind": "user",
            "owner_kind": "user",
        })
        self.db.save_initiative_link(initiative["id"], "task", hidden_task.id)
        self.db.save_initiative_link(initiative["id"], "task", product_task.id)
        self.db.save_initiative_link(initiative["id"], "decision", "decision-raw-initiative")
        self.db.save_initiative_link(initiative["id"], "decision", product_decision["id"])

        response = await self._call(
            "architect_proposal_initiative_show",
            {"initiative": initiative["id"]},
            req_id=3,
        )
        response_text = response.payload["result"]["content"][0]["text"]
        payload = self._result_payload(response)

        self.assertIn(hidden_task.id, response_text)
        self.assertIn("Hidden initiative task", response_text)
        self.assertIn("decision-raw-initiative", response_text)
        self.assertEqual(payload["links"]["tasks"], [hidden_task.id, product_task.id])
        self.assertEqual(payload["linked_tasks"]["count"], 2)
        self.assertEqual(payload["linked_tasks"]["hidden_count"], 0)
        self.assertEqual(payload["links"]["decisions"], ["decision-raw-initiative", product_decision["id"]])
        self.assertEqual(payload["linked_decisions"]["count"], 2)
        self.assertEqual(payload["linked_decisions"]["hidden_count"], 0)
        self.assertEqual(payload["linked_decisions"]["items"], ["decision-raw-initiative", product_decision["id"]])

        list_response = await self._call(
            "architect_proposal_initiative_list",
            {"include_links": True},
            req_id=4,
        )
        list_text = list_response.payload["result"]["content"][0]["text"]
        list_payload = self._result_payload(list_response)
        listed_initiative = next(
            item for item in list_payload["initiatives"]
            if item["id"] == initiative["id"]
        )
        self.assertIn(hidden_task.id, list_text)
        self.assertIn("Hidden initiative task", list_text)
        self.assertIn("decision-raw-initiative", list_text)
        self.assertEqual(
            listed_initiative["links"]["tasks"],
            [hidden_task.id, product_task.id],
        )
        self.assertEqual(listed_initiative["linked_tasks"]["hidden_count"], 0)
        self.assertEqual(listed_initiative["links"]["decisions"], ["decision-raw-initiative", product_decision["id"]])
        self.assertEqual(listed_initiative["linked_decisions"]["hidden_count"], 0)

    async def test_proposal_peer_list_and_message_include_same_group_full_architect_with_anchors(self):
        product_task = self.state.board_add_task(
            "TORQUE:909 Product Manager permanence",
            "g",
            lane="Backlog",
            id="TORQUE:909",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        self.assertIsNotNone(product_task)
        decision = self.state.save_decision({
            "id": "decision-54e82d9a220f",
            "architect_id": self.architect.id,
            "title": "Product Manager permanence proposal",
            "rationale": "Proposed product scope for PM dogfood state.",
            "status": "proposed",
            "linked_task_ids": ["TORQUE:909"],
            "metadata": {
                "product_proposal": {
                    "marker": "torque.decision_proposal.v1",
                    "owner_architect_id": self.architect.id,
                    "proposed_only": True,
                },
            },
        })
        self.assertIsNotNone(decision)

        peer_list = await self._call("architect_proposal_peer_list", {}, req_id=2)
        peers = self._result_payload(peer_list)["architects"]
        peer_ids = {item["id"] for item in peers}

        self.assertIn(self.peer.id, peer_ids)
        self.assertIn("a5a7fc9e", peer_ids)
        torqly = next(item for item in peers if item["id"] == "a5a7fc9e")
        self.assertEqual("product-peer-authority", torqly["proposal_peer_scope"])
        self.assertNotIn(self.cross_group_architect.id, peer_ids)
        self.assertNotIn(self.engineer.id, peer_ids)
        self.assertNotIn(self.worker.id, peer_ids)

        sent = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": "a5a7fc9e",
                "message": "Torqly, please review the product-planning permanence proposal.",
                "context_task_ids": ["TORQUE:909"],
                "context_decision_ids": ["decision-54e82d9a220f"],
                "context_summary": "Product-scoped product-planning permanence/TORQUE:909 handoff.",
            },
            req_id=3,
        )
        payload = self._result_payload(sent)
        self.assertEqual("a5a7fc9e", payload["recipient_architect_id"])
        saved = self.db.load_agent_peer_message(payload["message_id"])
        self.assertEqual(["TORQUE:909"], saved["context_task_ids"])
        self.assertEqual(["decision-54e82d9a220f"], saved["context_decision_ids"])
        self.assertEqual(
            "torque.proposal_peer.v1",
            saved["context_snapshot"]["proposal_peer"]["marker"],
        )
        self.assertEqual(
            "torque.proposal_context.v1",
            saved["context_snapshot"]["proposal_context"]["marker"],
        )
        self.assertEqual(1, len(self.calls))
        self.assertEqual("inject_mcp_message", self.calls[0]["cmd"])
        self.assertEqual("a5a7fc9e", self.calls[0]["agent_id"])

        reply = await self._call(
            "architect_proposal_peer_reply",
            {
                "message_id": payload["message_id"],
                "message": "Adding a product-scoped follow-up for Torqly.",
                "context_task_ids": ["TORQUE:909"],
                "context_decision_ids": ["decision-54e82d9a220f"],
            },
            req_id=4,
        )
        reply_payload = self._result_payload(reply)
        reply_row = self.db.load_agent_peer_message(reply_payload["message_id"])
        self.assertEqual(payload["thread_id"], reply_payload["thread_id"])
        self.assertEqual(payload["thread_id"], reply_row["thread_id"])
        self.assertEqual(
            "torque.proposal_peer.v1",
            reply_row["context_snapshot"]["proposal_peer"]["marker"],
        )
        self.assertEqual(["TORQUE:909"], reply_row["context_task_ids"])
        self.assertEqual(["decision-54e82d9a220f"], reply_row["context_decision_ids"])
        self.assertEqual(2, len(self.calls))
        self.assertEqual("a5a7fc9e", self.calls[1]["agent_id"])

    async def test_proposal_peer_message_denies_cross_group_non_architect_and_mixed_context_before_side_effects(self):
        product_task = self.state.board_add_task(
            "PM anchor",
            "g",
            lane="Backlog",
            labels=["product-proposal", "proposal-only"],
            created_by_architect_id=self.architect.id,
        )
        self.assertIsNotNone(product_task)
        attempts = [
            (
                {
                    "architect_id": self.cross_group_architect.id,
                    "message": "cross group",
                    "context_task_ids": [product_task.id],
                },
                "Unknown tool",
            ),
            (
                {
                    "architect_id": self.engineer.id,
                    "message": "engineer",
                    "context_task_ids": [product_task.id],
                },
                "Architect not found",
            ),
            (
                {
                    "architect_id": self.worker.id,
                    "message": "worker",
                    "context_task_ids": [product_task.id],
                },
                "Architect not found",
            ),
            (
                {
                    "architect_id": self.torqly.id,
                    "message": "mixed context",
                    "context_task_ids": [product_task.id],
                    "context_engineer_ids": [self.engineer.id],
                },
                "context_engineer_ids",
            ),
        ]

        for index, (args, expected) in enumerate(attempts, start=30):
            before_rows = self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)
            before_calls = list(self.calls)
            response = await self._call(
                "architect_proposal_peer_message",
                args,
                req_id=index,
            )
            self.assertIn(expected, self._error_text(response))
            self.assertEqual(
                [row["id"] for row in before_rows],
                [row["id"] for row in self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)],
            )
            self.assertEqual(before_calls, self.calls)

    async def test_proposal_peer_threads_include_eligible_unmarked_rows_and_keep_marker_for_routes(self):
        task_resp = await self._call("architect_task_propose", {"title": "Anchor task"})
        task_id = self._result_payload(task_resp)["id"]

        # Bare same-group Architect sends remain valid without an explicit
        # product-scope anchor, including acknowledgement requests.
        unanchored = await self._call(
            "architect_proposal_peer_message",
            {"architect_id": self.torqly.id, "message": "unanchored product peer"},
            req_id=20,
        )
        unanchored_payload = self._result_payload(unanchored)
        unanchored_row = self.db.load_agent_peer_message(
            unanchored_payload["message_id"]
        )
        self.assertEqual([], unanchored_row["context_task_ids"])
        self.assertEqual("delivered", unanchored_row["delivery_state"])
        self.assertEqual(
            "torque.proposal_peer.v1",
            unanchored_row["context_snapshot"]["proposal_peer"]["marker"],
        )

        no_anchor = await self._call(
            "architect_proposal_peer_message",
            {"architect_id": self.peer.id, "message": "ack?", "ack_required": True},
            req_id=2,
        )
        no_anchor_payload = self._result_payload(no_anchor)
        no_anchor_row = self.db.load_agent_peer_message(
            no_anchor_payload["message_id"]
        )
        self.assertTrue(no_anchor_row["ack_required"])
        self.assertEqual([], no_anchor_row["context_task_ids"])
        self.assertEqual("delivered", no_anchor_row["delivery_state"])

        unreadable_task = self.state.board_add_task(
            "Other-group product task", "other", lane="Backlog", id="TORQUE:unreadable"
        )
        before_rows = self.db.load_agent_peer_messages_for_agent(
            self.architect.id, limit=20
        )
        before_calls = list(self.calls)
        unreadable = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.peer.id,
                "message": "This attachment is outside my proposal scope.",
                "context_task_ids": [unreadable_task.id],
            },
            req_id=21,
        )
        self.assertIn(
            "not readable in product-proposal scope",
            self._error_text(unreadable),
        )
        self.assertEqual(
            [row["id"] for row in before_rows],
            [row["id"] for row in self.db.load_agent_peer_messages_for_agent(
                self.architect.id, limit=20
            )],
        )
        self.assertEqual(before_calls, self.calls)

        sent = await self._call(
            "architect_proposal_peer_message",
            {
                "architect_id": self.peer.id,
                "message": "Please review this product proposal.",
                "ack_required": True,
                "context_task_ids": [task_id],
            },
            req_id=3,
        )
        sent_payload = self._result_payload(sent)
        product_message_id = sent_payload["message_id"]
        saved = self.db.load_agent_peer_message(product_message_id)
        self.assertEqual("torque.proposal_peer.v1", saved["context_snapshot"]["proposal_peer"]["marker"])
        self.assertTrue(saved["ack_required"])

        # A full Architect sends over the ordinary peer path, which deliberately
        # has no product marker.  It remains an eligible same-group peer row.
        ordinary = await self._call(
            "architect_peer_message",
            {
                "architect_id": self.architect.id,
                "message": "Normal peer-path message; please reply in this thread.",
            },
            req_id=4,
            agent_id=self.full_peer.id,
        )
        ordinary_payload = self._result_payload(ordinary)
        ordinary_row = self.db.load_agent_peer_message(ordinary_payload["message_id"])
        self.assertFalse(
            ordinary_row.get("context_snapshot", {}).get("proposal_peer"),
        )

        # Non-Architect rows remain hidden even if they carry the marker.
        self.db.save_agent_peer_message({
            "id": "msg-engineer",
            "thread_id": "thread-engineer",
            "group_name": "g",
            "sender_id": self.engineer.id,
            "sender_kind": "engineer",
            "recipient_id": self.architect.id,
            "recipient_kind": "architect",
            "message": "engineer hidden",
            "created_at": 3,
            "context_snapshot": {"proposal_peer": {"marker": "torque.proposal_peer.v1"}},
        })
        # A same-group Architect thread that does not include the caller also
        # remains unaddressable, whether or not it is marked.
        self.db.save_agent_peer_message({
            "id": "msg-not-a-party",
            "thread_id": "thread-not-a-party",
            "group_name": "g",
            "sender_id": self.full_peer.id,
            "sender_kind": "architect",
            "recipient_id": self.peer.id,
            "recipient_kind": "architect",
            "message": "not for the caller",
            "created_at": 4,
        })

        inbox = await self._call(
            "architect_proposal_peer_inbox", {"detail": True}, req_id=5,
        )
        threads = self._result_payload(inbox)["threads"]
        thread_by_id = {thread["thread_id"]: thread for thread in threads}
        self.assertEqual(
            {
                unanchored_payload["thread_id"],
                no_anchor_payload["thread_id"],
                sent_payload["thread_id"],
                ordinary_payload["thread_id"],
            },
            set(thread_by_id),
        )
        self.assertEqual(
            [ordinary_payload["message_id"]],
            [entry["id"] for entry in thread_by_id[ordinary_payload["thread_id"]]["messages"]],
        )

        # The marker remains a hard discriminator for product-route evidence;
        # visibility uses relationship eligibility, not this provenance marker.
        proposals_mod = importlib.import_module("torque.mcp_scoped.proposals")
        self.assertTrue(proposals_mod._row_has_proposal_peer_marker(saved))
        self.assertFalse(proposals_mod._row_has_proposal_peer_marker(ordinary_row))
        self.db.save_agent_peer_message({
            "id": "msg-unmarked-route",
            "thread_id": "thread-unmarked-route",
            "group_name": "g",
            "sender_id": self.architect.id,
            "sender_kind": "architect",
            "recipient_id": self.full_peer.id,
            "recipient_kind": "architect",
            "message": "Unmarked route evidence must not authorize coverage.",
            "created_at": 5,
            "context_task_ids": [task_id],
        })
        self.assertEqual(
            {},
            proposals_mod._proposal_peer_route_message_for_task(
                self.state,
                self.full_peer.id,
                self.state.board_tasks[task_id],
            ),
        )

        # Explicit product context keeps the proposal-only anchor validation,
        # while the unmarked row is now addressable and replyable in place.
        ordinary_reply = await self._call(
            "architect_proposal_peer_reply",
            {
                "message_id": ordinary_payload["message_id"],
                "message": "Replying in the original full-Architect thread.",
                "context_task_ids": [task_id],
            },
            req_id=6,
        )
        ordinary_reply_payload = self._result_payload(ordinary_reply)
        ordinary_reply_row = self.db.load_agent_peer_message(
            ordinary_reply_payload["message_id"]
        )
        self.assertEqual(ordinary_payload["thread_id"], ordinary_reply_payload["thread_id"])
        self.assertEqual(
            "torque.proposal_peer.v1",
            ordinary_reply_row["context_snapshot"]["proposal_peer"]["marker"],
        )

        # Inbox and reply use the same eligibility predicate: it now returns
        # the two-way conversation and never leaks the non-party thread.
        inbox_after_reply = await self._call(
            "architect_proposal_peer_inbox", {"detail": True}, req_id=7
        )
        threads_after_reply = {
            thread["thread_id"]: thread
            for thread in self._result_payload(inbox_after_reply)["threads"]
        }
        self.assertEqual(
            [ordinary_payload["message_id"], ordinary_reply_payload["message_id"]],
            [entry["id"] for entry in threads_after_reply[ordinary_payload["thread_id"]]["messages"]],
        )
        self.assertNotIn("thread-not-a-party", threads_after_reply)

        denied_non_party = await self._call(
            "architect_proposal_peer_reply",
            {"message_id": "msg-not-a-party", "message": "must not send"},
            req_id=8,
        )
        self.assertIn("product-peer scope", self._error_text(denied_non_party))

        before_rows = self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)
        before_calls = list(self.calls)
        mixed_context_reply = await self._call(
            "architect_proposal_peer_reply",
            {
                "message_id": product_message_id,
                "message": "reply with invalid engineer context",
                "context_engineer_ids": [self.engineer.id],
            },
            req_id=45,
        )
        self.assertIn("context_engineer_ids", self._error_text(mixed_context_reply))
        self.assertEqual(
            [row["id"] for row in before_rows],
            [row["id"] for row in self.db.load_agent_peer_messages_for_agent(self.architect.id, limit=20)],
        )
        self.assertEqual(before_calls, self.calls)

        reply = await self._call(
            "architect_proposal_peer_reply",
            {"message_id": product_message_id, "message": "Acknowledged.", "ack_required": True},
            req_id=9,
        )
        reply_payload = self._result_payload(reply)
        reply_row = self.db.load_agent_peer_message(reply_payload["message_id"])
        self.assertEqual(sent_payload["thread_id"], reply_row["thread_id"])
        self.assertEqual("torque.proposal_peer.v1", reply_row["context_snapshot"]["proposal_peer"]["marker"])

    async def test_user_message_and_journal_wrappers_validate_product_scope(self):
        invalid_user = await self._call(
            "architect_proposal_message_user",
            {"message": "Hidden context", "context_task_ids": ["TORQUE:404"]},
        )
        self.assertIn("Task not found", self._error_text(invalid_user))
        self.assertEqual([], self.db.load_direct_messages_for_agent(self.architect.id, limit=20))

        bad_journal = await self._call(
            "architect_proposal_journal",
            {"type": "decision", "entry": "No decision journal rows for PM."},
            req_id=2,
        )
        self.assertIn("observation, checkpoint, plan", self._error_text(bad_journal))

        good_journal = await self._call(
            "architect_proposal_journal",
            {"type": "observation", "entry": "Scratch product-planning recovery note."},
            req_id=3,
        )
        self._result_payload(good_journal)
        read = await self._call("architect_proposal_journal_read", {}, req_id=4)
        entries = self._result_payload(read)["entries"]
        self.assertIn("observation", [entry["type"] for entry in entries])
        self.assertTrue(any("Scratch product-planning recovery note" in entry.get("entry", "") for entry in entries))

    async def test_frozen_engineer_can_route_owned_worker_ask_to_typed_resolution(self):
        """Worker asks are self-owned Engineer tasks, not a new broad grant."""
        self.engineer.effective_agent_class_snapshot = {
            "effective_authority": {
                "schema_version": 1,
                "base_kind": "engineer",
                "acl_mode": "allow",
                "capabilities": {"task.update": "self"},
            },
        }
        self.worker.owner_engineer_id = self.engineer.id
        parent = self.state.board_add_task(
            "Implement release", "g", lane="In Progress", id="ask-parent",
            agent_id=self.worker.id, assigned_engineer_id=self.engineer.id,
        )
        ask = self.state.board_add_task(
            "Which release gate applies?", "g", lane="Backlog", id="ask-row",
            labels=["torque:human", "torque:derived", "torque:non-user-ask"],
            parent_task_id=parent.id, reply_agent_id=self.worker.id,
            assigned_engineer_id=self.engineer.id,
        )

        tools = await self._list_tools(agent_id=self.engineer.id)
        self.assertIn("agent_ask_answer", tools)
        response = await self._call(
            "agent_ask_answer",
            {"task": ask.id, "answer": "Use the review gate."},
            agent_id=self.engineer.id,
            req_id=91,
        )
        self._result_payload(response)
        resolve_calls = [
            call for call in self.calls if call.get("cmd") == "resolve_ask"
        ]
        self.assertEqual(len(resolve_calls), 1)
        self.assertEqual(resolve_calls[0]["id"], ask.id)
        self.assertEqual(resolve_calls[0]["answer"], "Use the review gate.")

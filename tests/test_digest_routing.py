import asyncio
import importlib
import sys
import types
import unittest


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class FakeBridge:
    def __init__(self):
        self.sent = []

    async def send_text(self, session_id, text):
        self.sent.append((session_id, text))
        await asyncio.sleep(0)


class DigestRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.routing_mod = importlib.import_module("torque.digest_routing")
        self.routing_mod = importlib.reload(self.routing_mod)
        self.engineer_mod = importlib.import_module("torque.engineer")
        self.engineer_mod = importlib.reload(self.engineer_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        group = "g"
        state.groups[group] = []
        return state, group

    def _add_agent(self, state, *, agent_id, name, group, kind,
                   owner_engineer_id="", hired_by_architect_id="",
                   created_by_engineer_id="", running=True):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=agent_id,
            group=group,
            cell_type="agent",
            kind=kind,
            owner_engineer_id=owner_engineer_id,
            hired_by_architect_id=hired_by_architect_id,
            created_by_engineer_id=created_by_engineer_id,
            session_id=f"session-{agent_id}" if running else "",
            status="running" if running else "stopped",
            persistent=kind in {"engineer", "architect"},
        )
        state.agents[cell.id] = cell
        state.groups.setdefault(group, []).append(cell.id)
        return cell

    def test_resolve_worker_owner_suppresses_architect_churn_by_default(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [engineer.id])

    def test_architect_opt_in_surfaces_tunable_worker_event(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["task_completed"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [engineer.id, architect.id])

    def test_task_event_routes_to_assigned_engineer_before_worker_owner(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        assigned = self._add_agent(
            state,
            agent_id="eng-assigned",
            name="Assigned Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        owner = self._add_agent(
            state,
            agent_id="eng-owner",
            name="Worker Owner",
            group=group,
            kind="engineer",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=owner.id,
        )
        task = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(task)
        state.update_agent_digest_settings(assigned.id)
        state.update_agent_digest_settings(owner.id)
        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["task_completed"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": worker.id,
                "group": group,
                "kind": "task_completed",
                "task_id": task.id,
            },
        )

        self.assertEqual(recipients, [assigned.id, architect.id])

    def test_child_task_event_routes_via_parent_assigned_engineer(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        assigned = self._add_agent(
            state,
            agent_id="eng-assigned",
            name="Assigned Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
        )
        parent = state.board_add_task(
            "Architect-created task",
            group,
            id="task-1",
            agent_id=worker.id,
            assigned_engineer_id=assigned.id,
        )
        self.assertIsNotNone(parent)
        ask_task = state.board_add_task(
            "Need approval",
            group,
            id="task-1.1",
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
        )
        self.assertIsNotNone(ask_task)
        state.update_agent_digest_settings(assigned.id)
        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["task_completed"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": worker.id,
                "group": group,
                "kind": "ask_created",
                "task_id": ask_task.id,
            },
        )

        self.assertEqual(recipients, [assigned.id, architect.id])

    def test_workflow_breach_is_architect_coarse_event_but_opt_in(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "workflow_breach"},
        )

        self.assertEqual(recipients, [])
        self.assertIn(
            "workflow_breach",
            self.routing_mod.ARCHITECT_COARSE_EVENTS,
        )
        self.assertNotIn(
            "workflow_breach",
            state.get_agent_digest_settings(architect.id).enabled_events,
        )

        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["workflow_breach"],
        )
        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "workflow_breach"},
        )
        self.assertEqual(recipients, [architect.id])
        self.assertIn(
            "workflow_breach",
            state.get_agent_digest_settings(architect.id).enabled_events,
        )

    def test_engineer_queue_empty_routes_only_to_hiring_architect(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Courier",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(architect.id)

        event = {
            "cell_id": engineer.id,
            "agent_name": engineer.name,
            "group": group,
            "kind": "engineer_queue_empty",
        }

        recipients = self.routing_mod.resolve_digest_recipients(state, event)

        self.assertEqual(recipients, [])
        self.assertIn(
            "engineer_queue_empty",
            self.routing_mod.ARCHITECT_COARSE_EVENTS,
        )
        self.assertNotIn(
            "engineer_queue_empty",
            state.get_agent_digest_settings(architect.id).enabled_events,
        )
        self.assertNotIn(
            "engineer_queue_empty",
            self.state_mod.ENGINEER_MANDATORY_EVENTS,
        )

        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["engineer_queue_empty"],
        )
        recipients = self.routing_mod.resolve_digest_recipients(state, event)
        self.assertEqual(recipients, [architect.id])

    def test_candidate_worker_recipients_exclude_worker_self(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )

        recipients = self.routing_mod.candidate_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [engineer.id, architect.id])
        self.assertNotIn(worker.id, recipients)

    def test_resolve_worker_without_owner_returns_no_recipients(self):
        state, group = self._make_state()
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [])

    def test_resolve_engineer_with_and_without_architect(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["task_completed"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )
        self.assertEqual(recipients, [engineer.id, architect.id])

        engineer.hired_by_architect_id = ""
        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )
        self.assertEqual(recipients, [engineer.id])

    def test_architect_events_do_not_generate_recipients(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": architect.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [])

    def test_paused_recipient_is_filtered_out(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        state.update_agent_digest_settings(engineer.id, paused=True)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [])

    def test_event_not_in_enabled_events_is_filtered_out(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        state.update_agent_digest_settings(
            engineer.id,
            enabled_events=["task_derived"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_dispatched"},
        )

        self.assertEqual(recipients, [])

    def test_architect_coarse_filter_blocks_non_coarse_events(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(
            engineer.id,
            enabled_events=["task_dispatched"],
        )
        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["task_dispatched"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_dispatched"},
        )

        self.assertEqual(recipients, [engineer.id])

    def test_architect_does_not_receive_raw_progress_but_engineer_can(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id="arch-1",
        )
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(
            engineer.id,
            enabled_events=["agent_progress"],
        )
        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["agent_progress"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "agent_progress"},
        )

        self.assertEqual(recipients, [engineer.id])

    def test_architect_does_not_receive_events_from_engineer_they_did_not_hire(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        other_architect = self._add_agent(
            state,
            agent_id="arch-2",
            name="Other Architect",
            group=group,
            kind="architect",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=other_architect.id,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(engineer.id)
        state.update_agent_digest_settings(architect.id)
        state.update_agent_digest_settings(
            other_architect.id,
            enabled_events=["task_completed"],
        )

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [engineer.id, other_architect.id])
        self.assertNotIn(architect.id, recipients)

    def test_user_architect_without_hired_engineers_receives_nothing(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": architect.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [])

    def test_mandatory_events_bypass_enabled_events_filter(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        state.update_agent_digest_settings(engineer.id, enabled_events=[])

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": engineer.id, "group": group, "kind": "task_completed"},
        )

        self.assertEqual(recipients, [engineer.id])

    def test_architect_mandatory_floor_bypasses_enabled_events_filter(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(engineer.id, enabled_events=[])
        state.update_agent_digest_settings(
            architect.id,
            # Explicitly exclude floor events; the server floor still wins.
            enabled_events=["task_completed"],
        )

        for event_kind in sorted(self.state_mod.ARCHITECT_MANDATORY_EVENTS):
            recipients = self.routing_mod.resolve_digest_recipients(
                state,
                {"cell_id": worker.id, "group": group, "kind": event_kind},
            )
            self.assertIn(architect.id, recipients, event_kind)

    def test_architect_floor_events_are_not_persisted_as_optional_filters(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )

        state.update_agent_digest_settings(
            architect.id,
            enabled_events=[
                "task_completed",
                "ask_created",
                "agent_error",
            ],
        )

        settings = state.get_agent_digest_settings(architect.id)
        self.assertEqual(settings.enabled_events, ["task_completed"])

    def test_worker_boot_doa_is_mandatory_for_worker_owner_only(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(engineer.id, enabled_events=[])
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {"cell_id": worker.id, "group": group, "kind": "worker_boot_doa"},
        )

        self.assertEqual(recipients, [engineer.id])

    def test_perceived_empty_episode_routes_to_architect_only_when_opted_in(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(engineer.id, enabled_events=[])
        state.update_agent_digest_settings(architect.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": worker.id,
                "group": group,
                "kind": "perceived_empty_episode",
            },
        )

        self.assertEqual(recipients, [engineer.id])

        state.update_agent_digest_settings(
            architect.id,
            enabled_events=["perceived_empty_episode"],
        )
        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": worker.id,
                "group": group,
                "kind": "perceived_empty_episode",
            },
        )
        self.assertEqual(recipients, [engineer.id, architect.id])

    def test_blank_cell_task_event_routes_to_assigned_engineer(self):
        state, group = self._make_state()
        legacy = self._add_agent(
            state,
            agent_id="eng-a",
            name="Legacy Engineer",
            group=group,
            kind="engineer",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-b",
            name="Owner Engineer",
            group=group,
            kind="engineer",
        )
        state.group_settings[group] = self.state_mod.GroupSettings(
            engineer_agent_id=legacy.id
        )
        task = state.board_add_task(
            "Verify release",
            group,
            id="task-1",
            assigned_engineer_id=engineer.id,
        )
        self.assertIsNotNone(task)
        state.update_agent_digest_settings(legacy.id)
        state.update_agent_digest_settings(engineer.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": "",
                "group": group,
                "kind": "task_verification_updated",
                "task_id": task.id,
            },
        )

        self.assertEqual(recipients, [engineer.id])

    def test_blank_cell_task_event_routes_via_task_agent_chain(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Owner Engineer",
            group=group,
            kind="engineer",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        task = state.board_add_task(
            "Review implementation",
            group,
            id="task-1",
            agent_id=worker.id,
        )
        self.assertIsNotNone(task)
        state.update_agent_digest_settings(engineer.id)

        recipients = self.routing_mod.resolve_digest_recipients(
            state,
            {
                "cell_id": "",
                "group": group,
                "kind": "task_verification_updated",
                "task_id": task.id,
            },
        )

        self.assertEqual(recipients, [engineer.id])

    async def test_per_recipient_buffer_isolation(self):
        state, group = self._make_state()
        engineer_a = self._add_agent(
            state,
            agent_id="eng-a",
            name="Engineer A",
            group=group,
            kind="engineer",
        )
        engineer_b = self._add_agent(
            state,
            agent_id="eng-b",
            name="Engineer B",
            group=group,
            kind="engineer",
        )
        worker_a = self._add_agent(
            state,
            agent_id="worker-a",
            name="Worker A",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_a.id,
        )
        worker_b = self._add_agent(
            state,
            agent_id="worker-b",
            name="Worker B",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_b.id,
        )
        state.update_agent_digest_settings(engineer_a.id)
        state.update_agent_digest_settings(engineer_b.id)

        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())
        buffer._loop = asyncio.get_running_loop()

        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker_a.id,
                "group": group,
                "kind": "task_dispatched",
                "message": "event A",
            }
        )
        buffer.on_panel_event(
            {
                "id": 2,
                "cell_id": worker_b.id,
                "group": group,
                "kind": "task_dispatched",
                "message": "event B",
            }
        )
        await asyncio.sleep(0)

        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer_a.id]],
            ["event A"],
        )
        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer_b.id]],
            ["event B"],
        )

    async def test_pausing_one_recipient_does_not_block_another_buffer(self):
        state, group = self._make_state()
        engineer_a = self._add_agent(
            state,
            agent_id="eng-a",
            name="Engineer A",
            group=group,
            kind="engineer",
        )
        engineer_b = self._add_agent(
            state,
            agent_id="eng-b",
            name="Engineer B",
            group=group,
            kind="engineer",
        )
        worker_a = self._add_agent(
            state,
            agent_id="worker-a",
            name="Worker A",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_a.id,
        )
        worker_b = self._add_agent(
            state,
            agent_id="worker-b",
            name="Worker B",
            group=group,
            kind="worker",
            owner_engineer_id=engineer_b.id,
        )
        state.update_agent_digest_settings(
            engineer_a.id,
            push_interval=0,
            max_interval=0,
        )
        state.update_agent_digest_settings(
            engineer_b.id,
            push_interval=0,
            max_interval=0,
        )

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()

        state.update_agent_digest_settings(engineer_a.id, paused=True)
        buffer.on_delivery_paused(engineer_a.id)

        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker_a.id,
                "group": group,
                "kind": "task_completed",
                "message": "event A",
            }
        )
        buffer.on_panel_event(
            {
                "id": 2,
                "cell_id": worker_b.id,
                "group": group,
                "kind": "task_completed",
                "message": "event B",
            }
        )
        await asyncio.sleep(0.05)

        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer_a.id]],
            ["event A"],
        )
        self.assertEqual(
            [event["message"] for event in buffer.get_sent_events(engineer_b.id)],
            ["event B"],
        )
        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("event B", bridge.sent[0][1])

    async def test_stopped_recipient_keeps_buffered_events_until_running(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            running=False,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        state.update_agent_digest_settings(
            engineer.id,
            push_interval=0,
            max_interval=0,
        )

        bridge = FakeBridge()
        buffer = self.engineer_mod.EngineerEventBuffer(state, bridge)
        buffer._loop = asyncio.get_running_loop()
        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker.id,
                "group": group,
                "kind": "task_completed",
                "message": "event A",
            }
        )
        await asyncio.sleep(0)

        self.assertEqual(
            [event["message"] for event in buffer._buffers[engineer.id]],
            ["event A"],
        )
        self.assertEqual(bridge.sent, [])

        engineer.session_id = "session-eng-1"
        engineer.status = "running"
        buffer._timer_tick()
        await asyncio.sleep(0.05)

        self.assertEqual(len(bridge.sent), 1)
        self.assertIn("event A", bridge.sent[0][1])

    async def test_worker_event_creates_default_owner_digest_settings(self):
        state, group = self._make_state()
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        self.assertNotIn(engineer.id, state.agent_digest_settings)

        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())
        buffer._loop = asyncio.get_running_loop()
        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker.id,
                "group": group,
                "kind": "task_dispatched",
                "message": "event A",
            }
        )
        await asyncio.sleep(0)

        self.assertIn(engineer.id, state.agent_digest_settings)

    async def test_worker_coarse_event_creates_default_architect_settings(self):
        state, group = self._make_state()
        architect = self._add_agent(
            state,
            agent_id="arch-1",
            name="Architect",
            group=group,
            kind="architect",
        )
        engineer = self._add_agent(
            state,
            agent_id="eng-1",
            name="Engineer",
            group=group,
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_agent(
            state,
            agent_id="worker-1",
            name="Worker",
            group=group,
            kind="worker",
            owner_engineer_id=engineer.id,
        )
        self.assertNotIn(architect.id, state.agent_digest_settings)

        buffer = self.engineer_mod.EngineerEventBuffer(state, FakeBridge())
        buffer._loop = asyncio.get_running_loop()
        buffer.on_panel_event(
            {
                "id": 1,
                "cell_id": worker.id,
                "group": group,
                "kind": "task_completed",
                "message": "event A",
            }
        )
        await asyncio.sleep(0)

        settings = state.agent_digest_settings.get(architect.id)
        self.assertIsNotNone(settings)
        self.assertTrue(settings.architect_digest)
        self.assertFalse(settings.paused)
        self.assertEqual(settings.push_interval, 300)
        self.assertFalse(settings.wake_on_digest)

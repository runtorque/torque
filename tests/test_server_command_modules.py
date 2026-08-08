import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


EXPECTED_PLANNING_COMMANDS = {
    "initiative_list",
    "initiative_show",
    "initiative_create",
    "initiative_update",
    "initiative_archive",
    "initiative_link_task",
    "initiative_unlink_task",
    "initiative_link_decision",
    "initiative_unlink_decision",
    "area_list",
    "area_show",
    "area_create",
    "area_update",
    "area_archive",
    "area_link_task",
    "area_unlink_task",
    "area_link_decision",
    "area_unlink_decision",
    "area_link_initiative",
    "area_unlink_initiative",
    "area_link_area",
    "area_unlink_area",
    "area_note_create",
    "area_note_update",
    "area_note_archive",
    "scratchpad_note_list",
    "scratchpad_note_show",
    "scratchpad_note_create",
    "scratchpad_note_update",
    "scratchpad_note_archive",
    "scratchpad_note_delete",
    "idea_brief_list",
    "idea_brief_show",
    "idea_brief_create",
    "idea_brief_update",
    "idea_brief_refine",
    "idea_brief_park",
    "idea_brief_archive",
    "idea_brief_propose",
    "idea_brief_promote",
}
EXPECTED_BEHAVIOR_OVERLAY_READ_COMMANDS = {
    "behavior_overlay_read",
    "behavior_overlay_versions",
    "behavior_overlay_proposals",
    "behavior_overlay_diff",
}
EXPECTED_BEHAVIOR_OVERLAY_MUTATION_COMMANDS = {
    "behavior_overlay_propose",
    "behavior_overlay_architect_approve",
    "behavior_overlay_architect_reject",
    "behavior_overlay_user_approve",
    "behavior_overlay_user_reject",
    "behavior_overlay_user_rollback",
}
EXPECTED_UI_STATE_COMMANDS = {
    "board_add_lane",
    "board_rename_lane",
    "board_remove_lane",
    "board_reorder_lanes",
    "board_set_panel",
    "ui_select_group",
    "ui_select_principal",
    "select_agent",
    "ui_select_agent",
    "ui_set_window_bounds",
    "ui_set_workspace_sidebar_width",
    "ui_set_terminal_direct_messages_height",
    "ui_set_terminal_compose_height",
    "standalone_set_panel_layout",
    "ui_set_detached_panels",
    "ui_set_detached_panel_bounds",
    "first_run_complete",
    "ui_set_engineer_panel_split",
    "ui_set_context_panel_split",
    "ui_set_supervisor_panel_state",
    "events_dismiss",
    "board_set_filters",
    "board_set_selected_lanes",
    "board_set_hidden_wide_lanes",
    "board_set_saved_views",
    "board_set_lane_sorts",
    "board_set_card_density",
}
EXPECTED_SCHEDULE_COMMANDS = {
    "schedule_create",
    "schedule_update",
    "schedule_remove",
    "schedule_enable",
    "schedule_disable",
    "schedule_list",
    "schedule_run",
}
EXPECTED_MEMORY_COMMANDS = {
    "memory_list",
    "memory_read",
    "memory_publish",
    "memory_pin",
    "memory_link",
    "memory_unpin",
}
EXPECTED_CATALOG_COMMANDS = {
    "get_playbook_candidates",
    "extract_playbook_candidates",
    "get_playbooks",
    "get_playbook",
    "generate_playbook_draft",
    "publish_playbook_draft",
    "discard_playbook_draft",
    "list_actions",
    "list_action_catalog",
    "list_specializations",
    "get_specialization",
    "save_specialization",
    "delete_specialization",
    "set_engineer_specializations",
    "get_template",
    "render_template",
    "get_action",
    "render_action",
    "save_action",
    "delete_action",
    "list_roles",
    "list_templates",
    "save_role",
    "save_template",
    "delete_role",
    "delete_template",
}
EXPECTED_WORKTREE_COMMANDS = {
    "worktree_create",
    "worktree_advance_boundary",
    "worktree_adopt",
    "worktree_remove",
    "worktree_list",
    "worktree_prune",
    "worktree_checkpoint",
    "worktree_history",
    "worktree_diff_full",
    "worktree_check_merge",
    "worktree_rebase",
    "worktree_rollback",
    "worktree_diff",
    "worktree_check_conflicts",
    "worktree_create_pr",
    "worktree_merge",
}
EXPECTED_ENGINEER_OPERATION_COMMANDS = {
    "engineer_message",
    "inject_mcp_message",
    "architect_journal_append",
    "engineer_journal_append",
    "engineer_journal_read",
    "engineer_session_map_read",
    "engineer_journal_delete",
    "engineer_update_settings",
    "engineer_ask",
    "engineer_note",
    "engineer_dismiss_note",
    "engineer_reply",
    "engineer_pause",
    "engineer_resume",
    "digest_pause",
    "digest_resume",
    "engineer_flush_now",
}
EXPECTED_AGENT_CLASS_COMMANDS = {
    "agent_class_list",
    "agent_class_validate",
    "agent_class_draft_validate",
    "agent_class_create",
    "agent_class_save",
    "agent_class_update",
    "agent_class_archive",
    "agent_class_disable",
    "agent_class_delete",
    "agent_class_preview",
    "agent_class_assign",
    "agent_class_clear",
    "agent_class_status",
    "agent_class_audit",
}
EXPECTED_ROLE_TEMPLATE_COMMANDS = {
    "list_roles",
    "list_templates",
    "save_role",
    "save_template",
    "delete_role",
    "delete_template",
}
EXPECTED_SETTINGS_READ_COMMANDS = {
    "get_group_settings",
    "get_architect_settings",
    "get_global_settings",
    "get_ai_settings",
}
EXPECTED_SETTINGS_MUTATION_COMMANDS = {
    "update_group_settings",
    "update_architect_settings",
}
EXPECTED_BOARD_ARCHIVE_COMMANDS = {
    "board_archive_task",
    "board_archive_tasks",
    "board_unarchive_task",
}
EXPECTED_BOARD_OPERATION_COMMANDS = {
    "board_sync_preflight",
    "board_sync_list_projects",
    "board_sync_task",
    "board_sync_group",
    "board_pull_preview",
    "board_pull_apply",
    "board_import_preview",
    "board_pull_import_preview",
    "board_add_task",
    "board_archive_task",
    "board_archive_tasks",
    "board_unarchive_task",
    "board_update_task",
    "board_amend_task",
    "board_mark_task_covered",
    "board_pickup_architect_task",
    "architect_proposal_root_backlog_hygiene",
    "board_verify_task",
    "workflow_breach",
    "external_import_task",
    "external_link_task",
    "external_open_task",
    "external_push_task_status",
    "external_post_task_comment",
    "board_remove_task",
    "remove_attachment",
    "task_upload_artifact",
    "board_move_task",
    "board_reorder_task",
}
EXPECTED_AGENT_OPERATION_COMMANDS = {
    "rename_group",
    "add_engineer", "add_architect", "add_worker",
    "agent_class_launch", "create_agent_from_class",
    "architect_engineer_hire", "architect_engineer_set_specializations",
    "pending_hire_approve", "pending_hire_reject", "pending_hire_list",
    "engineer_dismiss", "architect_engineer_dismiss",
    "engineer_rehire", "architect_engineer_rehire",
    "architect_dismiss", "architect_rehire",
    "delete_engineer", "delete_architect",
    "restore_agent", "architect_engineer_restore",
    "purge_agent_now", "recently_deleted_agents", "rename_engineer",
    "architect_decision_create", "architect_decision_update",
    "architect_decision_link", "architect_decision_list",
    "architect_peer_inbox", "architect_peer_list",
    "architect_peer_message", "architect_task_update",
    "add_agent", "add_terminal", "remove_agent", "update_agent",
    "focus_agent", "send_text", "send_user_message",
    "user_agent_message", "user_agent_turn_cancel", "relaunch_agent", "restart_agent",
    "move_group", "move_agent", "reparent_terminal", "reorder_child",
    "clear_agent_context",
} | EXPECTED_BEHAVIOR_OVERLAY_MUTATION_COMMANDS
EXPECTED_DIRECT_COMMANDS = {
    "get_config", "preview_system_prompt", "test_relay_connection",
    "generate_relay_device_link", "generate_daemon_credential", "doctor",
    "help_list", "help_show", "help_search", "help_query",
    "get_metrics_history", "report_frontend_render",
    "get_system_health_metrics", "get_deploy_state", "get_mission_control",
    "supervisor_sessions_list", "supervisor_session_terminate",
    "supervisor_restart", "get_events", "task_detail",
    "get_agent_message_history", "decisions_snapshot",
    "pending_hires_snapshot", "archived_tasks",
    "engineer_journal_snapshot", "architect_journal_read", "mcp_calls",
    "architect_mcp_calls", "engineer_mcp_calls", "architect_task_list",
    "get_cell_events", "get_agent_history", "get_agent_history_detail",
} | EXPECTED_SETTINGS_READ_COMMANDS | EXPECTED_AGENT_CLASS_COMMANDS | \
    EXPECTED_PLANNING_COMMANDS | EXPECTED_BEHAVIOR_OVERLAY_READ_COMMANDS | \
    EXPECTED_CATALOG_COMMANDS
EXPECTED_RUNTIME_SETTINGS_COMMANDS = {
    "add_group", "update_global_settings", "update_ai_settings",
    "ai_index_start", "remove_group",
} | EXPECTED_SETTINGS_MUTATION_COMMANDS
EXPECTED_AGENT_LIFECYCLE_COMMANDS = {
    "remove_agent",
    "restore_agent",
    "architect_engineer_restore",
    "purge_agent_now",
    "recently_deleted_agents",
}


class PlanningCommandModuleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.planning = importlib.import_module("torque.commands.planning")
        cls.server = importlib.import_module("torque.server")

    def test_registry_inventory_is_complete_and_exact(self):
        self.assertEqual(
            EXPECTED_PLANNING_COMMANDS,
            set(self.planning.PLANNING_COMMAND_NAMES),
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_PLANNING_COMMANDS)),
            self.planning._PLANNING_COMMAND_REGISTRY.route_names(),
        )
        self.assertEqual((), self.planning._PLANNING_COMMAND_REGISTRY.route_prefixes())

    def test_server_preserves_planning_compatibility_exports(self):
        self.assertIs(
            self.planning._PLANNING_COMMAND_REGISTRY,
            self.server._PLANNING_COMMAND_REGISTRY,
        )
        self.assertIs(
            self.planning._handle_area_command,
            self.server._handle_area_command,
        )
        self.assertEqual(
            "torque.commands.planning",
            self.server._handle_initiative_command.__module__,
        )

    async def test_registry_does_not_claim_unrelated_commands(self):
        result = await self.planning._PLANNING_COMMAND_REGISTRY.dispatch(
            "board_add_task",
            {"cmd": "board_add_task"},
            object(),
        )
        self.assertFalse(result.handled)


class OperatorNoticeCommandModuleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.operator_notices")

    async def test_list_response_echoes_result_set_identity_for_race_rejection(self):
        class State:
            def list_operator_notices(self, **kwargs):
                self.list_kwargs = kwargs
                return [{"id": "notice-1"}]

            def operator_notice_summary(self):
                return {"open_alerts": 1}

        state = State()
        result = await self.commands._handle_operator_notice_command(
            {
                "cmd": "operator_notices_list",
                "notice_type": "notification",
                "include_archived": False,
                "limit": 20,
                "offset": 40,
            },
            state,
        )

        self.assertEqual(result["notice_type"], "notification")
        self.assertFalse(result["include_archived"])
        self.assertEqual(result["offset"], 40)
        self.assertEqual(state.list_kwargs, {
            "notice_type": "notification",
            "include_archived": False,
            "limit": 21,
            "offset": 40,
        })


class BehaviorOverlayCommandModuleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module(
            "torque.commands.behavior_overlays"
        )
        cls.server = importlib.import_module("torque.server")

    def test_read_and_mutation_registries_are_complete_and_disjoint(self):
        self.assertEqual(
            EXPECTED_BEHAVIOR_OVERLAY_READ_COMMANDS,
            set(self.commands.BEHAVIOR_OVERLAY_READ_COMMAND_NAMES),
        )
        self.assertEqual(
            EXPECTED_BEHAVIOR_OVERLAY_MUTATION_COMMANDS,
            set(self.commands.BEHAVIOR_OVERLAY_MUTATION_COMMAND_NAMES),
        )
        self.assertTrue(
            EXPECTED_BEHAVIOR_OVERLAY_READ_COMMANDS.isdisjoint(
                EXPECTED_BEHAVIOR_OVERLAY_MUTATION_COMMANDS
            )
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_BEHAVIOR_OVERLAY_READ_COMMANDS)),
            self.commands._BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY.route_names(),
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_BEHAVIOR_OVERLAY_MUTATION_COMMANDS)),
            self.commands._BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY.route_names(),
        )

    def test_server_preserves_behavior_overlay_compatibility_exports(self):
        self.assertIs(
            self.commands._handle_behavior_overlay_read_command,
            self.server._handle_behavior_overlay_read_command,
        )
        self.assertIs(
            self.commands._BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY,
            self.server._BEHAVIOR_OVERLAY_MUTATION_COMMAND_REGISTRY,
        )


class UIStateCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.ui_state")
        cls.server = importlib.import_module("torque.server")

    def test_registry_inventory_is_complete_and_exact(self):
        self.assertEqual(
            EXPECTED_UI_STATE_COMMANDS,
            set(self.commands.UI_STATE_COMMAND_NAMES),
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_UI_STATE_COMMANDS)),
            self.commands._UI_STATE_COMMAND_REGISTRY.route_names(),
        )

    def test_server_preserves_ui_state_compatibility_exports(self):
        self.assertIs(
            self.commands._handle_ui_state_command,
            self.server._handle_ui_state_command,
        )


class ScheduleCommandModuleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.schedules")
        cls.server = importlib.import_module("torque.server")

    def test_registry_inventory_is_complete_and_exact(self):
        self.assertEqual(
            EXPECTED_SCHEDULE_COMMANDS,
            set(self.commands.SCHEDULE_COMMAND_NAMES),
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_SCHEDULE_COMMANDS)),
            self.commands._SCHEDULE_COMMAND_REGISTRY.route_names(),
        )

    def test_server_preserves_schedule_compatibility_exports(self):
        self.assertIs(
            self.commands._handle_schedule_command,
            self.server._handle_schedule_command,
        )

    async def test_create_and_list_schedule(self):
        state_module = importlib.import_module("torque.state")
        state = state_module.MatrixState()
        state.groups["Torque"] = []

        async def dispatch_command(_data):
            return None

        def panel_event(*_args, **_kwargs):
            return None

        created = await self.commands._handle_schedule_command(
            {
                "cmd": "schedule_create",
                "name": "Daily",
                "group": "Torque",
                "scheduled_at": "2030-01-01T00:00:00+00:00",
            },
            state,
            dispatch_command=dispatch_command,
            panel_event=panel_event,
        )
        self.assertEqual("ok", created["type"])

        listed = await self.commands._handle_schedule_command(
            {"cmd": "schedule_list"},
            state,
            dispatch_command=dispatch_command,
            panel_event=panel_event,
        )
        self.assertEqual(1, len(listed["schedules"]))


class AgentClassCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.agent_classes")
        cls.server = importlib.import_module("torque.server")

    def test_registry_inventory_is_complete_and_exact(self):
        self.assertEqual(
            EXPECTED_AGENT_CLASS_COMMANDS,
            set(self.commands.AGENT_CLASS_COMMAND_NAMES),
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_AGENT_CLASS_COMMANDS)),
            self.commands._AGENT_CLASS_COMMAND_REGISTRY.route_names(),
        )

    def test_server_preserves_agent_class_compatibility_exports(self):
        self.assertIs(
            self.commands._handle_agent_class_command,
            self.server._handle_agent_class_command,
        )
        self.assertIs(
            self.commands._agent_class_authoring_payload_from_command,
            self.server._agent_class_authoring_payload_from_command,
        )


class RoleTemplateCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.roles")
        cls.server = importlib.import_module("torque.server")

    def test_registry_inventory_is_complete_and_exact(self):
        self.assertEqual(
            EXPECTED_ROLE_TEMPLATE_COMMANDS,
            set(self.commands.ROLE_TEMPLATE_COMMAND_NAMES),
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_ROLE_TEMPLATE_COMMANDS)),
            self.commands._ROLE_TEMPLATE_COMMAND_REGISTRY.route_names(),
        )

    def test_server_preserves_role_template_compatibility_export(self):
        self.assertIs(
            self.commands._handle_role_template_command,
            self.server._handle_role_template_command,
        )


class SettingsCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.settings")
        cls.server = importlib.import_module("torque.server")

    def test_registries_are_complete_and_disjoint(self):
        self.assertEqual(
            EXPECTED_SETTINGS_READ_COMMANDS,
            set(self.commands.SETTINGS_READ_COMMAND_NAMES),
        )
        self.assertEqual(
            EXPECTED_SETTINGS_MUTATION_COMMANDS,
            set(self.commands.SETTINGS_MUTATION_COMMAND_NAMES),
        )
        self.assertTrue(
            EXPECTED_SETTINGS_READ_COMMANDS.isdisjoint(
                EXPECTED_SETTINGS_MUTATION_COMMANDS
            )
        )

    def test_server_preserves_settings_compatibility_exports(self):
        self.assertIs(
            self.commands._handle_settings_read_command,
            self.server._handle_settings_read_command,
        )
        self.assertIs(
            self.commands._handle_settings_mutation_command,
            self.server._handle_settings_mutation_command,
        )


class BoardCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.board")
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_BOARD_ARCHIVE_COMMANDS,
            set(self.commands.BOARD_ARCHIVE_COMMAND_NAMES),
        )

    def test_server_preserves_board_compatibility_exports(self):
        self.assertIs(self.commands._resolve_task_id, self.server._resolve_task_id)
        self.assertIs(
            self.commands._handle_board_archive_command,
            self.server._handle_board_archive_command,
        )


class BoardOperationCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module(
            "torque.commands.board_operations"
        )
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_BOARD_OPERATION_COMMANDS,
            set(self.commands.BOARD_OPERATION_COMMAND_NAMES),
        )

    def test_server_uses_board_operation_domain_handler(self):
        self.assertIs(
            self.commands.handle_board_operation_command,
            self.server.handle_board_operation_command,
        )


class AgentOperationCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module(
            "torque.commands.agent_operations"
        )
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_AGENT_OPERATION_COMMANDS,
            set(self.commands.AGENT_OPERATION_COMMAND_NAMES),
        )

    def test_server_uses_agent_operation_domain_handler(self):
        self.assertIs(
            self.commands.handle_agent_operation_command,
            self.server.handle_agent_operation_command,
        )


class DirectCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.direct")
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_DIRECT_COMMANDS,
            set(self.commands.DIRECT_COMMAND_NAMES),
        )

    def test_server_uses_direct_command_handler(self):
        self.assertIs(
            self.commands.handle_direct_command,
            self.server.handle_direct_command,
        )


class PromptPreviewCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module(
            "torque.commands.prompt_preview"
        )
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            {"preview_prompt"},
            set(self.commands.PROMPT_PREVIEW_COMMAND_NAMES),
        )

    def test_server_uses_prompt_preview_handler(self):
        self.assertIs(
            self.commands.handle_prompt_preview_command,
            self.server.handle_prompt_preview_command,
        )


class RemainingCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.runtime_settings = importlib.import_module(
            "torque.commands.runtime_settings"
        )
        cls.asks = importlib.import_module("torque.commands.asks")
        cls.pipelines = importlib.import_module("torque.commands.pipelines")
        cls.server = importlib.import_module("torque.server")

    def test_route_manifests_are_exact(self):
        self.assertEqual(
            EXPECTED_RUNTIME_SETTINGS_COMMANDS,
            set(self.runtime_settings.RUNTIME_SETTINGS_COMMAND_NAMES),
        )
        self.assertEqual({"resolve_ask"}, set(self.asks.ASK_COMMAND_NAMES))
        self.assertEqual(
            {"task_chain", "discover_pipelines"},
            set(self.pipelines.PIPELINE_COMMAND_NAMES),
        )

    def test_server_uses_domain_handlers(self):
        self.assertIs(
            self.runtime_settings.handle_runtime_settings_command,
            self.server.handle_runtime_settings_command,
        )
        self.assertIs(
            self.asks.handle_ask_command,
            self.server.handle_ask_command,
        )
        self.assertIs(
            self.pipelines.handle_pipeline_command,
            self.server.handle_pipeline_command,
        )


class AgentLifecycleCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.agents")
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_AGENT_LIFECYCLE_COMMANDS,
            set(self.commands.AGENT_LIFECYCLE_COMMAND_NAMES),
        )

    def test_server_preserves_agent_lifecycle_compatibility_exports(self):
        self.assertIs(
            self.commands._handle_remove_agent_command,
            self.server._handle_remove_agent_command,
        )
        self.assertIs(
            self.commands._handle_restore_agent_command,
            self.server._handle_restore_agent_command,
        )


class MemoryCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.memory")
        cls.server = importlib.import_module("torque.server")

    def test_registry_inventory_is_complete_and_exact(self):
        self.assertEqual(
            EXPECTED_MEMORY_COMMANDS,
            set(self.commands.MEMORY_COMMAND_NAMES),
        )
        self.assertEqual(
            tuple(sorted(EXPECTED_MEMORY_COMMANDS)),
            self.commands._MEMORY_COMMAND_REGISTRY.route_names(),
        )

    def test_server_preserves_memory_compatibility_export(self):
        self.assertIs(
            self.commands._handle_memory_command,
            self.server._handle_memory_command,
        )


class AIReportCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.ai_reports")
        cls.server = importlib.import_module("torque.server")

    def test_worker_report_tool_manifest_is_exact(self):
        self.assertEqual(
            {
                "progress", "done", "blocked", "error", "ask",
                "derive", "ready", "verify", "name",
            },
            set(self.commands.TORQUE_AI_MCP_REPORT_ACTIONS),
        )

    def test_server_uses_domain_handler_and_tool_manifest(self):
        self.assertIs(
            self.commands.handle_ai_report_command,
            self.server.handle_ai_report_command,
        )
        self.assertIs(
            self.commands.TORQUE_AI_MCP_REPORT_TOOL_NAMES,
            self.server._TORQUE_AI_MCP_REPORT_TOOL_NAMES,
        )


class WorktreeCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.worktrees")
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_WORKTREE_COMMANDS,
            set(self.commands.WORKTREE_COMMAND_NAMES),
        )

    def test_server_uses_worktree_domain_handler(self):
        self.assertIs(
            self.commands.handle_worktree_command,
            self.server.handle_worktree_command,
        )

    def test_server_reexports_modular_worktree_orchestration(self):
        preflight = importlib.import_module(
            "torque.services.worktrees.preflight"
        )
        finalize = importlib.import_module("torque.services.worktrees.finalize")
        pr_merge = importlib.import_module("torque.services.worktrees.pr_merge")
        gates = importlib.import_module("torque.services.worktrees.gates")
        evidence = importlib.import_module("torque.services.worktrees.evidence")

        self.assertIs(
            preflight._preflight_worktree_merge_gates,
            self.server._preflight_worktree_merge_gates,
        )
        self.assertIs(
            finalize._run_direct_worktree_merge,
            self.server._run_direct_worktree_merge,
        )
        self.assertIs(
            pr_merge._run_pr_worktree_merge,
            self.server._run_pr_worktree_merge,
        )
        self.assertIs(
            gates._handle_workflow_breach_command,
            self.server._handle_workflow_breach_command,
        )
        self.assertIs(
            evidence._persist_preserved_merge_diff_warning_only,
            self.server._persist_preserved_merge_diff_warning_only,
        )


class CatalogCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.catalog")
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_CATALOG_COMMANDS,
            set(self.commands.CATALOG_COMMAND_NAMES),
        )

    def test_server_uses_catalog_domain_handler(self):
        self.assertIs(
            self.commands.handle_catalog_command,
            self.server.handle_catalog_command,
        )


class ServerRouteModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.routes = importlib.import_module("torque.server_routes")
        cls.server = importlib.import_module("torque.server")

    def test_server_uses_route_factories(self):
        self.assertIs(self.routes.build_event_routes, self.server.build_event_routes)
        self.assertIs(self.routes.build_http_routes, self.server.build_http_routes)


class TaskDispatchCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module("torque.commands.task_dispatch")
        cls.server = importlib.import_module("torque.server")

    def test_server_uses_task_dispatch_domain_handler(self):
        self.assertIs(
            self.commands.handle_dispatch_task_command,
            self.server.handle_dispatch_task_command,
        )


class EngineerOperationCommandModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_aiohttp_stub()
        cls.commands = importlib.import_module(
            "torque.commands.engineer_operations"
        )
        cls.server = importlib.import_module("torque.server")

    def test_route_manifest_is_exact(self):
        self.assertEqual(
            EXPECTED_ENGINEER_OPERATION_COMMANDS,
            set(self.commands.ENGINEER_OPERATION_COMMAND_NAMES),
        )

    def test_server_uses_engineer_operation_domain_handler(self):
        self.assertIs(
            self.commands.handle_engineer_operation_command,
            self.server.handle_engineer_operation_command,
        )


if __name__ == "__main__":
    unittest.main()

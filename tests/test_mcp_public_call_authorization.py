from types import SimpleNamespace
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub(include_json_helpers=True)

import torque.mcp_public_call_authorization as public_call_authorization

from torque.mcp_public_call_authorization import (
    PublicCallAuthorizationDependencies,
    _classify_public_tool_call,
)


class _Authority:
    def __init__(self, *, allows=True):
        self._allows = allows

    def allows(self, _capability, *, scope=None):
        return self._allows


def _dependencies(*, handler, canonical, cell, authority=None,
                  tool_allowed=True, requirements=(), chain_names=frozenset()):
    return PublicCallAuthorizationDependencies(
        raw_tools_for_caller=lambda _state, _cell_id: (
            [{"name": handler}], cell, cell.kind,
        ),
        effective_class_authority_for_cell=lambda _cell: authority,
        visible_tools=lambda _state, _cell_id: [{"name": canonical}],
        all_tool_map={handler: {}},
        canonical_callable_handler_registry={canonical: (handler,)},
        engineer_architect_chain_tool_names=chain_names,
        tool_authority_definitions={
            handler: SimpleNamespace(requirements=requirements),
        },
        tool_allowed_by_authority=lambda _name, _authority: tool_allowed,
    )


class PublicCallAuthorizationReasonTests(unittest.TestCase):
    def test_projected_but_hidden_refusal_names_projection_layer(self):
        cell = SimpleNamespace(id="caller", kind="engineer", group="g",
                               hired_by_architect_id="")
        classification, *_rest = _classify_public_tool_call(
            SimpleNamespace(agents={"caller": cell}), "caller",
            "supervisor_message", {},
            _dependencies(
                handler="engineer_message_architect",
                canonical="supervisor_message",
                cell=cell,
                chain_names=frozenset({"engineer_message_architect"}),
            ),
        )
        self.assertEqual("known_but_not_projected", classification)
        self.assertEqual(
            "Authorization denied: supervisor_message is not projected for this caller.",
            public_call_authorization.public_call_refusal_message(
                classification, "supervisor_message"
            ),
        )

    def test_projected_frozen_authority_refusal_names_session_snapshot(self):
        cell = SimpleNamespace(id="caller", kind="architect", group="g")
        classification, *_rest = _classify_public_tool_call(
            SimpleNamespace(agents={"caller": cell}), "caller", "context", {},
            _dependencies(
                handler="torque_context", canonical="context", cell=cell,
                authority=_Authority(), tool_allowed=False,
            ),
        )
        self.assertEqual("known_but_frozen_authority_denied", classification)
        self.assertEqual(
            "Authorization denied: context is denied by this session's frozen authority snapshot; relaunch after an Agent Class change to refresh it.",
            public_call_authorization.public_call_refusal_message(
                classification, "context"
            ),
        )

    def test_projected_target_refusal_names_scope_without_target_oracle(self):
        cell = SimpleNamespace(id="caller", kind="architect", group="g")
        task = SimpleNamespace(
            id="TORQUE:peer", group="g", agent_id="",
            assigned_engineer_id="", created_by_architect_id="peer",
            assigned_architect_id="", created_by_engineer_id="", owner_engineer_id="",
        )
        state = SimpleNamespace(
            agents={"caller": cell}, board_tasks={task.id: task},
            resolve_board_task_id=lambda identifier: identifier,
        )
        requirement = SimpleNamespace(
            scope_argument="", target_argument="task", target_kind="task",
            capability="task.read", handler_scoped=False,
        )
        classification, *_rest = _classify_public_tool_call(
            state, "caller", "task_get", {"task": task.id},
            _dependencies(
                handler="architect_task_show", canonical="task_get", cell=cell,
                authority=_Authority(allows=False), requirements=(requirement,),
            ),
        )
        self.assertEqual("known_but_target_scope_denied", classification)
        self.assertEqual(
            "Authorization denied: target is outside this caller's authorized scope for task_get.",
            public_call_authorization.public_call_refusal_message(
                classification, "task_get"
            ),
        )

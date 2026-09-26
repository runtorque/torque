"""Observe the exact target allocated by an HTTP launch, including failed launches.

Observation belongs to one async command. It never infers ownership from names
or from a before/after snapshot of all agents. Completed outcomes are retained by
the route until their receipt is durable; they are not crash-safe transactions.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ..config import log
from ..persistence.creation_outcomes import load_created_agent

AGENT_CREATION_COMMANDS = frozenset({
    'add_worker', 'add_engineer', 'add_architect', 'add_terminal',
    'create_agent_from_class', 'architect_engineer_hire',
})
_current: ContextVar[CreationOutcome | None] = ContextVar('creation_outcome', default=None)


def record_creation_target(target_type: str, target_id: str) -> None:
    """Record allocation before mutation, only within the owning active command."""
    owner = _current.get()
    if (owner is not None and owner.active and not owner.target_id
            and target_type == owner.target_type and target_id):
        owner.target_id = target_id


@dataclass
class CreationOutcome:
    command: str
    key: str
    request_hash: str
    requested_name: str
    target_id: str = ''
    active: bool = False
    finished: bool = False
    result: Any = None
    error: str | None = None

    @property
    def target_type(self) -> str:
        return 'pending_hire' if self.command == 'architect_engineer_hire' else 'agent'

    async def execute(self, handler, data) -> None:
        token = _current.set(self)
        self.active = True
        try:
            self.result = await handler(data)
        except Exception as exc:
            log.exception("API creation command '%s' failed", self.command)
            self.error = str(exc)
        finally:
            self.active = False
            _current.reset(token)
        self.finished = True

    def resolve(self, state, db):
        if not self.finished:
            # Includes cancellation/shutdown during a launch. Never reexecute an
            # operation whose final outcome has not been observed.
            raise RuntimeError('Creation outcome is not yet available')
        failed = self.error is not None or (
            isinstance(self.result, dict) and self.result.get('type') == 'error'
        )
        if not failed:
            return self.result
        message = self.error if self.error is not None else self.result.get('message', '')
        target = self._target(state, db)
        if target is None:
            return {'type': 'error', 'message': message, 'creation_refused': True}
        return {
            'type': 'creation_incomplete', 'command': self.command,
            'idempotency_key': self.key, 'requested_name': self.requested_name,
            'message': message, 'target': target,
        }

    def _target(self, state, db) -> dict | None:
        if not self.target_id:
            return None
        if self.target_type == 'pending_hire':
            # Unlike the state facade, this read must propagate verification
            # failures. An unavailable database is not proof of rollback.
            row = db.load_pending_hire(self.target_id)
            if row is None:
                return None
            return {'type': 'pending_hire', 'id': self.target_id,
                    'name': row.get('requested_name', ''),
                    'architect_id': row.get('architect_id', ''),
                    'status': row.get('status', '')}
        cell = state.agents.get(self.target_id)
        row = ({'name': cell.name, 'kind': cell.kind, 'cell_type': cell.cell_type}
               if cell is not None else load_created_agent(db, self.target_id))
        if row is None:
            return None
        return {'type': 'agent', 'id': self.target_id, 'name': row['name'],
                'kind': 'terminal' if row['cell_type'] == 'terminal' else row['kind']}

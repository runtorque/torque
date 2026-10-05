"""Retain terminal command outcomes without treating errors as rollback.

The HTTP owner retains this object through receipt failures. Once persisted,
uncertain error receipts also prevent re-execution after route reconstruction.
This does not close the crash window before a receipt is persisted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import log
from ..terminal_adapter import TerminalInputUnavailableError

DELIVERY_COMMANDS = frozenset({'send_user_message', 'user_agent_message', 'user_agent_turn_cancel'})


@dataclass
class DeliveryOutcome:
    command: str
    key: str
    request_hash: str
    finished: bool = False
    result: Any = None

    async def execute(self, handler, data) -> None:
        try:
            self.result = await handler(data)
        except TerminalInputUnavailableError as exc:
            # Adapter contract: a verified preflight failure attempted no input.
            self.result = {'type': 'error', 'message': str(exc), 'delivery_refused': True}
        except Exception as exc:
            log.exception("API delivery command '%s' has an uncertain outcome", self.command)
            self.result = {'type': 'error', 'message': str(exc)}
        self.finished = True

    def resolve(self) -> dict:
        if not self.finished or not isinstance(self.result, dict):
            return {'type': 'error', 'message': 'Could not confirm the delivery outcome.',
                    'delivery_uncertain': True}
        if self.result.get('type') != 'error':
            return self.result
        flag = 'delivery_refused' if self.result.get('delivery_refused') is True else 'delivery_uncertain'
        return {'type': 'error', 'message': self.result.get('message') or 'Could not confirm the delivery outcome.',
                flag: True}

    def refusal_response(self, result: dict) -> dict:
        flag = 'delivery_refused' if result.get('delivery_refused') is True else 'delivery_uncertain'
        return {'ok': False, 'error': result['message'], flag: True,
                'command': self.command, 'idempotency_key': self.key}

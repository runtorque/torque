import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque import cloud_hooks
from torque.db import TorqueDB
from torque.state import AgentCell, MatrixState


class DirectMessageCloudObserverTests(unittest.TestCase):
    def test_state_notifies_observer_after_direct_message_cache_update(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / "torque.db")
        db.init()
        self.addCleanup(db.close)

        state = MatrixState(db=db)
        state.agents["worker-1"] = AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        events = []

        def observer(event):
            events.append(event)
            self.assertEqual(
                state.direct_messages_by_agent["worker-1"][0]["id"],
                "msg-direct-1",
            )

        unregister = cloud_hooks.register_direct_message_observer(observer)
        self.addCleanup(unregister)

        saved = state.save_direct_message({
            "id": "msg-direct-1",
            "thread_id": "user-agent:user:worker-1",
            "group_name": "g",
            "sender_id": "worker-1",
            "sender_kind": "agent",
            "recipient_id": "user",
            "recipient_kind": "user",
            "message": "hello user",
            "message_type": "message",
            "created_at": 100.0,
            "delivery_state": "delivered",
        })

        self.assertEqual(saved["id"], "msg-direct-1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "direct_message_saved")
        self.assertIs(events[0]["state"], state)
        self.assertEqual(events[0]["agent_ids"], ["worker-1"])


if __name__ == "__main__":
    unittest.main()

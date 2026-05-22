import asyncio
import unittest

from torque_ee_connector import create_connector


class EnterpriseConnectorBoundaryTests(unittest.TestCase):
    def test_connector_is_inert_until_explicitly_started(self):
        connector = create_connector(context={"config": {}})
        self.assertFalse(connector.started)

        asyncio.run(connector.on_direct_message({"type": "direct_message_saved"}))
        self.assertEqual(connector.observed_events, ["direct_message_saved"])
        self.assertTrue(connector._outbound_queue.empty())


if __name__ == "__main__":
    unittest.main()

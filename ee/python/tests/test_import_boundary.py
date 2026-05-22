import asyncio
import unittest

from torque_ee_connector import create_connector


class EnterpriseConnectorSkeletonTests(unittest.TestCase):
    def test_lifecycle_is_noop(self):
        connector = create_connector(context={"phase": 2})
        self.assertFalse(connector.started)

        asyncio.run(connector.start())
        self.assertTrue(connector.started)
        connector.on_direct_message({"type": "direct_message_saved"})
        self.assertEqual(connector.observed_events, ["direct_message_saved"])

        asyncio.run(connector.stop())
        self.assertFalse(connector.started)


if __name__ == "__main__":
    unittest.main()

import asyncio
import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EnterprisePythonPackageSkeletonTests(unittest.TestCase):
    def test_connector_package_import_boundary_is_inert_until_configured(self):
        ee_python = ROOT / "ee" / "python"
        sys.path.insert(0, str(ee_python))
        self.addCleanup(lambda: sys.path.remove(str(ee_python)))
        module = importlib.import_module("torque_ee_connector")
        connector = module.create_connector(context={"test": True})

        self.assertFalse(connector.started)
        asyncio.run(connector.on_direct_message({"type": "direct_message_saved"}))
        self.assertEqual(connector.observed_events, ["direct_message_saved"])
        self.assertTrue(connector._outbound_queue.empty())


if __name__ == "__main__":
    unittest.main()

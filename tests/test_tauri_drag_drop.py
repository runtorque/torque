import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TauriDragDropTests(unittest.TestCase):
    def test_main_window_leaves_file_drops_to_frontend_handlers(self):
        config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
        main_window = next(
            window
            for window in config["app"]["windows"]
            if window.get("label") == "main"
        )

        self.assertIs(main_window.get("dragDropEnabled"), False)

    def test_detached_windows_leave_file_drops_to_frontend_handlers(self):
        source = (ROOT / "src-tauri" / "src" / "commands" / "window.rs").read_text()

        self.assertIn(".disable_drag_drop_handler()", source)


if __name__ == "__main__":
    unittest.main()

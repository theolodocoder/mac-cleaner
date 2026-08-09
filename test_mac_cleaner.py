import os
import tempfile
import time
import unittest
import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import mac_cleaner
import mac_cleaner_gui


class CleanerTests(unittest.TestCase):
    def make_file(self, root: Path, name: str, age_days: int, size: int = 10) -> Path:
        path = root / name
        path.write_bytes(b"x" * size)
        timestamp = time.time() - age_days * 86400
        os.utime(path, (timestamp, timestamp))
        return path

    def test_finds_expected_clutter_and_ignores_normal_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_file(root, "Screenshot 2026-01-01 at 10.00.00.png", 10)
            self.make_file(root, "installer.dmg", 20)
            self.make_file(root, "notes.txt", 100)
            found, warnings = mac_cleaner.scan([root], min_age=7)
            self.assertEqual({item.path.name for item in found}, {
                "Screenshot 2026-01-01 at 10.00.00.png", "installer.dmg"
            })
            self.assertEqual(warnings, [])

    def test_archives_need_at_least_thirty_days(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_file(root, "recent.zip", 20)
            self.make_file(root, "old.zip", 31)
            found, _ = mac_cleaner.scan([root], min_age=7)
            self.assertEqual([item.path.name for item in found], ["old.zip"])
            self.assertTrue(found[0].important)

    def test_installer_is_recommended_after_one_day(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_file(root, "today.dmg", 0)
            self.make_file(root, "yesterday.dmg", 1)
            found, _ = mac_cleaner.scan([root], min_age=30)
            self.assertEqual([item.path.name for item in found], ["yesterday.dmg"])
            self.assertFalse(found[0].important)

    def test_recent_screenshot_is_visible_but_protected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_file(root, "Screenshot 2026-08-06 at 10.54.30.png", 0)
            found, _ = mac_cleaner.scan([root], min_age=7)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].reason, "recent screenshot/recording")
            self.assertTrue(found[0].important)

    def test_skips_project_trees(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "app"
            project.mkdir()
            (project / "package.json").write_text("{}")
            self.make_file(project, "Screenshot old.png", 100)
            found, _ = mac_cleaner.scan([root], min_age=7)
            self.assertEqual(found, [])

    def test_refuses_home_directory(self):
        found, warnings = mac_cleaner.scan([Path.home()], min_age=7)
        self.assertEqual(found, [])
        self.assertTrue(any("Refused broad scan" in warning for warning in warnings))

    def test_partitions_recommended_and_review_files(self):
        recommended = mac_cleaner.Candidate(Path("old.dmg"), 10, "old installer", 40)
        review = mac_cleaner.Candidate(Path("large.dmg"), 3 * 1024**3, "old installer", 40, True)
        normal_items, review_items = mac_cleaner.partition_candidates([review, recommended])
        self.assertEqual(normal_items, [recommended])
        self.assertEqual(review_items, [review])
        self.assertEqual(recommended.recommendation, "Recommended")
        self.assertEqual(review.recommendation, "Needs review")

    def test_parses_batch_file_selection(self):
        allowed = {1, 2, 3, 4, 5}
        self.assertEqual(mac_cleaner.parse_number_selection("1,3-5", allowed), [1, 3, 4, 5])
        self.assertEqual(mac_cleaner.parse_number_selection("all", allowed), [1, 2, 3, 4, 5])
        self.assertEqual(mac_cleaner.parse_number_selection("", allowed), [])

    def test_rejects_numbers_outside_group(self):
        with self.assertRaisesRegex(ValueError, "not available"):
            mac_cleaner.parse_number_selection("2,4", {1, 2, 3})

    def test_permanent_delete_only_removes_selected_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            selected_path = self.make_file(root, "selected.dmg", 5)
            kept_path = self.make_file(root, "kept.dmg", 5)
            candidate = mac_cleaner.Candidate(selected_path, 10, "downloaded installer", 5)
            deleted, bytes_deleted, errors = mac_cleaner.delete_permanently([candidate])
            self.assertEqual((deleted, bytes_deleted, errors), (1, 10, []))
            self.assertFalse(selected_path.exists())
            self.assertTrue(kept_path.exists())

    def test_move_to_trash_uses_native_macos_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_file(Path(folder), "installer.dmg", 5, 42)
            candidate = mac_cleaner.classify(path, path.stat(), time.time(), 7)
            self.assertIsNotNone(candidate)
            with patch("mac_cleaner.trash_item", return_value=Path("/Trash/installer.dmg")) as native:
                moved, bytes_moved, errors = mac_cleaner.move_to_trash([candidate])
            self.assertEqual((moved, bytes_moved, errors), (1, 42, []))
            native.assert_called_once_with(path)

    def test_changed_file_is_refused_before_trash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.make_file(root, "installer.dmg", 5, 10)
            candidate = mac_cleaner.classify(path, path.stat(), time.time(), 7)
            self.assertIsNotNone(candidate)
            path.write_bytes(b"changed after scan")
            with patch("mac_cleaner.trash_item") as native:
                moved, bytes_moved, errors = mac_cleaner.move_to_trash([candidate])
            self.assertEqual((moved, bytes_moved), (0, 0))
            self.assertIn("changed or was replaced", errors[0])
            native.assert_not_called()
            self.assertTrue(path.exists())

    def test_replaced_file_is_refused_before_permanent_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.make_file(root, "installer.dmg", 5, 10)
            candidate = mac_cleaner.classify(path, path.stat(), time.time(), 7)
            self.assertIsNotNone(candidate)
            path.unlink()
            replacement = self.make_file(root, "installer.dmg", 5, 10)
            deleted, bytes_deleted, errors = mac_cleaner.delete_permanently([candidate])
            self.assertEqual((deleted, bytes_deleted), (0, 0))
            self.assertIn("changed or was replaced", errors[0])
            self.assertTrue(replacement.exists())


class GuiServerTests(unittest.TestCase):
    def test_local_gui_requires_token_and_returns_config(self):
        server = mac_cleaner_gui.CleanerServer("test-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request("GET", "/api/config")
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()

            connection.request("GET", "/api/config", headers={
                "X-Mac-Cleaner-Token": "test-token"
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
            self.assertIn("folders", payload)

            body = json.dumps({"ids": [], "permanent": True})
            connection.request("POST", "/api/clean", body=body, headers={
                "Content-Type": "application/json",
                "X-Mac-Cleaner-Token": "test-token",
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertIn("typing DELETE", json.loads(response.read())["error"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

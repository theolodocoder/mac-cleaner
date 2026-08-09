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
import scheduler


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
            self.assertGreaterEqual(found[0].confidence, 88)
            self.assertEqual(found[0].category, "Installers")

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
        self.assertEqual(
            mac_cleaner.parse_number_selection("all", allowed, {1, 3}), [1, 3]
        )

    def test_rejects_numbers_outside_group(self):
        with self.assertRaisesRegex(ValueError, "not available"):
            mac_cleaner.parse_number_selection("2,4", {1, 2, 3})

    def test_global_selection_accepts_a_protected_file_number(self):
        recommended = mac_cleaner.Candidate(Path("installer.dmg"), 10, "installer", 8)
        protected = mac_cleaner.Candidate(
            Path("Screenshot recent.png"), 20, "recent screenshot/recording", 0, True
        )
        numbered = [(1, recommended), (2, protected)]
        with patch("builtins.input", return_value="2"):
            selected = mac_cleaner.ask_file_selection(
                "Select cleanup items", numbered, all_selection={1}
            )
        self.assertEqual(selected, [protected])

    def test_permanent_delete_only_removes_selected_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            selected_path = self.make_file(root, "selected.dmg", 5)
            kept_path = self.make_file(root, "kept.dmg", 5)
            candidate = mac_cleaner.Candidate(selected_path, 10, "downloaded installer", 5)
            with patch("mac_cleaner.record_cleanup"):
                deleted, bytes_deleted, errors = mac_cleaner.delete_permanently([candidate])
            self.assertEqual((deleted, bytes_deleted, errors), (1, 10, []))
            self.assertFalse(selected_path.exists())
            self.assertTrue(kept_path.exists())

    def test_move_to_trash_uses_native_macos_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_file(Path(folder), "installer.dmg", 5, 42)
            candidate = mac_cleaner.classify(path, path.stat(), time.time(), 7)
            self.assertIsNotNone(candidate)
            with patch("mac_cleaner.trash_item", return_value=Path("/Trash/installer.dmg")) as native, \
                    patch("mac_cleaner.record_cleanup") as journal:
                moved, bytes_moved, errors = mac_cleaner.move_to_trash([candidate])
            self.assertEqual((moved, bytes_moved, errors), (1, 42, []))
            native.assert_called_once_with(path)
            journal.assert_called_once()

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

    def test_overlapping_roots_and_hard_links_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child = root / "child"
            child.mkdir()
            original = self.make_file(child, "installer.dmg", 5)
            linked = root / "linked.dmg"
            os.link(original, linked)
            found, warnings = mac_cleaner.scan([root, child, root], min_age=7)
            self.assertEqual(len(found), 1)
            self.assertTrue(any("duplicate scan folder" in warning for warning in warnings))
            self.assertTrue(any("overlapping scan folder" in warning for warning in warnings))

    def test_cleanup_history_is_json_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / "history.jsonl"
            item = mac_cleaner.Candidate(Path("old.dmg"), 50, "downloaded installer", 5)
            with patch.dict(os.environ, {"MAC_CLEANER_HISTORY": str(journal)}):
                mac_cleaner.record_cleanup(item, "trash", Path("/Trash/old.dmg"))
            entry = json.loads(journal.read_text())
            self.assertEqual(entry["action"], "trash")
            self.assertEqual(entry["size"], 50)

    def test_loads_preset_and_json_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text(json.dumps({
                "preset": "conservative",
                "thresholds": {"installer_age": 3},
                "exclude_patterns": ["*/Private/*"],
                "include_folders": ["~/Downloads"],
            }))
            rules = mac_cleaner.load_rules(config)
            self.assertEqual(rules.preset, "conservative")
            self.assertEqual(rules.installer_age, 3)
            self.assertEqual(rules.archive_age, 90)
            self.assertEqual(rules.exclude_patterns, ("*/Private/*",))

    def test_config_exclusion_pattern_skips_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            excluded = root / "Private"
            excluded.mkdir()
            self.make_file(excluded, "installer.dmg", 20)
            rules = mac_cleaner.Rules(exclude_patterns=("*/Private",))
            found, _ = mac_cleaner.scan([root], min_age=7, rules=rules)
            self.assertEqual(found, [])

    def test_detects_byte_for_byte_duplicates_for_review(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            content = b"duplicate" * 150_000
            (root / "original.bin").write_bytes(content)
            (root / "copy.bin").write_bytes(content)
            found, _ = mac_cleaner.scan([root], min_age=7)
            duplicates = [item for item in found if item.category == "Duplicates"]
            self.assertEqual(len(duplicates), 1)
            self.assertTrue(duplicates[0].important)
            self.assertEqual(duplicates[0].confidence, 70)

    def test_empty_folder_detection_is_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            empty = root / "empty"
            empty.mkdir()
            hidden, _ = mac_cleaner.scan([root], 7)
            shown, _ = mac_cleaner.scan([root], 7, mac_cleaner.Rules(detect_empty_folders=True))
            self.assertEqual(hidden, [])
            self.assertEqual([item.path for item in shown], [empty.resolve()])
            self.assertEqual(shown[0].kind, "directory")

    def test_nonempty_storage_directory_refuses_permanent_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache"
            path.mkdir()
            (path / "data").write_text("cache")
            folder_stat = path.stat()
            item = mac_cleaner.Candidate(
                path, 5, "developer cache", 1, True,
                folder_stat.st_dev, folder_stat.st_ino, folder_stat.st_mtime_ns,
                85, "Developer caches", (), "directory", folder_stat.st_size, True,
            )
            deleted, _, errors = mac_cleaner.delete_permanently([item])
            self.assertEqual(deleted, 0)
            self.assertIn("only be moved to Trash", errors[0])
            self.assertTrue(path.exists())

    def test_marks_older_installer_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            older = self.make_file(root, "Example-App-1.0-arm64.dmg", 20)
            newer = self.make_file(root, "Example App 2.0 Apple Silicon.dmg", 5)
            found, _ = mac_cleaner.scan([root], 7)
            by_path = {item.path: item for item in found}
            self.assertEqual(by_path[older.resolve()].category, "Older installers")
            self.assertEqual(by_path[older.resolve()].confidence, 97)
            self.assertEqual(by_path[newer.resolve()].category, "Installers")

    def test_developer_cache_detection_is_directory_level_and_trash_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / "Library/Caches/Homebrew"
            cache.mkdir(parents=True)
            (cache / "download").write_bytes(b"cache-data")
            with patch("mac_cleaner.Path.home", return_value=root):
                found = mac_cleaner.special_storage_candidates(include_developer_caches=True)
            homebrew = next(item for item in found if item.path == cache)
            self.assertEqual(homebrew.size, 10)
            self.assertEqual(homebrew.kind, "directory")
            self.assertTrue(homebrew.trash_only)
            self.assertTrue(homebrew.important)

    def test_scan_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_file(root, "installer.dmg", 5)
            cancelled = threading.Event()
            cancelled.set()
            found, warnings = mac_cleaner.scan([root], 7, cancel_event=cancelled)
            self.assertEqual(found, [])
            self.assertTrue(any("Scan cancelled" in warning for warning in warnings))

    def test_writes_machine_readable_scan_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "report.json"
            item = mac_cleaner.Candidate(Path("old.dmg"), 50, "downloaded installer", 5)
            mac_cleaner.write_scan_report(report, [item], ["warning"], "balanced")
            payload = json.loads(report.read_text())
            self.assertEqual(payload["candidate_count"], 1)
            self.assertEqual(payload["total_bytes"], 50)
            self.assertEqual(payload["warnings"], ["warning"])
            mac_cleaner.update_cleanup_report(report, "trash", 1, 50, [])
            payload = json.loads(report.read_text())
            self.assertEqual(payload["cleanup"]["bytes_reclaimed"], 50)

    def test_installer_confidence_increases_when_app_is_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_file(Path(folder), "Example-App-2.0.dmg", 5)
            with patch("mac_cleaner.installed_app_families", return_value={"exampleapp"}):
                candidate = mac_cleaner.classify(path, path.stat(), time.time(), 7)
            self.assertEqual(candidate.confidence, 98)
            self.assertIn("matching application found", candidate.signals)


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

            with tempfile.TemporaryDirectory() as folder:
                installer = Path(folder) / "installer.dmg"
                installer.write_bytes(b"installer")
                timestamp = time.time() - 5 * 86400
                os.utime(installer, (timestamp, timestamp))
                body = json.dumps({
                    "folders": [folder], "min_age": 7, "preset": "balanced",
                    "duplicates": False,
                })
                connection.request("POST", "/api/scan", body=body, headers={
                    "Content-Type": "application/json",
                    "X-Mac-Cleaner-Token": "test-token",
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                results = json.loads(response.read())
                self.assertGreaterEqual(results["recommended"][0]["confidence"], 88)

            connection.request("GET", "/api/progress", headers={
                "X-Mac-Cleaner-Token": "test-token"
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertFalse(json.loads(response.read())["active"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class SchedulerTests(unittest.TestCase):
    def test_schedule_is_report_only(self):
        payload = scheduler.schedule_payload("daily")
        arguments = payload["ProgramArguments"]
        self.assertIn("--dry-run", arguments)
        self.assertIn("--report", arguments)
        self.assertIn("--notify", arguments)
        self.assertNotIn("--yes", arguments)
        self.assertNotIn("--permanent", arguments)
        self.assertEqual(payload["StartInterval"], 86400)


if __name__ == "__main__":
    unittest.main()

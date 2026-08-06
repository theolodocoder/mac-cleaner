import os
import tempfile
import time
import unittest
from pathlib import Path

import mac_cleaner


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

    def test_refuses_home_directory(self):
        found, warnings = mac_cleaner.scan([Path.home()], min_age=7)
        self.assertEqual(found, [])
        self.assertTrue(any("Refused broad scan" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()

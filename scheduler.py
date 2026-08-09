"""Install and remove safe, report-only macOS launchd schedules."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


LABEL = "com.theolodocoder.maccleaner.scan"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def scheduled_arguments() -> list[str]:
    installed = shutil.which("mac-cleaner")
    if installed:
        command = [installed]
    else:
        command = [sys.executable, str(Path(__file__).with_name("mac_cleaner.py").resolve())]
    report = Path.home() / "Library" / "Application Support" / "Mac Cleaner" / "scheduled-report.json"
    return command + ["--dry-run", "--report", str(report), "--notify"]


def schedule_payload(frequency: str) -> dict[str, object]:
    intervals = {"daily": 86400, "weekly": 604800}
    if frequency not in intervals:
        raise ValueError(f"unsupported schedule: {frequency}")
    log_dir = Path.home() / "Library" / "Logs" / "Mac Cleaner"
    return {
        "Label": LABEL,
        "ProgramArguments": scheduled_arguments(),
        "StartInterval": intervals[frequency],
        "RunAtLoad": False,
        "StandardOutPath": str(log_dir / "scheduled-scan.log"),
        "StandardErrorPath": str(log_dir / "scheduled-scan-error.log"),
    }


def install_schedule(frequency: str) -> Path:
    target = launch_agent_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    (Path.home() / "Library" / "Logs" / "Mac Cleaner").mkdir(parents=True, exist_ok=True)
    target.write_bytes(plistlib.dumps(schedule_payload(frequency)))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", domain, str(target)], capture_output=True)
    result = subprocess.run(
        ["/bin/launchctl", "bootstrap", domain, str(target)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "launchctl could not install the schedule")
    return target


def remove_schedule() -> bool:
    target = launch_agent_path()
    if not target.exists():
        return False
    subprocess.run(
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(target)], capture_output=True
    )
    target.unlink()
    return True

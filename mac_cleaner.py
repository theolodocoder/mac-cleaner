#!/usr/bin/env python3
"""A conservative, dependency-free cleanup assistant for macOS."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SCREENSHOT_RE = re.compile(
    r"^(screen ?shot|screenshot|screen recording)[ _-].*\.(png|jpe?g|heic|mov)$",
    re.IGNORECASE,
)
INSTALLERS = {".dmg", ".pkg", ".mpkg"}
ARCHIVES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
PARTIALS = {".download", ".crdownload", ".part"}
SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "Library", "Applications",
    "System", ".Trash", ".cache", "venv", ".venv", "__pycache__",
}


@dataclass(frozen=True)
class Candidate:
    path: Path
    size: int
    reason: str
    age_days: int
    important: bool = False


def human_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def default_folders() -> list[Path]:
    home = Path.home()
    return [home / name for name in ("Downloads", "Desktop", "Pictures")]


def classify(path: Path, stat: os.stat_result, now: float, min_age: int) -> Candidate | None:
    age_days = max(0, int((now - stat.st_mtime) / 86400))
    suffix = path.suffix.lower()
    reason = ""

    if SCREENSHOT_RE.match(path.name) and age_days >= min_age:
        reason = "old screenshot/recording"
    elif suffix in INSTALLERS and age_days >= min_age:
        reason = "old installer"
    elif suffix in PARTIALS and age_days >= 1:
        reason = "incomplete download"
    elif suffix in ARCHIVES and age_days >= max(min_age, 30):
        reason = "old archive"
    else:
        return None

    # Recent or unusually large files deserve individual attention.
    important = age_days < 14 or stat.st_size >= 2 * 1024**3
    return Candidate(path, stat.st_size, reason, age_days, important)


def scan(roots: list[Path], min_age: int) -> tuple[list[Candidate], list[str]]:
    found: list[Candidate] = []
    warnings: list[str] = []
    now = time.time()

    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists() or not root.is_dir():
            warnings.append(f"Skipped missing folder: {root}")
            continue
        if root == Path("/") or root == Path.home().resolve():
            warnings.append(f"Refused broad scan target: {root}")
            continue

        def on_error(error: OSError) -> None:
            warnings.append(f"Could not read {error.filename}: {error.strerror}")

        for directory, dirnames, filenames in os.walk(root, topdown=True, onerror=on_error, followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if name not in SKIP_DIRS and not name.startswith(".")
                and not (Path(directory) / name).is_symlink()
            ]
            for filename in filenames:
                path = Path(directory) / filename
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    candidate = classify(path, path.stat(), now, min_age)
                    if candidate:
                        found.append(candidate)
                except (OSError, PermissionError) as error:
                    warnings.append(f"Could not inspect {path}: {error}")

    found.sort(key=lambda item: item.size, reverse=True)
    return found, warnings


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    hint = "[y/N]" if not default else "[Y/n]"
    while True:
        answer = input(f"{prompt} {hint} ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter y or n.")


def unique_trash_path(trash: Path, source: Path) -> Path:
    target = trash / source.name
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    counter = 1
    while True:
        target = trash / f"{source.stem}-{stamp}-{counter}{source.suffix}"
        if not target.exists():
            return target
        counter += 1


def move_to_trash(candidates: list[Candidate], trash: Path) -> tuple[int, int, list[str]]:
    trash.mkdir(mode=0o700, exist_ok=True)
    moved = bytes_moved = 0
    errors: list[str] = []
    for item in candidates:
        try:
            shutil.move(str(item.path), str(unique_trash_path(trash, item.path)))
            moved += 1
            bytes_moved += item.size
        except (OSError, PermissionError) as error:
            errors.append(f"Failed to move {item.path}: {error}")
    return moved, bytes_moved, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find common macOS clutter and safely move approved files to Trash."
    )
    parser.add_argument("folders", nargs="*", type=Path, help="folders to scan")
    parser.add_argument("--min-age", type=int, default=7, metavar="DAYS",
                        help="minimum age for screenshots/installers (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="scan only; change nothing")
    parser.add_argument("--yes", action="store_true",
                        help="approve normal candidates (flagged items still require review)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_age < 0:
        print("Error: --min-age cannot be negative.", file=sys.stderr)
        return 2

    roots = args.folders or default_folders()
    print("Scanning:")
    for root in roots:
        print(f"  • {root.expanduser()}")
    candidates, warnings = scan(roots, args.min_age)

    if not candidates:
        print("\nNo matching clutter found. Nothing was changed.")
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        return 0

    print(f"\nFound {len(candidates)} candidate(s), {human_size(sum(x.size for x in candidates))} total:\n")
    selected: list[Candidate] = []
    for index, item in enumerate(candidates, 1):
        marker = " ⚠ review" if item.important else ""
        print(f"{index:>3}. {human_size(item.size):>9}  {item.age_days:>4}d  {item.reason}{marker}\n     {item.path}")

    if args.dry_run:
        print("\nDry run complete. Nothing was changed.")
        return 0

    flagged = [item for item in candidates if item.important]
    normal = [item for item in candidates if not item.important]
    if normal and (args.yes or ask_yes_no(f"\nMove {len(normal)} normal candidate(s) to Trash?")):
        selected.extend(normal)

    for item in flagged:
        if ask_yes_no(f"Move flagged file to Trash? {item.path} ({human_size(item.size)}, {item.age_days}d old)"):
            selected.append(item)

    if not selected:
        print("Nothing selected. No files were changed.")
        return 0
    if not ask_yes_no(f"Final confirmation: move {len(selected)} file(s), {human_size(sum(x.size for x in selected))}, to Trash?"):
        print("Cancelled. No files were changed.")
        return 0

    moved, bytes_moved, errors = move_to_trash(selected, Path.home() / ".Trash")
    print(f"Moved {moved} file(s), {human_size(bytes_moved)}, to Trash. You can restore them from Finder.")
    for error in errors:
        print(f"Warning: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

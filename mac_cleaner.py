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
DISK_IMAGES = {".iso", ".img", ".ipsw"}
ARCHIVES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
PARTIALS = {".download", ".crdownload", ".part"}
SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "Library", "Applications",
    "System", ".Trash", ".cache", "venv", ".venv", "__pycache__",
    "Workspaces", "Projects", "Developer", "Pods", "vendor", "build", "dist",
}
PROJECT_MARKERS = {
    ".git", ".svn", ".hg", "package.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "composer.json", "Podfile", "Gemfile",
}


@dataclass(frozen=True)
class Candidate:
    path: Path
    size: int
    reason: str
    age_days: int
    important: bool = False

    @property
    def recommendation(self) -> str:
        return "Needs review" if self.important else "Recommended"


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

    important = False
    if suffix in INSTALLERS and age_days >= 1:
        # A downloaded installer is normally disposable once installation is done.
        reason = "downloaded installer"
        important = stat.st_size >= 2 * 1024**3
    elif suffix in PARTIALS and age_days >= 1:
        reason = "incomplete download"
    elif SCREENSHOT_RE.match(path.name):
        reason = "recent screenshot/recording" if age_days < min_age else "old screenshot/recording"
        important = age_days < max(min_age, 14) or stat.st_size >= 2 * 1024**3
    elif suffix in ARCHIVES and age_days >= max(min_age, 30):
        reason = "old archive"
        important = True
    elif suffix in DISK_IMAGES and age_days >= max(min_age, 14):
        reason = "old disk image"
        important = True
    elif stat.st_size >= 500 * 1024**2 and age_days >= max(min_age, 7):
        reason = "large old file"
        important = True
    else:
        return None

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
            current = Path(directory)
            if any((current / marker).exists() for marker in PROJECT_MARKERS):
                dirnames[:] = []
                continue
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


def parse_number_selection(answer: str, allowed: set[int]) -> list[int]:
    """Parse `1,3-5`, `a`, or an empty/none answer into sorted file numbers."""
    answer = answer.strip().lower()
    if answer in {"", "n", "none"}:
        return []
    if answer in {"a", "all"}:
        return sorted(allowed)

    selected: set[int] = set()
    for part in answer.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty item")
        if "-" in part:
            bounds = part.split("-", 1)
            if len(bounds) != 2 or not all(value.strip().isdigit() for value in bounds):
                raise ValueError(f"invalid range: {part}")
            start, end = (int(value.strip()) for value in bounds)
            if start > end:
                raise ValueError(f"range must go from low to high: {part}")
            selected.update(range(start, end + 1))
        elif part.isdigit():
            selected.add(int(part))
        else:
            raise ValueError(f"invalid file number: {part}")
    invalid = selected - allowed
    if invalid:
        raise ValueError(f"not available in this group: {', '.join(map(str, sorted(invalid)))}")
    return sorted(selected)


def ask_file_selection(label: str, numbered: list[tuple[int, Candidate]], action: str = "move to Trash") -> list[Candidate]:
    """Ask once for a batch selection from a displayed candidate group."""
    allowed = {number for number, _ in numbered}
    if not allowed:
        return []
    print(f"\n{label}")
    print("Enter a for all, file numbers/ranges such as 1,3-4, or press Enter for none.")
    while True:
        try:
            chosen = set(parse_number_selection(input(f"Files to {action}: "), allowed))
            return [item for number, item in numbered if number in chosen]
        except ValueError as error:
            print(f"Invalid selection ({error}). Please try again.")


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


def delete_permanently(candidates: list[Candidate]) -> tuple[int, int, list[str]]:
    """Permanently delete regular candidate files. This cannot be undone."""
    deleted = bytes_deleted = 0
    errors: list[str] = []
    for item in candidates:
        try:
            if item.path.is_symlink() or not item.path.is_file():
                raise OSError("file is no longer a safe regular file")
            item.path.unlink()
            deleted += 1
            bytes_deleted += item.size
        except (OSError, PermissionError) as error:
            errors.append(f"Failed to delete {item.path}: {error}")
    return deleted, bytes_deleted, errors


def partition_candidates(candidates: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
    """Return (recommended, needs_review) without changing the scan order."""
    return (
        [item for item in candidates if not item.important],
        [item for item in candidates if item.important],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find common macOS clutter and safely clean selected files."
    )
    parser.add_argument("folders", nargs="*", type=Path, help="folders to scan")
    parser.add_argument("--min-age", type=int, default=7, metavar="DAYS",
                        help="minimum age for screenshots and general clutter (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="scan only; change nothing")
    parser.add_argument("--yes", action="store_true",
                        help="approve normal candidates (flagged items still require review)")
    parser.add_argument("--gui", action="store_true",
                        help="open the optional graphical interface instead of the CLI")
    parser.add_argument("--permanent", action="store_true",
                        help="permanently delete selected files instead of moving them to Trash")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.gui:
        from mac_cleaner_gui import main as run_gui

        run_gui()
        return 0
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
    numbered: list[tuple[int, Candidate]] = []
    for index, item in enumerate(candidates, 1):
        numbered.append((index, item))
        marker = " ⚠ review" if item.important else ""
        print(f"{index:>3}. {human_size(item.size):>9}  {item.age_days:>4}d  {item.reason}{marker}\n     {item.path}")

    if args.dry_run:
        print("\nDry run complete. Nothing was changed.")
        return 0

    recommended, needs_review = partition_candidates(candidates)
    selection_action = "delete permanently" if args.permanent else "move to Trash"
    recommended_numbered = [(number, item) for number, item in numbered if not item.important]
    review_numbered = [(number, item) for number, item in numbered if item.important]
    if args.yes:
        selected.extend(recommended)
    else:
        selected.extend(ask_file_selection("Recommended cleanup", recommended_numbered, selection_action))

    if needs_review:
        selected.extend(ask_file_selection(
            "Needs review — these files are recent, large, or may be important",
            review_numbered,
            selection_action,
        ))

    if not selected:
        print("Nothing selected. No files were changed.")
        return 0
    if args.permanent:
        print(f"\nWARNING: {len(selected)} file(s), {human_size(sum(x.size for x in selected))}, will be permanently deleted.")
        if input("Type DELETE to continue: ").strip() != "DELETE":
            print("Cancelled. No files were changed.")
            return 0
        changed, bytes_changed, errors = delete_permanently(selected)
        print(f"Permanently deleted {changed} file(s), {human_size(bytes_changed)}.")
    else:
        changed, bytes_changed, errors = move_to_trash(selected, Path.home() / ".Trash")
        print(f"Moved {changed} file(s), {human_size(bytes_changed)}, to Trash. You can restore them from Finder.")
    for error in errors:
        print(f"Warning: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""A conservative, dependency-free cleanup assistant for macOS."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat as stat_module
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from datetime import datetime, timezone
from threading import Event
from typing import Callable

from macos_trash import trash_item


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
class Rules:
    preset: str = "balanced"
    installer_age: int = 1
    screenshot_recommended_age: int = 14
    archive_age: int = 30
    disk_image_age: int = 14
    large_file_age: int = 7
    large_file_bytes: int = 500 * 1024**2
    exclude_patterns: tuple[str, ...] = ()
    include_folders: tuple[Path, ...] = ()
    detect_duplicates: bool = True
    duplicate_min_bytes: int = 1024**2
    detect_empty_folders: bool = False


PRESETS: dict[str, Rules] = {
    "conservative": Rules("conservative", 7, 30, 90, 60, 30, 1024**3),
    "balanced": Rules(),
    "aggressive": Rules("aggressive", 1, 7, 14, 7, 3, 250 * 1024**2),
}

_INSTALLED_APP_FAMILIES: set[str] | None = None


def default_config_path() -> Path:
    return Path.home() / ".config" / "mac-cleaner" / "config.json"


def load_rules(config_path: Path | None = None, preset_override: str | None = None) -> Rules:
    """Load a preset and optional safe JSON overrides."""
    path = (config_path or default_config_path()).expanduser()
    payload: dict[str, object] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not load config {path}: {error}") from error
        if not isinstance(loaded, dict):
            raise ValueError(f"config must contain a JSON object: {path}")
        payload = loaded

    preset = preset_override or str(payload.get("preset", "balanced"))
    if preset not in PRESETS:
        raise ValueError(f"unknown preset: {preset}")
    rules = PRESETS[preset]
    thresholds = payload.get("thresholds", {})
    if not isinstance(thresholds, dict):
        raise ValueError("config thresholds must be an object")
    allowed = {
        "installer_age", "screenshot_recommended_age", "archive_age",
        "disk_image_age", "large_file_age", "large_file_bytes",
    }
    overrides: dict[str, int] = {}
    for key, value in thresholds.items():
        if key not in allowed:
            raise ValueError(f"unknown threshold: {key}")
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"threshold {key} must be a non-negative integer")
        overrides[key] = value
    exclude = payload.get("exclude_patterns", [])
    include = payload.get("include_folders", [])
    if not isinstance(exclude, list) or not all(isinstance(value, str) for value in exclude):
        raise ValueError("exclude_patterns must be a list of strings")
    if not isinstance(include, list) or not all(isinstance(value, str) for value in include):
        raise ValueError("include_folders must be a list of paths")
    detect_duplicates = payload.get("detect_duplicates", rules.detect_duplicates)
    detect_empty_folders = payload.get("detect_empty_folders", rules.detect_empty_folders)
    duplicate_min_bytes = payload.get("duplicate_min_bytes", rules.duplicate_min_bytes)
    if not isinstance(detect_duplicates, bool) or not isinstance(detect_empty_folders, bool):
        raise ValueError("detection feature flags must be true or false")
    if isinstance(duplicate_min_bytes, bool) or not isinstance(duplicate_min_bytes, int) or duplicate_min_bytes < 0:
        raise ValueError("duplicate_min_bytes must be a non-negative integer")
    return replace(
        rules,
        **overrides,
        exclude_patterns=tuple(exclude),
        include_folders=tuple(Path(value).expanduser() for value in include),
        detect_duplicates=detect_duplicates,
        duplicate_min_bytes=duplicate_min_bytes,
        detect_empty_folders=detect_empty_folders,
    )


@dataclass(frozen=True)
class Candidate:
    path: Path
    size: int
    reason: str
    age_days: int
    important: bool = False
    device_id: int | None = None
    inode: int | None = None
    modified_ns: int | None = None
    confidence: int = 0
    category: str = "Other"
    signals: tuple[str, ...] = ()
    kind: str = "file"
    stat_size: int | None = None
    trash_only: bool = False

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


def classify(
    path: Path, stat: os.stat_result, now: float, min_age: int, rules: Rules | None = None
) -> Candidate | None:
    rules = rules or PRESETS["balanced"]
    age_days = max(0, int((now - stat.st_mtime) / 86400))
    suffix = path.suffix.lower()
    reason = ""

    important = False
    confidence = 0
    category = "Other"
    signals: list[str] = []
    if suffix in INSTALLERS and age_days >= rules.installer_age:
        # A downloaded installer is normally disposable once installation is done.
        reason = "downloaded installer"
        category = "Installers"
        installed = likely_app_is_installed(path)
        confidence = 98 if installed else 88
        important = stat.st_size >= 2 * 1024**3
        signals = [
            f"installer file ({suffix})", f"{age_days} days old",
            "matching application found" if installed else "installation status unknown",
        ]
    elif suffix in PARTIALS and age_days >= 1:
        reason = "incomplete download"
        category = "Incomplete downloads"
        confidence = 99
        signals = [f"unfinished download ({suffix})", f"{age_days} days old"]
    elif SCREENSHOT_RE.match(path.name):
        reason = "recent screenshot/recording" if age_days < min_age else "old screenshot/recording"
        category = "Screenshots" if suffix != ".mov" else "Screen recordings"
        confidence = 92 if age_days >= rules.screenshot_recommended_age else 45
        important = confidence < 80 or stat.st_size >= 2 * 1024**3
        signals = ["recognized macOS capture name", f"{age_days} days old"]
    elif suffix in ARCHIVES and age_days >= max(min_age, rules.archive_age):
        reason = "old archive"
        category = "Archives"
        confidence = 55
        important = True
        signals = [f"archive file ({suffix})", f"{age_days} days old"]
    elif suffix in DISK_IMAGES and age_days >= max(min_age, rules.disk_image_age):
        reason = "old disk image"
        category = "Disk images"
        confidence = 50
        important = True
        signals = [f"disk image ({suffix})", f"{age_days} days old"]
    elif stat.st_size >= rules.large_file_bytes and age_days >= max(min_age, rules.large_file_age):
        reason = "large old file"
        category = "Large files"
        confidence = 35
        important = True
        signals = [f"larger than {human_size(rules.large_file_bytes)}", f"{age_days} days old"]
    else:
        return None

    return Candidate(
        path=path,
        size=stat.st_size,
        reason=reason,
        age_days=age_days,
        important=important,
        device_id=stat.st_dev,
        inode=stat.st_ino,
        modified_ns=stat.st_mtime_ns,
        confidence=confidence,
        category=category,
        signals=tuple(signals),
        stat_size=stat.st_size,
    )


def scan(
    roots: list[Path], min_age: int, rules: Rules | None = None,
    progress: Callable[[int, Path], None] | None = None,
    cancel_event: Event | None = None,
) -> tuple[list[Candidate], list[str]]:
    rules = rules or PRESETS["balanced"]
    found: list[Candidate] = []
    warnings: list[str] = []
    now = time.time()
    seen_files: set[tuple[int, int]] = set()
    duplicate_pool: dict[int, list[tuple[Path, os.stat_result]]] = {}
    empty_folders: list[tuple[Path, os.stat_result]] = []
    inspected = 0
    cancelled = False

    normalized_roots: list[Path] = []
    for supplied in roots:
        resolved = supplied.expanduser().resolve()
        if resolved in normalized_roots:
            warnings.append(f"Skipped duplicate scan folder: {resolved}")
            continue
        if any(resolved.is_relative_to(parent) for parent in normalized_roots):
            warnings.append(f"Skipped overlapping scan folder: {resolved}")
            continue
        children = [parent for parent in normalized_roots if parent.is_relative_to(resolved)]
        for child in children:
            normalized_roots.remove(child)
            warnings.append(f"Skipped overlapping scan folder: {child}")
        normalized_roots.append(resolved)

    for root in normalized_roots:
        if not root.exists() or not root.is_dir():
            warnings.append(f"Skipped missing folder: {root}")
            continue
        if root == Path("/") or root == Path.home().resolve():
            warnings.append(f"Refused broad scan target: {root}")
            continue

        def on_error(error: OSError) -> None:
            warnings.append(f"Could not read {error.filename}: {error.strerror}")

        for directory, dirnames, filenames in os.walk(root, topdown=True, onerror=on_error, followlinks=False):
            if cancel_event and cancel_event.is_set():
                cancelled = True
                break
            current = Path(directory)
            if any(fnmatch.fnmatch(str(current), pattern) for pattern in rules.exclude_patterns):
                dirnames[:] = []
                continue
            if any((current / marker).exists() for marker in PROJECT_MARKERS):
                dirnames[:] = []
                continue
            dirnames[:] = [
                name for name in dirnames
                if name not in SKIP_DIRS and not name.startswith(".")
                and not (Path(directory) / name).is_symlink()
            ]
            for filename in filenames:
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    break
                path = Path(directory) / filename
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    file_stat = path.stat()
                    identity = (file_stat.st_dev, file_stat.st_ino)
                    if identity in seen_files:
                        continue
                    seen_files.add(identity)
                    if rules.detect_duplicates and file_stat.st_size >= rules.duplicate_min_bytes:
                        duplicate_pool.setdefault(file_stat.st_size, []).append((path, file_stat))
                    candidate = classify(path, file_stat, now, min_age, rules)
                    if candidate:
                        found.append(candidate)
                    inspected += 1
                    if progress and (inspected == 1 or inspected % 250 == 0):
                        progress(inspected, current)
                except (OSError, PermissionError) as error:
                    warnings.append(f"Could not inspect {path}: {error}")

            if rules.detect_empty_folders and not dirnames and not filenames:
                try:
                    if not any(current.iterdir()):
                        empty_folders.append((current, current.stat()))
                except OSError as error:
                    warnings.append(f"Could not inspect empty folder {current}: {error}")
        if cancelled:
            break

    if cancelled:
        warnings.append(f"Scan cancelled after inspecting {inspected} files")

    existing_paths = {item.path for item in found}
    for size_group in duplicate_pool.values():
        if len(size_group) < 2:
            continue
        hashes: dict[str, list[tuple[Path, os.stat_result]]] = {}
        for path, file_stat in size_group:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                break
            try:
                digest = hash_file(path)
                hashes.setdefault(digest, []).append((path, file_stat))
            except OSError as error:
                warnings.append(f"Could not hash {path}: {error}")
        if cancelled:
            break
        for matches in hashes.values():
            if len(matches) < 2:
                continue
            matches.sort(key=lambda pair: (pair[1].st_mtime_ns, len(str(pair[0]))))
            original = matches[0][0]
            for path, file_stat in matches[1:]:
                if path in existing_paths:
                    continue
                age_days = max(0, int((now - file_stat.st_mtime) / 86400))
                found.append(Candidate(
                    path=path, size=file_stat.st_size, reason="byte-for-byte duplicate",
                    age_days=age_days, important=True, device_id=file_stat.st_dev,
                    inode=file_stat.st_ino, modified_ns=file_stat.st_mtime_ns,
                    confidence=70, category="Duplicates",
                    signals=(f"identical SHA-256 content to {original}",),
                    stat_size=file_stat.st_size,
                ))
                existing_paths.add(path)

    for path, folder_stat in empty_folders:
        found.append(Candidate(
            path=path, size=0, reason="empty folder", age_days=max(0, int((now - folder_stat.st_mtime) / 86400)),
            important=False, device_id=folder_stat.st_dev, inode=folder_stat.st_ino,
            modified_ns=folder_stat.st_mtime_ns, confidence=98, category="Empty folders",
            signals=("contains no files or folders",), kind="directory", stat_size=folder_stat.st_size,
        ))

    found = mark_older_installer_versions(found)
    found.sort(key=lambda item: item.size, reverse=True)
    if progress:
        progress(inspected, Path("."))
    return found, warnings


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def installer_family(path: Path) -> str:
    name = path.stem.lower()
    name = re.sub(r"\b(?:macos|mac|apple|silicon|intel|universal|arm64|aarch64|x86_64)\b", " ", name)
    name = re.sub(r"\bv?\d+(?:[._-]\d+)*\b", " ", name)
    return re.sub(r"[^a-z]+", "", name)


def installed_app_families() -> set[str]:
    global _INSTALLED_APP_FAMILIES
    if _INSTALLED_APP_FAMILIES is None:
        families: set[str] = set()
        for root in (Path("/Applications"), Path.home() / "Applications", Path("/System/Applications")):
            try:
                families.update(installer_family(path) for path in root.glob("*.app"))
            except OSError:
                continue
        _INSTALLED_APP_FAMILIES = {family for family in families if family}
    return _INSTALLED_APP_FAMILIES


def likely_app_is_installed(installer: Path) -> bool:
    family = installer_family(installer)
    if len(family) < 4:
        return False
    return any(
        family.startswith(app) or app.startswith(family)
        for app in installed_app_families() if len(app) >= 4
    )


def mark_older_installer_versions(candidates: list[Candidate]) -> list[Candidate]:
    groups: dict[str, list[Candidate]] = {}
    for item in candidates:
        if item.category == "Installers":
            family = installer_family(item.path)
            if family:
                groups.setdefault(family, []).append(item)
    replacements: dict[Path, Candidate] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        newest = max(group, key=lambda item: item.modified_ns or 0)
        for item in group:
            if item is newest:
                continue
            replacements[item.path] = replace(
                item,
                reason="older installer version",
                category="Older installers",
                confidence=97,
                signals=item.signals + (f"newer related installer found: {newest.path.name}",),
            )
    return [replacements.get(item.path, item) for item in candidates]


def directory_size(path: Path) -> int:
    total = 0
    for directory, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [name for name in dirnames if not (Path(directory) / name).is_symlink()]
        for filename in filenames:
            child = Path(directory) / filename
            try:
                if not child.is_symlink():
                    total += child.stat().st_size
            except OSError:
                continue
    return total


def special_storage_candidates(
    include_developer_caches: bool = False, include_iphone_backups: bool = False
) -> list[Candidate]:
    """Return opt-in, directory-level storage candidates that always require review."""
    home = Path.home()
    specs: list[tuple[Path, str, str, int]] = []
    if include_developer_caches:
        specs.extend([
            (home / "Library/Developer/Xcode/DerivedData", "Developer caches", "Xcode DerivedData", 82),
            (home / "Library/Developer/CoreSimulator/Caches", "Developer caches", "simulator caches", 82),
            (home / "Library/Caches/Homebrew", "Developer caches", "Homebrew cache", 85),
            (home / ".npm/_cacache", "Developer caches", "npm download cache", 85),
            (home / "Library/Caches/pip", "Developer caches", "pip download cache", 85),
        ])
    if include_iphone_backups:
        backup_root = home / "Library/Application Support/MobileSync/Backup"
        if backup_root.is_dir():
            try:
                specs.extend(
                    (child, "iPhone backups", "local iPhone backup", 35)
                    for child in backup_root.iterdir() if child.is_dir() and not child.is_symlink()
                )
            except OSError:
                pass

    now = time.time()
    candidates: list[Candidate] = []
    for path, category, reason, confidence in specs:
        try:
            folder_stat = path.stat()
            size = directory_size(path)
            if size == 0:
                continue
            candidates.append(Candidate(
                path=path, size=size, reason=reason,
                age_days=max(0, int((now - folder_stat.st_mtime) / 86400)),
                important=True, device_id=folder_stat.st_dev, inode=folder_stat.st_ino,
                modified_ns=folder_stat.st_mtime_ns, confidence=confidence, category=category,
                signals=("opt-in storage location", "directory will only be moved to Trash"),
                kind="directory", stat_size=folder_stat.st_size, trash_only=True,
            ))
        except OSError:
            continue
    return candidates


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


def history_path() -> Path:
    override = os.environ.get("MAC_CLEANER_HISTORY")
    return Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / "Mac Cleaner" / "history.jsonl"


def record_cleanup(item: Candidate, action: str, destination: Path | None = None) -> None:
    """Append one successful cleanup operation to the local history journal."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "original_path": str(item.path),
        "destination": str(destination) if destination else None,
        "size": item.size,
        "reason": item.reason,
        "device_id": item.device_id,
        "inode": item.inode,
        "kind": item.kind,
        "category": item.category,
        "confidence": item.confidence,
    }
    target = history_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with target.open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(limit: int = 20) -> list[dict[str, object]]:
    target = history_path()
    if not target.exists():
        return []
    entries: list[dict[str, object]] = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    entries.append(value)
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return entries[-limit:][::-1]


def candidate_dict(item: Candidate) -> dict[str, object]:
    return {
        "path": str(item.path), "size": item.size, "age_days": item.age_days,
        "reason": item.reason, "category": item.category, "confidence": item.confidence,
        "recommendation": item.recommendation, "signals": list(item.signals), "kind": item.kind,
    }


def write_scan_report(path: Path, candidates: list[Candidate], warnings: list[str], preset: str) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "preset": preset,
        "candidate_count": len(candidates), "total_bytes": sum(item.size for item in candidates),
        "warnings": warnings, "candidates": [candidate_dict(item) for item in candidates],
    }
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def update_cleanup_report(path: Path, action: str, changed: int, bytes_changed: int, errors: list[str]) -> None:
    target = path.expanduser()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    payload["cleanup"] = {
        "completed_at": datetime.now(timezone.utc).isoformat(), "action": action,
        "changed": changed, "bytes_reclaimed": bytes_changed, "errors": errors,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def notify(message: str) -> None:
    """Display a local macOS notification without interpolating shell code."""
    script = "on run argv\ndisplay notification (item 1 of argv) with title \"Mac Cleaner\"\nend run"
    subprocess.run(["/usr/bin/osascript", "-e", script, message], capture_output=True)


def move_to_trash(candidates: list[Candidate]) -> tuple[int, int, list[str]]:
    """Move candidates with macOS's native, volume-aware Trash operation."""
    moved = bytes_moved = 0
    errors: list[str] = []
    for item in candidates:
        try:
            verify_candidate_unchanged(item)
            destination = trash_item(item.path)
            moved += 1
            bytes_moved += item.size
            try:
                record_cleanup(item, "trash", destination)
            except OSError as error:
                errors.append(f"Moved {item.path}, but could not write cleanup history: {error}")
        except (OSError, PermissionError) as error:
            errors.append(f"Failed to move {item.path}: {error}")
    return moved, bytes_moved, errors


def delete_permanently(candidates: list[Candidate]) -> tuple[int, int, list[str]]:
    """Permanently delete regular candidate files. This cannot be undone."""
    deleted = bytes_deleted = 0
    errors: list[str] = []
    for item in candidates:
        try:
            verify_candidate_unchanged(item)
            if item.kind == "directory":
                if item.trash_only:
                    raise OSError("this directory is protected and can only be moved to Trash")
                item.path.rmdir()
            else:
                item.path.unlink()
            deleted += 1
            bytes_deleted += item.size
            try:
                record_cleanup(item, "permanent_delete")
            except OSError as error:
                errors.append(f"Deleted {item.path}, but could not write cleanup history: {error}")
        except (OSError, PermissionError) as error:
            errors.append(f"Failed to delete {item.path}: {error}")
    return deleted, bytes_deleted, errors


def verify_candidate_unchanged(item: Candidate) -> None:
    """Refuse files that are unsafe or no longer match their scan fingerprint."""
    try:
        current = item.path.lstat()
    except OSError as error:
        raise OSError(f"file is no longer available: {error}") from error

    expected_mode = stat_module.S_ISDIR if item.kind == "directory" else stat_module.S_ISREG
    if not expected_mode(current.st_mode):
        raise OSError(f"item is no longer a safe {item.kind}")

    expected = (item.device_id, item.inode, item.stat_size if item.stat_size is not None else item.size, item.modified_ns)
    # Manually constructed Candidates from integrations predating fingerprints
    # retain the basic regular-file check. Every scanner-created Candidate has
    # all four identity values.
    if item.device_id is None or item.inode is None or item.modified_ns is None:
        return
    actual = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    if actual != expected:
        raise OSError("file changed or was replaced after scanning; scan again")


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
    parser.add_argument("--min-age", type=int, default=None, metavar="DAYS",
                        help="minimum age for screenshots and general clutter")
    parser.add_argument("--preset", choices=tuple(PRESETS), help="cleanup sensitivity preset")
    parser.add_argument("--config", type=Path, help="path to JSON configuration")
    parser.add_argument("--duplicates", action=argparse.BooleanOptionalAction, default=None,
                        help="enable or disable byte-for-byte duplicate detection")
    parser.add_argument("--empty-folders", action="store_true", help="include empty folders")
    parser.add_argument("--developer-caches", action="store_true",
                        help="review known developer cache directories")
    parser.add_argument("--iphone-backups", action="store_true",
                        help="review local iPhone backup directories")
    parser.add_argument("--report", type=Path, help="write scan results as a JSON report")
    parser.add_argument("--history", action="store_true", help="show recent cleanup history and exit")
    parser.add_argument("--install-schedule", choices=("daily", "weekly"),
                        help="install a safe report-only scheduled scan")
    parser.add_argument("--remove-schedule", action="store_true", help="remove the scheduled scan")
    parser.add_argument("--notify", action="store_true", help=argparse.SUPPRESS)
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
    if args.install_schedule or args.remove_schedule:
        from scheduler import install_schedule, remove_schedule
        try:
            if args.remove_schedule:
                print("Scheduled scan removed." if remove_schedule() else "No scheduled scan was installed.")
            else:
                print(f"Scheduled scan installed: {install_schedule(args.install_schedule)}")
            return 0
        except OSError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
    if args.history:
        entries = read_history()
        if not entries:
            print("No cleanup history yet.")
        for entry in entries:
            print(f"{entry.get('timestamp', '?')}  {entry.get('action', '?'):>16}  {human_size(int(entry.get('size', 0)))}\n  {entry.get('original_path', '?')}")
        return 0
    if args.gui:
        from mac_cleaner_gui import main as run_gui

        run_gui()
        return 0
    if args.min_age is not None and args.min_age < 0:
        print("Error: --min-age cannot be negative.", file=sys.stderr)
        return 2

    try:
        rules = load_rules(args.config, args.preset)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    if args.duplicates is not None or args.empty_folders:
        rules = replace(
            rules,
            detect_duplicates=rules.detect_duplicates if args.duplicates is None else args.duplicates,
            detect_empty_folders=rules.detect_empty_folders or args.empty_folders,
        )
    minimum_age = args.min_age if args.min_age is not None else 7

    roots = args.folders or list(rules.include_folders) or default_folders()
    print(f"Scanning with {rules.preset} preset:")
    for root in roots:
        print(f"  • {root.expanduser()}")
    candidates, warnings = scan(roots, minimum_age, rules)
    candidates.extend(special_storage_candidates(args.developer_caches, args.iphone_backups))
    candidates.sort(key=lambda item: item.size, reverse=True)
    if args.report:
        try:
            write_scan_report(args.report, candidates, warnings, rules.preset)
            print(f"Report written to {args.report.expanduser()}")
        except OSError as error:
            print(f"Warning: could not write report: {error}", file=sys.stderr)

    if not candidates:
        print("\nNo matching clutter found. Nothing was changed.")
        if args.notify:
            notify("Scheduled scan complete: no clutter found.")
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        return 0

    print(f"\nFound {len(candidates)} candidate(s), {human_size(sum(x.size for x in candidates))} total:\n")
    selected: list[Candidate] = []
    numbered: list[tuple[int, Candidate]] = []
    for index, item in enumerate(candidates, 1):
        numbered.append((index, item))
        marker = " ⚠ review" if item.important else ""
        print(
            f"{index:>3}. {human_size(item.size):>9}  {item.age_days:>4}d  "
            f"{item.category} · {item.confidence}% confidence · {item.reason}{marker}\n"
            f"     {item.path}\n     Why: {', '.join(item.signals)}"
        )

    if args.dry_run:
        print("\nDry run complete. Nothing was changed.")
        if args.notify:
            notify(f"Scheduled scan found {len(candidates)} item(s), {human_size(sum(x.size for x in candidates))}.")
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
        cleanup_action = "permanent_delete"
        print(f"Permanently deleted {changed} file(s), {human_size(bytes_changed)}.")
    else:
        changed, bytes_changed, errors = move_to_trash(selected)
        cleanup_action = "trash"
        print(f"Moved {changed} file(s), {human_size(bytes_changed)}, to Trash. You can restore them from Finder.")
    for error in errors:
        print(f"Warning: {error}", file=sys.stderr)
    if args.report:
        try:
            update_cleanup_report(args.report, cleanup_action, changed, bytes_changed, errors)
        except OSError as error:
            print(f"Warning: could not update cleanup report: {error}", file=sys.stderr)
    if args.notify:
        notify(f"Cleanup processed {changed} item(s), {human_size(bytes_changed)}.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

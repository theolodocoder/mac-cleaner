# Mac Cleaner

A conservative Python cleanup assistant for macOS with an optional native GUI. It scans your Downloads,
Desktop, and Pictures folders for old screenshots, screen recordings, installers,
archives, and incomplete downloads. Approved files are moved to macOS Trash—never
permanently deleted.

## Requirements

- macOS
- Python 3.9 or newer
- No third-party packages

## Use it (default CLI)

The command-line interface is the default:

```bash
python3 mac_cleaner.py
```

Preview without changing anything:

```bash
python3 mac_cleaner.py --dry-run
```

Scan particular folders or change the minimum age:

```bash
python3 mac_cleaner.py ~/Downloads ~/Movies --min-age 14
```

## Optional GUI

Open the GUI only when you want it:

```bash
python3 mac_cleaner.py --gui
```

You can also double-click `Launch Mac Cleaner.command` in Finder.

The GUI opens as a private local page in your browser. It uses Python's standard
library, listens only on `127.0.0.1`, and requires a new random access token each
time it starts. No scan data leaves your Mac.

The GUI separates results into two groups:

- **Recommended cleanup:** old, recognizable clutter. One click moves the whole group to Trash without another prompt.
- **Needs review:** recent or unusually large files. These stay protected until you select them and approve one consolidated confirmation.

Files at least 2 GB or newer than 14 days are flagged for review. The command-line
version asks once for the whole review group, never once per file. The program skips hidden folders, common project/cache folders, symbolic
links, and refuses to scan `/` or your entire home directory.

macOS may ask you to grant Terminal access to Desktop, Downloads, or Pictures.

## Optional shortcut

```bash
chmod +x mac_cleaner.py
./mac_cleaner.py --dry-run
```

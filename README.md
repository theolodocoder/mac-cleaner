# Mac Cleaner

A conservative Python cleanup assistant with a native macOS GUI. It scans your Downloads,
Desktop, and Pictures folders for old screenshots, screen recordings, installers,
archives, and incomplete downloads. Approved files are moved to macOS Trash—never
permanently deleted.

## Requirements

- macOS
- Python 3.9 or newer
- No third-party packages

## Use the GUI

Double-click `Launch Mac Cleaner.command` in Finder, or run:

```bash
python3 mac_cleaner_gui.py
```

The GUI separates results into two groups:

- **Recommended cleanup:** old, recognizable clutter. One click moves the whole group to Trash without another prompt.
- **Needs review:** recent or unusually large files. These stay protected until you select them and approve one consolidated confirmation.

## Use the command line

Preview first:

```bash
cd /Users/promiseokafor/Desktop/Workspaces/prodev/mac-cleaner
python3 mac_cleaner.py --dry-run
```

Run interactively:

```bash
python3 mac_cleaner.py
```

Scan particular folders or change the minimum age:

```bash
python3 mac_cleaner.py ~/Downloads ~/Movies --min-age 14
```

Files at least 2 GB or newer than 14 days are flagged for review. The command-line
version asks once for the whole review group, never once per file. The program skips hidden folders, common project/cache folders, symbolic
links, and refuses to scan `/` or your entire home directory.

macOS may ask you to grant Terminal access to Desktop, Downloads, or Pictures.

## Optional shortcut

```bash
chmod +x mac_cleaner.py
./mac_cleaner.py --dry-run
```

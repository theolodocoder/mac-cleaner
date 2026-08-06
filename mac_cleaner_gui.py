#!/usr/bin/env python3
"""Optional, dependency-free graphical interface for Mac Cleaner."""

from __future__ import annotations

import os

# Apple's bundled Tk 8.5 emits this warning even though the classic widgets used
# below remain functional. It must be set before tkinter is imported.
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from mac_cleaner import (
    Candidate,
    default_folders,
    human_size,
    move_to_trash,
    partition_candidates,
    scan,
)


class CleanerApp(tk.Tk):
    BG = "#f3f5f8"
    CARD = "#ffffff"
    TEXT = "#182230"
    MUTED = "#667085"
    BORDER = "#d0d5dd"
    BLUE = "#1769e0"
    BLUE_ACTIVE = "#0f56bd"
    RED = "#b42318"
    FONT = ("Helvetica", 12)

    def __init__(self) -> None:
        super().__init__()
        self.title("Mac Cleaner")
        self.geometry("980x700")
        self.minsize(820, 580)
        self.configure(bg=self.BG)

        self.folders = list(default_folders())
        self.candidates: list[Candidate] = []
        self.recommended_items: list[Candidate] = []
        self.review_items: list[Candidate] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.age_var = tk.StringVar(value="7")
        self.status_var = tk.StringVar(value="Ready to scan")
        self.summary_var = tk.StringVar(value="No scan results yet")
        self.folder_var = tk.StringVar()

        self._build_ui()
        self._update_folder_label()
        self.after(100, self._drain_events)

    def _label(self, parent: tk.Widget, text: str = "", **kwargs: object) -> tk.Label:
        return tk.Label(parent, text=text, bg=kwargs.pop("bg", self.BG),
                        fg=kwargs.pop("fg", self.TEXT), font=kwargs.pop("font", self.FONT),
                        **kwargs)

    def _button(self, parent: tk.Widget, text: str, command: object,
                primary: bool = False, **kwargs: object) -> tk.Button:
        bg = self.BLUE if primary else self.CARD
        fg = "white" if primary else self.TEXT
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=self.BLUE_ACTIVE if primary else "#e9edf3",
            activeforeground="white" if primary else self.TEXT,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=self.BLUE if primary else self.BORDER,
            font=("Helvetica", 11, "bold"), padx=14, pady=8, cursor="pointinghand",
            **kwargs,
        )

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg=self.BG, padx=24, pady=22)
        outer.pack(fill="both", expand=True)

        self._label(outer, "Mac Cleaner", font=("Helvetica", 28, "bold")).pack(anchor="w")
        self._label(
            outer,
            "Safely recover space. Recommended clutter is one click; important files stay protected.",
            fg=self.MUTED,
        ).pack(anchor="w", pady=(3, 18))

        controls = tk.Frame(outer, bg=self.CARD, padx=14, pady=12,
                            highlightthickness=1, highlightbackground=self.BORDER)
        controls.pack(fill="x")
        self._label(controls, bg=self.CARD, textvariable=self.folder_var,
                    anchor="w").pack(side="left", fill="x", expand=True)
        self._button(controls, "Add Folder…", self._add_folder).pack(side="left", padx=(8, 14))
        self._label(controls, "Minimum age", bg=self.CARD).pack(side="left", padx=(0, 6))
        age_menu = tk.OptionMenu(controls, self.age_var, "1", "7", "14", "30", "60", "90")
        age_menu.configure(bg=self.CARD, fg=self.TEXT, relief="flat", highlightthickness=1,
                           highlightbackground=self.BORDER, font=("Helvetica", 11), width=3)
        age_menu["menu"].configure(font=("Helvetica", 11))
        age_menu.pack(side="left")
        self._label(controls, "days", bg=self.CARD).pack(side="left", padx=(4, 12))
        self.scan_button = self._button(controls, "Scan Now", self._start_scan, primary=True)
        self.scan_button.pack(side="left")

        summary = tk.Frame(outer, bg=self.CARD, padx=14, pady=12,
                           highlightthickness=1, highlightbackground=self.BORDER)
        summary.pack(fill="x", pady=12)
        self._label(summary, bg=self.CARD, textvariable=self.summary_var,
                    font=("Helvetica", 14, "bold")).pack(side="left")
        self._label(summary, bg=self.CARD, fg=self.MUTED,
                    textvariable=self.status_var).pack(side="right")

        lists = tk.PanedWindow(outer, orient="horizontal", sashwidth=8, sashrelief="flat",
                              bg=self.BG, bd=0, showhandle=False)
        lists.pack(fill="both", expand=True)
        recommended_card, self.recommended_list = self._make_list_card(
            lists, "Recommended cleanup", "Safe, recognizable clutter", selectmode="browse"
        )
        review_card, self.review_list = self._make_list_card(
            lists, "Needs review", "Recent or unusually large files", selectmode="extended"
        )
        lists.add(recommended_card, minsize=360, stretch="always")
        lists.add(review_card, minsize=360, stretch="always")

        actions = tk.Frame(outer, bg=self.BG, pady=14)
        actions.pack(fill="x")
        self._label(actions, "Everything goes to Trash and can be restored.",
                    fg=self.MUTED).pack(side="left")
        self.review_button = self._button(actions, "Move Selected Review Files…", self._clean_review)
        self.review_button.configure(state="disabled")
        self.review_button.pack(side="right")
        self.clean_button = self._button(
            actions, "Move Recommended to Trash", self._clean_recommended, primary=True
        )
        self.clean_button.configure(state="disabled")
        self.clean_button.pack(side="right", padx=8)

    def _make_list_card(self, parent: tk.Widget, title: str, subtitle: str,
                        selectmode: str) -> tuple[tk.Frame, tk.Listbox]:
        card = tk.Frame(parent, bg=self.CARD, padx=12, pady=12,
                        highlightthickness=1, highlightbackground=self.BORDER)
        self._label(card, title, bg=self.CARD, font=("Helvetica", 14, "bold")).pack(anchor="w")
        self._label(card, subtitle, bg=self.CARD, fg=self.MUTED,
                    font=("Helvetica", 10)).pack(anchor="w", pady=(1, 9))
        body = tk.Frame(card, bg=self.CARD)
        body.pack(fill="both", expand=True)
        listing = tk.Listbox(
            body, selectmode=selectmode, bg=self.CARD, fg=self.TEXT,
            selectbackground="#dbeafe", selectforeground=self.TEXT,
            font=("Menlo", 10), relief="flat", bd=0,
            highlightthickness=1, highlightbackground=self.BORDER,
            activestyle="none", exportselection=False,
        )
        scrollbar = tk.Scrollbar(body, orient="vertical", command=listing.yview)
        listing.configure(yscrollcommand=scrollbar.set)
        listing.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return card, listing

    def _update_folder_label(self) -> None:
        shown = ", ".join(path.name or str(path) for path in self.folders[:3])
        extra = f" +{len(self.folders) - 3} more" if len(self.folders) > 3 else ""
        self.folder_var.set(f"Scanning: {shown}{extra}")

    def _add_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Choose a folder to scan")
        if chosen:
            path = Path(chosen)
            if path not in self.folders:
                self.folders.append(path)
                self._update_folder_label()

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status_var.set(status)
        self.scan_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.clean_button.configure(state="disabled")
            self.review_button.configure(state="disabled")

    def _start_scan(self) -> None:
        self._set_busy(True, "Scanning…")
        threading.Thread(
            target=self._scan_worker,
            args=(list(self.folders), int(self.age_var.get())),
            daemon=True,
        ).start()

    def _scan_worker(self, folders: list[Path], minimum_age: int) -> None:
        try:
            self.events.put(("scan", scan(folders, minimum_age)))
        except Exception as error:
            self.events.put(("error", f"Scan failed: {error}"))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "scan":
                    candidates, warnings = payload  # type: ignore[misc]
                    self._show_results(candidates, warnings)
                elif kind == "clean":
                    moved, bytes_moved, errors = payload  # type: ignore[misc]
                    self._finish_clean(moved, bytes_moved, errors)
                else:
                    self._set_busy(False, "Ready")
                    messagebox.showerror("Mac Cleaner", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _show_results(self, candidates: list[Candidate], warnings: list[str]) -> None:
        self.candidates = candidates
        self.recommended_items, self.review_items = partition_candidates(candidates)
        self.recommended_list.delete(0, "end")
        self.review_list.delete(0, "end")
        for item in self.recommended_items:
            self.recommended_list.insert("end", self._row(item))
        for item in self.review_items:
            self.review_list.insert("end", self._row(item))
        total = sum(item.size for item in candidates)
        self.summary_var.set(
            f"{len(self.recommended_items)} recommended · "
            f"{len(self.review_items)} need review · {human_size(total)} found"
        )
        self._set_busy(False, "Scan complete")
        self.clean_button.configure(state="normal" if self.recommended_items else "disabled")
        self.review_button.configure(state="normal" if self.review_items else "disabled")
        if warnings:
            messagebox.showwarning("Some folders were skipped", "\n".join(warnings[:8]))

    @staticmethod
    def _row(item: Candidate) -> str:
        return (
            f"{human_size(item.size):>9}  {item.age_days:>4}d  "
            f"{item.path.name}  —  {item.reason}  —  {item.path.parent}"
        )

    def _clean_recommended(self) -> None:
        if self.recommended_items:
            self._start_clean(self.recommended_items)

    def _clean_review(self) -> None:
        selected = [self.review_items[index] for index in self.review_list.curselection()]
        if not selected:
            messagebox.showinfo("Select files", "Select one or more files in Needs review first.")
            return
        names = "\n".join(f"• {item.path.name} ({human_size(item.size)})" for item in selected[:8])
        if len(selected) > 8:
            names += f"\n• …and {len(selected) - 8} more"
        if messagebox.askyesno(
            "Move important files to Trash?",
            f"These files are recent or very large:\n\n{names}\n\nMove them to Trash?",
            icon="warning",
        ):
            self._start_clean(selected)

    def _start_clean(self, selected: list[Candidate]) -> None:
        self._set_busy(True, "Moving files to Trash…")
        threading.Thread(
            target=lambda: self.events.put(("clean", move_to_trash(selected, Path.home() / ".Trash"))),
            daemon=True,
        ).start()

    def _finish_clean(self, moved: int, bytes_moved: int, errors: list[str]) -> None:
        if errors:
            messagebox.showwarning("Cleanup finished with warnings", "\n".join(errors[:8]))
        else:
            messagebox.showinfo(
                "Cleanup complete",
                f"Moved {moved} file(s), {human_size(bytes_moved)}, to Trash.",
            )
        self._start_scan()


def main() -> None:
    CleanerApp().mainloop()


if __name__ == "__main__":
    main()

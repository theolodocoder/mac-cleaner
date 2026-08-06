#!/usr/bin/env python3
"""Native Tkinter interface for Mac Cleaner."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mac_cleaner import (
    Candidate,
    default_folders,
    human_size,
    move_to_trash,
    partition_candidates,
    scan,
)


class CleanerApp(tk.Tk):
    BG = "#f4f5f7"
    CARD = "#ffffff"
    TEXT = "#17202a"
    MUTED = "#657080"
    BLUE = "#1769e0"
    RED = "#b42318"

    def __init__(self) -> None:
        super().__init__()
        self.title("Mac Cleaner")
        self.geometry("980x680")
        self.minsize(820, 560)
        self.configure(background=self.BG)

        self.folders = list(default_folders())
        self.candidates: list[Candidate] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.age_var = tk.StringVar(value="7")
        self.status_var = tk.StringVar(value="Ready to scan")
        self.summary_var = tk.StringVar(value="No scan results yet")

        self._configure_styles()
        self._build_ui()
        self.after(100, self._drain_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("aqua")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", background=self.BG, foreground=self.TEXT,
                        font=("Helvetica Neue", 26, "bold"))
        style.configure("Sub.TLabel", background=self.BG, foreground=self.MUTED,
                        font=("Helvetica Neue", 12))
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("Card.TLabel", background=self.CARD, foreground=self.TEXT)
        style.configure("Summary.TLabel", background=self.CARD, foreground=self.TEXT,
                        font=("Helvetica Neue", 14, "bold"))
        style.configure("Treeview", rowheight=28, font=("Helvetica Neue", 11))
        style.configure("Treeview.Heading", font=("Helvetica Neue", 11, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Mac Cleaner", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Safely recover space. Recommended clutter is one click; important files stay protected.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 18))

        controls = ttk.Frame(outer, style="Card.TFrame", padding=14)
        controls.pack(fill="x")
        self.folder_label = ttk.Label(controls, style="Card.TLabel")
        self.folder_label.pack(side="left", fill="x", expand=True)
        ttk.Button(controls, text="Add Folder…", command=self._add_folder).pack(side="left", padx=6)
        ttk.Label(controls, text="Minimum age", style="Card.TLabel").pack(side="left", padx=(12, 5))
        ttk.Combobox(controls, textvariable=self.age_var, values=("1", "7", "14", "30", "60", "90"),
                     width=5, state="readonly").pack(side="left")
        ttk.Label(controls, text="days", style="Card.TLabel").pack(side="left", padx=(4, 10))
        self.scan_button = ttk.Button(controls, text="Scan Now", command=self._start_scan)
        self.scan_button.pack(side="left")
        self._update_folder_label()

        summary = ttk.Frame(outer, style="Card.TFrame", padding=14)
        summary.pack(fill="x", pady=12)
        ttk.Label(summary, textvariable=self.summary_var, style="Summary.TLabel").pack(side="left")
        ttk.Label(summary, textvariable=self.status_var, style="Card.TLabel").pack(side="right")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        recommended_frame = ttk.Frame(notebook, padding=8)
        review_frame = ttk.Frame(notebook, padding=8)
        notebook.add(recommended_frame, text="Recommended cleanup")
        notebook.add(review_frame, text="Needs review")

        self.recommended_tree = self._make_tree(recommended_frame, selectmode="none")
        self.review_tree = self._make_tree(review_frame, selectmode="extended")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Label(actions, text="Everything is moved to Trash and can be restored.",
                  style="Sub.TLabel").pack(side="left")
        self.review_button = ttk.Button(actions, text="Move Selected Review Files…",
                                        command=self._clean_review, state="disabled")
        self.review_button.pack(side="right")
        self.clean_button = ttk.Button(actions, text="Move Recommended to Trash",
                                       command=self._clean_recommended, state="disabled")
        self.clean_button.pack(side="right", padx=8)

    def _make_tree(self, parent: ttk.Frame, selectmode: str) -> ttk.Treeview:
        columns = ("size", "age", "reason", "path")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode=selectmode)
        tree.heading("size", text="Size")
        tree.heading("age", text="Age")
        tree.heading("reason", text="Why it was found")
        tree.heading("path", text="File")
        tree.column("size", width=85, anchor="e", stretch=False)
        tree.column("age", width=70, anchor="e", stretch=False)
        tree.column("reason", width=175, stretch=False)
        tree.column("path", width=550)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return tree

    def _update_folder_label(self) -> None:
        shown = ", ".join(path.name or str(path) for path in self.folders[:3])
        extra = f" +{len(self.folders) - 3} more" if len(self.folders) > 3 else ""
        self.folder_label.configure(text=f"Scanning: {shown}{extra}")

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
        minimum_age = int(self.age_var.get())
        threading.Thread(target=self._scan_worker, args=(list(self.folders), minimum_age), daemon=True).start()

    def _scan_worker(self, folders: list[Path], minimum_age: int) -> None:
        try:
            self.events.put(("scan", scan(folders, minimum_age)))
        except Exception as error:  # Keep GUI alive on unexpected filesystem errors.
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
        for tree in (self.recommended_tree, self.review_tree):
            tree.delete(*tree.get_children())
        recommended, review = partition_candidates(candidates)
        for item in recommended:
            self._insert(self.recommended_tree, item)
        for item in review:
            self._insert(self.review_tree, item)
        total = sum(item.size for item in candidates)
        self.summary_var.set(
            f"{len(recommended)} recommended · {len(review)} need review · {human_size(total)} found"
        )
        self._set_busy(False, "Scan complete")
        self.clean_button.configure(state="normal" if recommended else "disabled")
        self.review_button.configure(state="normal" if review else "disabled")
        if warnings:
            messagebox.showwarning("Some folders were skipped", "\n".join(warnings[:8]))

    @staticmethod
    def _insert(tree: ttk.Treeview, item: Candidate) -> None:
        tree.insert("", "end", iid=str(item.path), values=(
            human_size(item.size), f"{item.age_days}d", item.reason, str(item.path)
        ))

    def _clean_recommended(self) -> None:
        recommended, _ = partition_candidates(self.candidates)
        if recommended:
            self._start_clean(recommended)

    def _clean_review(self) -> None:
        paths = set(self.review_tree.selection())
        selected = [item for item in self.candidates if str(item.path) in paths and item.important]
        if not selected:
            messagebox.showinfo("Select files", "Select one or more files in Needs review first.")
            return
        names = "\n".join(f"• {item.path.name} ({human_size(item.size)})" for item in selected[:8])
        if len(selected) > 8:
            names += f"\n• …and {len(selected) - 8} more"
        approved = messagebox.askyesno(
            "Move important files to Trash?",
            f"These files were protected because they are recent or very large:\n\n{names}\n\nMove them to Trash?",
            icon="warning",
        )
        if approved:
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
            messagebox.showinfo("Cleanup complete", f"Moved {moved} file(s), {human_size(bytes_moved)}, to Trash.")
        self._start_scan()


def main() -> None:
    CleanerApp().mainloop()


if __name__ == "__main__":
    main()

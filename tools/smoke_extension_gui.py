#!/usr/bin/env python3
"""Exercise the real extension dialogs on a display, using temporary local files.

Run from a checkout with ``python tools/smoke_extension_gui.py``. Tk needs an
actual display (or an externally started Xorg/Xvfb); ffmpeg and ffprobe are used
for an optional video decode check. No FFHS login, browser extension, network
download, or Windows behavior is verified. Only startup/login, browser launch,
and message boxes are replaced. All workflow/association code and Tk workers
run normally, and all archive/profile/download files are temporary.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ffhs_archiver import extension_dialog as dialog_module
from ffhs_archiver.gui import App


def prepare_archive(base):
    archive = base / "archive"
    course = archive / "course"
    downloads = base / "downloads"
    downloads.mkdir()
    activities = []
    for number, title, names in (
        (1, "First activity", ["lecture.pdf", "shared.pdf"]),
        (2, "Second activity", ["slides.pptx", "shared.pdf"]),
    ):
        relative = f"section/{number:02}_activity.md"
        note = course / relative
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"---\ntitle: {title}\n---\n# {title}\n", encoding="utf-8")
        activities.append({"key": f"cm:{number}", "type": "resource", "title": title,
                           "md": relative, "files": [{"name": name} for name in names]})
    (archive / "courses.json").write_text(json.dumps({"courses": [{"id": 42, "folder": "course"}]}))
    (course / "course_structure.json").write_text(json.dumps({"course": {"id": 42}, "sections": [
        {"key": "section:1", "type": "section", "title": "Section", "children": activities}]}))
    return archive, course, downloads


def main():
    errors, infos, opened, dialogs, layout_errors = [], [], [], [], []
    root = tk.Tk()
    root.report_callback_exception = lambda typ, exc, tb: errors.append(f"Tk callback: {exc}")

    def pump(condition, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            root.update()
            if condition():
                return
            time.sleep(0.02)
        raise AssertionError("Timed out waiting for a real GUI worker/event result")

    original_dialog = dialog_module._ExtensionDialog

    def remember_dialog(*args, **kwargs):
        dialog = original_dialog(*args, **kwargs)
        dialogs.append(dialog)
        return dialog

    def check_busy(dialog, app):
        assert dialog.working and app.busy and app._importing
        for button in (dialog.start, dialog.scan, dialog.existing,
                       dialog.import_button, dialog.assign_button):
            assert str(button["state"]) == "disabled", button

    def check_idle(dialog, app):
        assert not dialog.working and not app.busy and not app._importing
        assert str(dialog.start["state"]) == "normal"
        assert str(dialog.existing["state"]) == "normal"

    def check_layout(dialog, phase):
        for width, height in ((960, 740), (760, 600)):
            dialog.window.geometry(f"{width}x{height}")
            root.update()
            for tab in (dialog.tree.master, dialog.pending_frame):
                if dialog.tool == "fetchv" and tab is dialog.pending_frame:
                    continue
                dialog.tabs.select(tab)
                root.update()
                controls = [dialog.tabs, dialog.start, dialog.scan, dialog.existing,
                            dialog.report_button, dialog.folder_entry, dialog.browse,
                            dialog.browser]
                controls += ([dialog.pending_activity, dialog.assign_button, dialog.pending_tree]
                             if tab is dialog.pending_frame else [dialog.import_button, dialog.tree])
                left, top = dialog.window.winfo_rootx(), dialog.window.winfo_rooty()
                right, bottom = left + dialog.window.winfo_width(), top + dialog.window.winfo_height()
                for widget in controls:
                    if not widget.winfo_ismapped():
                        layout_errors.append(f"{phase}/{width}/{tab}: unmapped {widget}")
                        continue
                    x, y = widget.winfo_rootx(), widget.winfo_rooty()
                    if x < left or y < top or x + widget.winfo_width() > right or y + widget.winfo_height() > bottom:
                        layout_errors.append(f"{phase}/{width}/{tab}: clipped {widget}: "
                                             f"{x-left},{y-top},{widget.winfo_width()},{widget.winfo_height()}")
                # Within each action row, adjacent controls must stay separate.
                rows = [[dialog.start, dialog.scan, dialog.existing]]
                if tab is dialog.pending_frame:
                    rows.append([dialog.pending_activity, dialog.assign_button])
                else:
                    rows.append([dialog.import_button, dialog.report_button])
                for row in rows:
                    for first, second in zip(row, row[1:]):
                        if first.winfo_rootx() + first.winfo_width() > second.winfo_rootx():
                            layout_errors.append(f"{phase}/{width}: overlapping {first} and {second}")
        dialog.window.geometry("960x740")
        root.update()

    def payload_snapshot(course):
        folder = course / "materiali_moodle_buddy"
        return {path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
                for path in folder.rglob("*") if path.is_file() and path.suffix != ".md"}

    try:
        with tempfile.TemporaryDirectory(prefix="ffhs-gui-smoke-") as temporary, \
                patch.dict(os.environ, {"FFHS_ARCHIVER_HOME": str(Path(temporary) / "profile")}), \
                patch.object(App, "_startup"), patch.object(App, "_heartbeat"), \
                patch.object(dialog_module, "_ExtensionDialog", side_effect=remember_dialog), \
                patch("tkinter.messagebox.showerror", side_effect=lambda *a, **kw: errors.append(str(a))), \
                patch("tkinter.messagebox.showinfo", side_effect=lambda *a, **kw: infos.append(str(a))), \
                patch("ffhs_archiver.extension_workflow.open_moodle_page",
                      side_effect=lambda url, browser: opened.append((url, browser)) or True):
            archive, course, downloads = prepare_archive(Path(temporary))
            app = App(root)
            selected_course = {"id": 42, "fullname": "A long Moodle course name " * 5, "shortname": "SMOKE"}
            app.courses = [selected_course]
            app.tree.insert("", "end", iid="42", values=(selected_course["fullname"], "SMOKE", "", ""))
            app.tree.focus("42")
            app.tree.selection_set("42")
            app.dest_var.set(str(archive))
            app.settings.extension_download_dir = str(downloads)
            app._show_main()
            root.update()

            def show(tool):
                dialog_module.open_extension_dialog(app, tool)
                dialog = dialogs[-1]
                pump(lambda: not dialog.working and not app.busy)
                check_idle(dialog, app)
                return dialog

            buddy = show("moodle_buddy")
            check_layout(buddy, "empty")
            buddy.start.invoke()
            check_busy(buddy, app)
            pump(lambda: buddy.workflow is not None and not app.busy)
            long_folder = downloads / ("A_very_long_Moodle_course_context_" * 4) / "Documents"
            long_folder.mkdir(parents=True)
            bundle = long_folder / "Moodle Buddy course.zip"
            with ZipFile(bundle, "w") as zipped:
                zipped.writestr("Week 1/lecture.pdf", b"%PDF-1.4 exact unique lecture")
                zipped.writestr("Week 2/shared.pdf", b"%PDF-1.4 ambiguous shared lecture")
                zipped.writestr("Extra/unknown.pdf", b"%PDF-1.4 unknown document")
            original_bundle = bundle.read_bytes()
            buddy.scan.invoke()
            check_busy(buddy, app)
            pump(lambda: bool(buddy.items) and not app.busy)
            assert len(buddy.items) == 1
            assert buddy.tree.item("0", "values")[0] == bundle.name, "Preview hides basename behind course prefix"
            buddy.tree.selection_set("0")
            buddy.import_button.invoke()
            check_busy(buddy, app)
            pump(lambda: buddy.report is not None and not app.busy)
            check_idle(buddy, app)
            assert len(buddy.pending) == 2
            assert buddy.tabs.select() == str(buddy.pending_frame), "Pending tab was not automatically shown"
            assert {buddy.pending_tree.item(item, "values")[0]
                    for item in buddy.pending_tree.get_children()} == {"shared.pdf", "unknown.pdf"}
            assert not infos, f"Import displayed blocking showinfo: {infos}"
            assert bundle.read_bytes() == original_bundle
            before = payload_snapshot(course)
            assert len(before) == 3
            check_layout(buddy, "pending")
            buddy.close()
            root.update()

            # Version 0.5 ledgers had origins but no per-origin associations.
            ledger_path = course / ".archiver/moodle_buddy_imports.json"
            ledger = json.loads(ledger_path.read_text())
            for entry in ledger["files"].values():
                entry.pop("associations", None)
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            bundle.unlink()  # Reopening and assignment must use archived bytes.
            buddy = show("moodle_buddy")
            assert len(buddy.pending) == 2, "Older saved imports were not recovered"
            assert buddy.workflow is None and not buddy.items
            assert buddy.tabs.select() == str(buddy.pending_frame)
            buddy.pending_tree.selection_set(*buddy.pending_tree.get_children())
            buddy.pending_activity.current(next(i for i, target in enumerate(buddy.targets) if target["key"] == "cm:2"))
            buddy.pending_tree.event_generate("<<TreeviewSelect>>")
            root.update()
            assert str(buddy.assign_button["state"]) == "normal"
            buddy.assign_button.invoke()
            check_busy(buddy, app)
            pump(lambda: not buddy.pending and not app.busy)
            check_idle(buddy, app)
            assert "Restano 0" in buddy.status.get()
            assert buddy.tabs.tab(buddy.pending_frame, "text") == "File da collegare (0)"
            assert buddy.report.is_file()
            assert payload_snapshot(course) == before, "Association recopied or modified an archived payload"
            assert "Collegamenti da scegliere: **0**" in (course / "download_estensioni.md").read_text()
            buddy.close()
            root.update()
            buddy = show("moodle_buddy")
            assert not buddy.pending, "Assignments were not persisted after reopening"
            buddy.close()
            root.update()
            print("PASS: ambiguous ZIP import, automatic pending tab, 0.5 recovery, assignment without recopy, persisted zero pending")

            fetchv = show("fetchv")
            fetchv.activity.current(next(i for i, target in enumerate(fetchv.targets) if target["key"] == "cm:2"))
            check_layout(fetchv, "fetchv")
            fetchv.start.invoke()
            check_busy(fetchv, app)
            pump(lambda: fetchv.workflow is not None and not app.busy)
            ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
            source = downloads / "FetchV lesson.mp4"
            if ffmpeg and ffprobe:
                subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                                "testsrc=size=64x48:rate=5:duration=1", "-f", "lavfi", "-i",
                                "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt",
                                "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
            else:
                source.write_bytes(b"\x00\x00\x00\x18ftypmp42local-import-smoke")
            fetchv.scan.invoke()
            pump(lambda: bool(fetchv.items) and not app.busy)
            fetchv.tree.selection_set(*fetchv.tree.get_children())
            fetchv.import_button.invoke()
            check_busy(fetchv, app)
            pump(lambda: fetchv.report is not None and not app.busy)
            check_idle(fetchv, app)
            imported = next(path for path in course.rglob("*.mp4") if path.read_bytes() == source.read_bytes())
            assert hashlib.sha256(imported.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
            if ffmpeg and ffprobe:
                streams = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_entries",
                                                             "stream=codec_type", "-of", "json", str(imported)]))
                assert {entry["codec_type"] for entry in streams["streams"]} == {"video", "audio"}
                subprocess.run([ffmpeg, "-v", "error", "-i", str(imported), "-map", "0:v:0",
                                "-map", "0:a:0", "-f", "null", "-"], check=True)
                print("PASS: FetchV local video import, SHA-256 equality, audio/video probe and offline decode")
            else:
                print("PASS: FetchV byte import; SKIP media decode (ffmpeg/ffprobe absent)")
            fetchv.close()
            root.update()
            assert not errors, errors
            assert not infos, infos
            assert opened == [("https://moodle.ffhs.ch/course/view.php?id=42", "edge"),
                              ("https://moodle.ffhs.ch/mod/resource/view.php?id=2", "edge")], opened
            assert not layout_errors, "\n".join(layout_errors)
            print("PASS: 960×740 and 760×600 dialog/tab/button layouts, busy states, zero GUI errors or blocking info boxes")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()

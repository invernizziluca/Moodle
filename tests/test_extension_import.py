"""Real local files and ZIPs from browser extensions, with no browser/network access."""

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from ffhs_archiver.extension_import import (assign_buddy_files, import_buddy_downloads,
                                            import_fetchv_downloads, list_unmatched_buddy_files)


class ExtensionImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffhs-extension-import-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.archive = self.base / "archive"
        self.course = self.archive / "course"
        self.downloads = self.base / "downloads"
        self.downloads.mkdir()
        self.activities = []
        for number, title, names in ((1, "First activity", ["lecture.pdf", "shared.pdf"]),
                                     (2, "Second activity", ["slides.pptx", "shared.pdf"])):
            relative = f"section/activity_{number}/activity_{number}.md"
            note = self.course / relative
            note.parent.mkdir(parents=True)
            note.write_text(f"---\ntitle: {title}\n---\n# {title}\n")
            self.activities.append({"key": f"cm:{number}", "type": "resource", "title": title,
                                    "md": relative, "files": [{"name": name} for name in names]})
        self.structure = {"course": {"id": 42}, "sections": [{"key": "section:1", "type": "section",
                           "title": "Section", "children": self.activities}]}
        self.write_metadata()

    def write_metadata(self):
        (self.archive / "courses.json").write_text(json.dumps({"courses": [{"id": 42, "folder": "course"}]}))
        (self.course / "course_structure.json").write_text(json.dumps(self.structure))

    def source(self, relative, content):
        path = self.downloads / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def run_import(self, sources, **kwargs):
        return import_buddy_downloads(sources, self.archive, 42, self.downloads, **kwargs)

    def payloads(self):
        return [path for path in (self.course / "materiali_moodle_buddy").rglob("*")
                if path.is_file() and path.suffix != ".md"] if (self.course / "materiali_moodle_buddy").exists() else []

    def test_real_downloads_preserve_hierarchy_bytes_and_originals(self):
        pdf = self.source("Course/Section/lecture.pdf", b"%PDF-1.4 real downloaded material")
        slides = self.source("Course/Section/slides.pptx", b"PK presentation bytes")
        result = self.run_import([pdf, slides])
        self.assertEqual(result["new_files"], 2)
        self.assertEqual(len(result["matched"]), 2)
        self.assertEqual(result["unmatched"], [])
        self.assertEqual({row["activity_key"] for row in result["matched"]}, {"cm:1", "cm:2"})
        for row in result["matched"]:
            self.assertEqual(row["path"].read_bytes(), (self.downloads / row["original"]).read_bytes())
            self.assertIn("Course/Section/", row["original"])
            self.assertLessEqual(len(row["path"].relative_to(self.archive).as_posix()), 150)
        self.assertTrue(pdf.is_file())
        self.assertTrue(slides.is_file())
        report = result["report_path"].read_text()
        self.assertIn("Associati per nome esatto", report)
        self.assertIn("Course/Section/lecture.pdf", report)
        note = next((self.course / "section/activity_1").glob("materiali_moodle_buddy*.md"))
        self.assertIn("lecture.pdf", note.read_text())
        self.assertTrue(note.read_text().startswith("---\n"))

    def test_ambiguous_and_unknown_names_are_not_guessed(self):
        files = [self.source("shared.pdf", b"shared material"),
                 self.source("First activity.mp4", b"video bytes"),
                 self.source("lecture (1).pdf", b"renamed by browser")]
        result = self.run_import(files)
        self.assertEqual(result["matched"], [])
        self.assertEqual(len(result["unmatched"]), 3)
        self.assertIn("Da associare", result["report_path"].read_text())
        self.assertFalse(list(self.course.glob("section/*/materiali_moodle_buddy*.md")))

    def test_assign_archived_file_without_original_or_another_copy(self):
        self.use_flat_notes()
        source = self.source("Course/Unknown folder/shared.pdf", b"ambiguous material")
        imported = self.run_import([source])
        payload = imported["files"][0]
        before = payload.stat().st_mtime_ns
        source.unlink()
        pending = list_unmatched_buddy_files(self.archive, 42)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["original"], "Course/Unknown folder/shared.pdf")
        self.assertEqual(pending[0]["path"], payload)
        result = assign_buddy_files(self.archive, 42, [pending[0]["id"]], "cm:2")
        self.assertEqual(result["assigned"], 1)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(list_unmatched_buddy_files(self.archive, 42), [])
        self.assertEqual(payload.read_bytes(), b"ambiguous material")
        self.assertEqual(payload.stat().st_mtime_ns, before)
        self.assertEqual(self.payloads(), [payload])
        self.assertIn("Second activity", result["report_path"].read_text())
        self.assertIn("shared.pdf", result["note"].read_text())
        self.assertEqual(result["note"].parent, self.course / "section/02_Activity")
        snapshot = {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()}
        repeated = assign_buddy_files(self.archive, 42, [pending[0]["id"]], "cm:2")
        self.assertEqual(repeated["assigned"], 0)
        self.assertEqual(snapshot, {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()})

    def test_legacy_ledger_without_associations_is_resolved_without_downloads(self):
        files = [self.source("lecture.pdf", b"known"), self.source("unknown.pdf", b"unknown")]
        self.run_import(files)
        ledger_path = self.course / ".archiver/moodle_buddy_imports.json"
        ledger = json.loads(ledger_path.read_text())
        for entry in ledger["files"].values():
            entry.pop("associations", None)
        ledger_path.write_text(json.dumps(ledger))
        pending = list_unmatched_buddy_files(self.archive, 42)
        self.assertEqual([row["name"] for row in pending], ["unknown.pdf"])
        result = assign_buddy_files(self.archive, 42, [pending[0]["id"]], "cm:1")
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(len(self.payloads()), 2)

    def test_reimport_preserves_user_assignment_for_ambiguous_filename(self):
        source = self.source("shared.pdf", b"shared")
        self.run_import([source])
        pending = list_unmatched_buddy_files(self.archive, 42)
        assign_buddy_files(self.archive, 42, [pending[0]["id"]], "cm:2")
        repeated = self.run_import([source])
        self.assertEqual(repeated["new_files"], 0)
        self.assertEqual(repeated["unmatched"], [])
        self.assertEqual(repeated["matched"][0]["activity_key"], "cm:2")

    def test_assignment_rollback_preserves_existing_ledger_payloads_and_notes(self):
        self.use_flat_notes()
        source = self.source("shared.pdf", b"shared")
        self.run_import([source])
        pending = list_unmatched_buddy_files(self.archive, 42)
        before = {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()}
        with patch("ffhs_archiver.extension_import.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                assign_buddy_files(self.archive, 42, [pending[0]["id"]], "cm:1")
        self.assertEqual(before, {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()})
        self.assertFalse((self.course / "section/01_Activity").exists())
        self.assertEqual(len(list_unmatched_buddy_files(self.archive, 42)), 1)

    def test_stale_foreign_or_modified_assignment_selection_changes_nothing(self):
        source = self.source("shared.pdf", b"shared")
        imported = self.run_import([source])
        pending = list_unmatched_buddy_files(self.archive, 42)
        before = {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()}
        for ids, key in ((["foreign-id"], "cm:1"), ([pending[0]["id"]], "cm:999"), ([], "cm:1")):
            with self.subTest(ids=ids, key=key), self.assertRaises(ValueError):
                assign_buddy_files(self.archive, 42, ids, key)
            self.assertEqual(before, {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()})
        imported["files"][0].write_bytes(b"edited by user")
        with self.assertRaises(ValueError):
            assign_buddy_files(self.archive, 42, [pending[0]["id"]], "cm:1")
        self.assertEqual(len(list_unmatched_buddy_files(self.archive, 42)), 1)

    def test_identical_payload_origins_can_be_assigned_independently(self):
        sources = [self.source("One/shared.pdf", b"same"), self.source("Two/shared.pdf", b"same")]
        imported = self.run_import(sources)
        self.assertEqual(len(imported["files"]), 1)
        self.assertEqual(imported["unmatched_files"], 1)
        pending = list_unmatched_buddy_files(self.archive, 42)
        self.assertEqual(len({row["id"] for row in pending}), 2)
        first = assign_buddy_files(self.archive, 42, [pending[0]["id"]], "cm:1")
        self.assertEqual(first["remaining"], 1)
        second = assign_buddy_files(self.archive, 42, [pending[1]["id"]], "cm:2")
        self.assertEqual(second["remaining"], 0)
        self.assertEqual(len(self.payloads()), 1)

    def test_buddy_explicit_activity_resolves_ambiguous_names_by_user_choice(self):
        files = [self.source("shared.pdf", b"shared material"),
                 self.source("unknown.pdf", b"unlisted file")]
        default = self.run_import(files)
        self.assertEqual(len(default["unmatched"]), 2)
        explicit = self.run_import(files, activity_key="cm:2")
        self.assertEqual(explicit["unmatched"], [])
        self.assertEqual(len(explicit["matched"]), 2)
        self.assertTrue(all(row["activity_key"] == "cm:2" for row in explicit["matched"]))
        self.assertEqual(explicit["new_files"], 0)
        report = explicit["report_path"].read_text()
        self.assertIn("Associati dall'utente", report)
        self.assertIn("scelta esplicitamente dall'utente", report)
        self.assertNotIn("Associati per nome esatto", report)
        self.assertTrue(list((self.course / "section/activity_2").glob("materiali_moodle_buddy*.md")))
        self.assertFalse(list((self.course / "section/activity_1").glob("materiali_moodle_buddy*.md")))

    def test_buddy_invalid_explicit_activity_publishes_nothing(self):
        source = self.source("shared.pdf", b"shared")
        with self.assertRaises(ValueError):
            self.run_import([source], activity_key="cm:999")
        self.assertEqual(self.payloads(), [])
        self.assertFalse((self.course / ".archiver/moodle_buddy_imports.json").exists())

    def test_folder_zip_is_really_expanded_and_context_is_preserved(self):
        bundle = self.downloads / "Course/Folder.zip"
        bundle.parent.mkdir()
        with ZipFile(bundle, "w") as archive:
            archive.writestr("Nested/lecture.pdf", b"lecture in zip")
            archive.writestr("Nested/Sub/worksheet.xlsx", b"spreadsheet in zip")
        original = bundle.read_bytes()
        result = self.run_import([bundle])
        self.assertEqual(result["new_files"], 2)
        self.assertEqual({p.read_bytes() for p in result["files"]}, {b"lecture in zip", b"spreadsheet in zip"})
        self.assertEqual(result["matched"][0]["original"], "Course/Folder/Nested/lecture.pdf")
        self.assertEqual(bundle.read_bytes(), original)
        self.assertNotIn(bundle, result["files"])

    def test_repeated_import_reuses_payload_report_and_activity_notes(self):
        source = self.source("lecture.pdf", b"fixed content")
        first = self.run_import([source])
        before = {path: path.stat().st_mtime_ns for path in self.course.rglob("*") if path.is_file()}
        again = self.run_import([source])
        self.assertEqual(again["new_files"], 0)
        self.assertEqual(again["reused_files"], 1)
        self.assertEqual(first["report_path"], again["report_path"])
        self.assertEqual(first["files"], again["files"])
        self.assertEqual(before, {path: path.stat().st_mtime_ns for path in self.course.rglob("*") if path.is_file()})

    def test_identical_bytes_in_distinct_paths_are_deduplicated(self):
        one = self.source("Section/lecture.pdf", b"same content")
        two = self.source("Other/copy.pdf", b"same content")
        result = self.run_import([one, two])
        self.assertEqual(result["new_files"], 1)
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(len(result["unmatched"]), 1)
        ledger = json.loads((self.course / ".archiver/moodle_buddy_imports.json").read_text())
        entry = ledger["files"][hashlib.sha256(b"same content").hexdigest()]
        self.assertEqual(set(entry["origins"]), {"Section/lecture.pdf", "Other/copy.pdf"})

    def test_missing_or_modified_local_copy_is_not_falsely_reused(self):
        source = self.source("lecture.pdf", b"right content")
        first = self.run_import([source])
        first["files"][0].write_bytes(b"user changed the previous copy")
        second = self.run_import([source])
        self.assertEqual(second["new_files"], 1)
        self.assertNotEqual(first["files"], second["files"])
        self.assertEqual(first["files"][0].read_bytes(), b"user changed the previous copy")
        self.assertEqual(second["files"][0].read_bytes(), b"right content")

    def test_zip_slip_symlinks_and_special_members_publish_nothing(self):
        for bad_name, mode in (("../outside.pdf", 0), ("/outside.pdf", 0),
                               ("x\\outside.pdf", 0), ("C:/outside.pdf", 0),
                               ("link.pdf", stat.S_IFLNK | 0o777), ("pipe", stat.S_IFIFO | 0o600)):
            with self.subTest(name=bad_name, mode=mode):
                bundle = self.downloads / "bad.zip"
                with ZipFile(bundle, "w") as archive:
                    archive.writestr("lecture.pdf", b"valid first member")
                    info = ZipInfo(bad_name)
                    info.external_attr = mode << 16
                    archive.writestr(info, b"bad")
                with self.assertRaises(ValueError):
                    self.run_import([bundle])
                self.assertEqual(self.payloads(), [])
                self.assertFalse((self.course / ".archiver/moodle_buddy_imports.json").exists())
        self.assertFalse((self.base / "outside.pdf").exists())

    def test_entry_and_uncompressed_byte_limits_fail_before_publication(self):
        bundle = self.downloads / "large.zip"
        with ZipFile(bundle, "w") as archive:
            archive.writestr("a.pdf", b"a" * 20)
            archive.writestr("b.pdf", b"b" * 20)
        for limits in ({"max_entries": 1}, {"max_bytes": 30}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.run_import([bundle], **limits)
            self.assertEqual(self.payloads(), [])

    def test_long_zip_paths_are_shortened_without_losing_original_context(self):
        bundle = self.downloads / "long.zip"
        original_name = "/".join(["Long section title " * 3] * 8 + ["a" * 120 + ".pdf"])
        with ZipFile(bundle, "w") as archive:
            archive.writestr(original_name, b"kept")
        result = self.run_import([bundle])
        self.assertLessEqual(len(result["files"][0].relative_to(self.archive).as_posix()), 150)
        self.assertEqual(result["files"][0].read_bytes(), b"kept")
        self.assertIn(original_name, result["report_path"].read_text())

    def test_ledger_commit_failure_rolls_back_only_this_import(self):
        previous = self.source("lecture.pdf", b"already archived")
        first = self.run_import([previous])
        ledger_path = self.course / ".archiver/moodle_buddy_imports.json"
        before_ledger = ledger_path.read_bytes()
        before_files = {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()}
        new = self.source("slides.pptx", b"new slides")
        with patch("ffhs_archiver.extension_import.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_import([new])
        self.assertEqual(ledger_path.read_bytes(), before_ledger)
        self.assertEqual(before_files, {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()})
        self.assertEqual(new.read_bytes(), b"new slides")
        self.assertTrue(first["report_path"].is_file())

    def test_source_inside_archive_or_outside_selected_download_folder_is_rejected(self):
        inside = self.course / "owned.pdf"
        inside.write_bytes(b"local archive file")
        outside = self.base / "outside.pdf"
        outside.write_bytes(b"outside")
        for path in (inside, outside):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.run_import([path])
        self.assertEqual(self.payloads(), [])

    def use_flat_notes(self):
        section = self.course / "section"
        for number, activity in enumerate(self.activities, 1):
            name = f"0{number}_Activity"
            (section / (name + ".md")).write_text("# " + name + "\n")
            activity["md"] = "section/" + name + ".md"
        self.write_metadata()
        return section

    def test_flat_layout_matches_keep_each_activity_in_its_own_folder(self):
        section = self.use_flat_notes()
        one = self.source("lecture.pdf", b"lecture")
        two = self.source("slides.pptx", b"slides")
        result = self.run_import([one, two])
        first = list((section / "01_Activity").glob("materiali_moodle_buddy*.md"))
        second = list((section / "02_Activity").glob("materiali_moodle_buddy*.md"))
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertIn("lecture.pdf", first[0].read_text())
        self.assertNotIn("slides.pptx", first[0].read_text())
        self.assertIn("slides.pptx", second[0].read_text())
        self.assertNotIn("lecture.pdf", second[0].read_text())
        self.assertEqual(len(result["matched"]), 2)
        self.assertFalse((section / "materiali_moodle_buddy.md").exists())

    def test_flat_layout_new_folders_are_rolled_back_on_ledger_failure(self):
        section = self.use_flat_notes()
        source = self.source("lecture.pdf", b"lecture")
        with patch("ffhs_archiver.extension_import.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_import([source])
        self.assertFalse((section / "01_Activity").exists())
        self.assertFalse((section / "02_Activity").exists())
        self.assertEqual((section / "01_Activity.md").read_text(), "# 01_Activity\n")
        self.assertEqual(self.payloads(), [])

    def test_fetchv_uses_the_explicit_activity_and_real_video_bytes(self):
        section = self.use_flat_notes()
        source = self.source("FetchV/video without a Moodle title.mp4", b"downloaded mp4 bytes")
        result = import_fetchv_downloads([source], self.archive, 42, "cm:2", self.downloads)
        self.assertEqual(result["new_files"], 1)
        self.assertEqual(result["unmatched"], [])
        self.assertEqual(result["matched"][0]["activity_key"], "cm:2")
        self.assertTrue(result["files"][0].is_relative_to(section / "02_Activity"))
        self.assertFalse((section / "01_Activity").exists())
        self.assertEqual(result["files"][0].read_bytes(), source.read_bytes())
        self.assertTrue(source.is_file())
        self.assertIn("Attività scelta dall'utente", result["report_path"].read_text())
        self.assertIn("FetchV", (section / "02_Activity/video_fetchv.md").read_text())

    def test_fetchv_reimport_deduplicates_per_activity_without_cross_assignment(self):
        section = self.use_flat_notes()
        source = self.source("video.webm", b"webm video bytes")
        first = import_fetchv_downloads([source], self.archive, 42, "cm:1", self.downloads)
        again = import_fetchv_downloads([source], self.archive, 42, "cm:1", self.downloads)
        second_activity = import_fetchv_downloads([source], self.archive, 42, "cm:2", self.downloads)
        self.assertEqual(again["new_files"], 0)
        self.assertEqual(again["files"], first["files"])
        self.assertEqual(again["report_path"], first["report_path"])
        self.assertEqual(second_activity["new_files"], 1)
        self.assertTrue(first["files"][0].is_relative_to(section / "01_Activity"))
        self.assertTrue(second_activity["files"][0].is_relative_to(section / "02_Activity"))
        self.assertNotEqual(first["files"], second_activity["files"])

    def test_fetchv_requires_a_real_activity_and_final_video_container(self):
        source = self.source("video.mp4", b"video")
        partial = self.source("segment.ts", b"single segment")
        bundle = self.source("FetchV.zip", b"not a final video")
        for files, activity in (([source], "cm:999"), ([partial], "cm:1"), ([bundle], "cm:1")):
            with self.subTest(files=files, activity=activity), self.assertRaises(ValueError):
                import_fetchv_downloads(files, self.archive, 42, activity, self.downloads)
        self.assertFalse((self.course / ".archiver/fetchv_imports.json").exists())

    def test_fetchv_commit_failure_preserves_source_and_existing_ledger(self):
        section = self.use_flat_notes()
        source = self.source("video.mp4", b"video")
        before = {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()}
        with patch("ffhs_archiver.extension_import.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                import_fetchv_downloads([source], self.archive, 42, "cm:1", self.downloads)
        self.assertEqual(before, {p: p.read_bytes() for p in self.course.rglob("*") if p.is_file()})
        self.assertFalse((section / "01_Activity").exists())
        self.assertEqual(source.read_bytes(), b"video")

    def test_corrupt_zip_crc_rejects_the_entire_import(self):
        bundle = self.downloads / "broken.zip"
        marker = b"unique-binary-payload-to-corrupt"
        with ZipFile(bundle, "w") as archive:
            archive.writestr("lecture.pdf", b"first valid member")
            archive.writestr("second.pdf", marker)
        content = bundle.read_bytes()
        self.assertEqual(content.count(marker), 1)
        bundle.write_bytes(content.replace(marker, b"X" + marker[1:]))
        from zipfile import BadZipFile
        with self.assertRaises(BadZipFile):
            self.run_import([bundle])
        self.assertEqual(self.payloads(), [])
        self.assertFalse((self.course / ".archiver/moodle_buddy_imports.json").exists())

    def test_symlink_source_or_import_register_cannot_redirect_a_copy(self):
        source = self.source("lecture.pdf", b"lecture")
        link = self.downloads / "symlink.pdf"
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links unavailable")
        with self.assertRaises(ValueError):
            self.run_import([link])
        outside = self.base / "outside"
        outside.mkdir()
        (self.course / ".archiver").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_import([source])
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

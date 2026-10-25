"""Local browser import: real file copies, activity mapping and containment."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ffhs_archiver.browser_import import import_directory, import_files, list_activities


class BrowserImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "archive"
        self.folder = self.root / "course" / "section" / "activity"
        self.folder.mkdir(parents=True)
        (self.folder / "note.md").write_text("# Attività\n")
        self.index = {"courses": [{"id": 42, "folder": "course"}]}
        self.structure = {"course": {"id": 42}, "sections": [{
            "key": "section:1", "type": "section", "title": "Section", "children": [{
                "key": "cm:2", "type": "page", "title": "Video [demo]",
                "md": "section/activity/note.md"}]}]}
        self.write_metadata()
        self.source = self.base / "video (1).mp4"
        self.source.write_bytes(b"user-selected-video")

    def write_metadata(self):
        (self.root / "courses.json").write_text(json.dumps(self.index))
        (self.root / "course" / "course_structure.json").write_text(json.dumps(self.structure))

    def test_activity_mapping_and_copy_with_immediate_local_note(self):
        activities = list_activities(self.root, 42)
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0]["directory"], self.folder)
        result = import_files([self.source], self.root, 42, "cm:2")
        self.assertEqual(result["files"][0].read_bytes(), self.source.read_bytes())
        self.assertTrue(self.source.is_file())
        note = result["note"].read_text()
        self.assertIn("video%20%281%29.mp4", note)
        self.assertIn("non certifica la completezza", note)

    def test_existing_files_and_notes_are_never_overwritten(self):
        (self.folder / self.source.name).write_bytes(b"previous-video")
        (self.folder / "materiali_browser.md").write_text("User's note")
        result = import_files([self.source], self.root, 42, "cm:2")
        self.assertEqual((self.folder / self.source.name).read_bytes(), b"previous-video")
        self.assertEqual((self.folder / "materiali_browser.md").read_text(), "User's note")
        self.assertEqual(result["files"][0].name, "video (1) (2).mp4")
        self.assertEqual(result["note"].name, "materiali_browser (2).md")

    def test_unknown_course_or_activity_does_not_copy(self):
        for course, activity in ((99, "cm:2"), (42, "cm:99")):
            with self.subTest(course=course), self.assertRaises(ValueError):
                import_files([self.source], self.root, course, activity)
        self.assertFalse((self.folder / self.source.name).exists())

    def test_course_and_note_traversal_are_rejected(self):
        for invalid in ("../outside", "/tmp/outside", "C:\\outside", "..\\outside"):
            with self.subTest(path=invalid):
                self.index["courses"][0]["folder"] = invalid
                self.write_metadata()
                with self.assertRaises(ValueError):
                    list_activities(self.root, 42)
        self.index["courses"][0]["folder"] = "course"
        self.structure["sections"][0]["children"][0]["md"] = "../../video (1).mp4"
        self.write_metadata()
        with self.assertRaises(ValueError):
            list_activities(self.root, 42)

    def test_symlink_source_and_destination_are_rejected(self):
        symlink = self.base / "link.mp4"
        try:
            symlink.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links unavailable")
        with self.assertRaises(ValueError):
            import_files([symlink], self.root, 42, "cm:2")
        (self.folder / "note.md").unlink()
        (self.folder / "note.md").symlink_to(self.source)
        with self.assertRaises(ValueError):
            list_activities(self.root, 42)

    def make_hls(self):
        hls = self.base / "Downloaded HLS"
        (hls / "segments").mkdir(parents=True)
        (hls / "video.m3u8").write_bytes(b"#EXTM3U\n#EXTINF:1,\nsegments/001.ts\n#EXT-X-ENDLIST\n")
        (hls / "segments" / "001.ts").write_bytes(b"\x47actual-segment-bytes")
        return hls

    def test_hls_directory_preserves_playlist_segments_and_originals(self):
        hls = self.make_hls()
        result = import_directory(hls, self.root, 42, "cm:2")
        self.assertEqual(len(result["files"]), 2)
        for original in hls.rglob("*"):
            if original.is_file():
                self.assertEqual((result["directory"] / original.relative_to(hls)).read_bytes(),
                                 original.read_bytes())
        self.assertEqual(result["playlist"], result["directory"] / "video.m3u8")
        self.assertIn("](video_browser/video.m3u8)", result["note"].read_text())

    def test_hls_directory_and_note_collisions_never_overwrite(self):
        hls = self.make_hls()
        (self.folder / "video_browser").mkdir()
        (self.folder / "video_browser" / "previous.txt").write_text("keep")
        (self.folder / "materiali_browser.md").write_text("keep note")
        result = import_directory(hls, self.root, 42, "cm:2")
        self.assertEqual(result["directory"].name, "video_browser (2)")
        self.assertEqual(result["note"].name, "materiali_browser (2).md")
        self.assertIn("video_browser%20%282%29/video.m3u8", result["note"].read_text())
        self.assertEqual((self.folder / "video_browser" / "previous.txt").read_text(), "keep")
        self.assertEqual((self.folder / "materiali_browser.md").read_text(), "keep note")

    def test_hls_rejects_escaping_playlist_and_invalid_windows_names_before_copy(self):
        hls = self.make_hls()
        outside = self.base / "outside.ts"
        outside.write_bytes(b"outside")
        (hls / "video.m3u8").write_text("#EXTM3U\n../outside.ts\n")
        with self.assertRaises(ValueError):
            import_directory(hls, self.root, 42, "cm:2")
        self.assertFalse((self.folder / "video_browser").exists())
        (hls / "video.m3u8").write_text("#EXTM3U\nsegments/001.ts\n")
        (hls / "CON.ts").write_bytes(b"invalid Windows name")
        with self.assertRaises(ValueError):
            import_directory(hls, self.root, 42, "cm:2")
        self.assertFalse((self.folder / "video_browser").exists())

    def test_hls_rejects_internal_symlinks_and_preserves_source(self):
        hls = self.make_hls()
        link = hls / "linked.ts"
        try:
            link.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links unavailable")
        with self.assertRaises(ValueError):
            import_directory(hls, self.root, 42, "cm:2")
        self.assertTrue(self.source.is_file())
        self.assertTrue((hls / "video.m3u8").is_file())
        self.assertFalse((self.folder / "video_browser").exists())

    def test_hls_copy_error_rolls_back_only_new_outputs(self):
        hls = self.make_hls()
        (self.folder / "materiali_browser.md").write_text("existing user's note")
        with patch("ffhs_archiver.browser_import.shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                import_directory(hls, self.root, 42, "cm:2")
        self.assertFalse((self.folder / "video_browser").exists())
        self.assertEqual((self.folder / "materiali_browser.md").read_text(), "existing user's note")
        self.assertEqual((hls / "segments" / "001.ts").read_bytes(), b"\x47actual-segment-bytes")


if __name__ == "__main__":
    unittest.main()

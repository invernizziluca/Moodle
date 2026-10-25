"""Keep complete HLS recordings coherent during local archive synchronization."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ffhs_archiver import fsutil
from ffhs_archiver.exporter import CourseExport, Exporter, ExportOptions, FileSpec, Item
from ffhs_archiver.hls import HLSResult
from ffhs_archiver.moodle import MoodleClient


class HLSSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hls-sync-")
        self.addCleanup(self.temp.cleanup)
        client = MoodleClient("https://moodle.ffhs.ch", "offline-test", min_interval=0, retries=0)
        exporter = Exporter(client, ExportOptions(dest=self.temp.name))
        self.course = CourseExport(exporter, {"id": 1, "fullname": "Course", "shortname": "C"}, "course")
        self.course.root.mkdir()
        self.root = self.course.root
        activity = Item(kind="activity", key="cm:1", title="Video", modname="url",
                        rel_dir="section/activity", md_path="section/activity/activity.md")
        self.course.root_item = Item(kind="course", key="course", title="Course", children=[activity])
        self.course.items = {activity.key: activity}
        self.activity = activity
        self.spec = FileSpec(key="media:stream", url="https://moodle.ffhs.ch/pluginfile.php/1/video.m3u8",
                             rel_path="section/activity/clip_hls/video.m3u8", kind="hls", owner=activity.key)
        self.course.specs = [self.spec]
        self.dest = (self.root / self.spec.rel_path).parent

    @staticmethod
    def recording(folder, content=b"old media"):
        folder.mkdir(parents=True)
        (folder / "video.m3u8").write_text("#EXTM3U\n#EXTINF:1,\ns00000.ts\n#EXT-X-ENDLIST\n")
        (folder / "s00000.ts").write_bytes(content)

    def test_rename_moves_playlist_and_segments_as_one_directory(self):
        old = self.root / "old/activity/clip_hls"
        self.recording(old)
        self.course.state.files[self.spec.key] = {"path": "old/activity/clip_hls/video.m3u8"}
        self.course._reconcile_moves()
        self.assertFalse(old.exists())
        self.assertEqual((self.dest / "s00000.ts").read_bytes(), b"old media")
        self.assertIn("s00000.ts", (self.dest / "video.m3u8").read_text())
        self.assertEqual(self.course.state.files[self.spec.key]["path"], self.spec.rel_path)
        self.assertEqual(self.course.counts["moved"], 1)

    def test_generated_segments_are_not_classified_as_manual_files(self):
        self.recording(self.dest)
        notes = self.root / self.activity.rel_dir / "appunti.md"
        notes.write_text("Appunti reali dell'utente")
        self.course._scan_manual_files()
        self.assertEqual(self.activity.manual_files, ["section/activity/appunti.md"])

    def test_failed_replacement_restores_complete_old_recording_without_versions(self):
        self.course.opts.keep_versions = False
        self.recording(self.dest)
        original_move = fsutil.move

        def staged_download(client, url, target, **kwargs):
            self.recording(target, b"new media")
            return HLSResult(target / "video.m3u8", 9, 1)

        def fail_publication(source, destination):
            if Path(source).name == "hls" and Path(destination) == self.dest:
                raise PermissionError("simulated replacement failure")
            return original_move(source, destination)

        with patch("ffhs_archiver.exporter.download_hls", side_effect=staged_download), \
                patch("ffhs_archiver.exporter.fsutil.move", side_effect=fail_publication):
            with self.assertRaises(PermissionError):
                self.course._download_hls_spec(self.spec, lambda *args: None)
        self.assertEqual((self.dest / "s00000.ts").read_bytes(), b"old media")
        self.assertIn("s00000.ts", (self.dest / "video.m3u8").read_text())
        self.assertFalse(list(self.dest.parent.glob(".previous-*")))
        self.assertFalse(list(self.dest.parent.glob(".recording-*")))
        self.assertNotIn(self.spec.key, self.course.state.files)

    def test_successful_replacement_preserves_old_version_when_requested(self):
        self.recording(self.dest)

        def staged_download(client, url, target, **kwargs):
            self.recording(target, b"new media")
            return HLSResult(target / "video.m3u8", 9, 1)

        with patch("ffhs_archiver.exporter.download_hls", side_effect=staged_download):
            self.course._download_hls_spec(self.spec, lambda *args: None)
        self.assertEqual(self.spec.status, "updated")
        self.assertEqual((self.dest / "s00000.ts").read_bytes(), b"new media")
        previous = list((self.root / "_versioni_precedenti").glob("hls_*"))
        self.assertEqual(len(previous), 1)
        self.assertEqual((previous[0] / "s00000.ts").read_bytes(), b"old media")
        self.assertTrue((previous[0] / "video.m3u8").is_file())


if __name__ == "__main__":
    unittest.main()

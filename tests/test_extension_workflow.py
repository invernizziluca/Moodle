"""Complete local workflow: metadata, real downloaded bytes, import and vault links.

Browser downloads are represented by real files written after the snapshot.
No installed third-party extension or authenticated FFHS session is simulated as verified.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from ffhs_archiver.appdata import Settings
from ffhs_archiver.exporter import Exporter, ExportOptions
from ffhs_archiver.extension_workflow import ExtensionWorkflow, course_targets
from ffhs_archiver.moodle import MoodleClient, READ_ONLY_FUNCTIONS
from ffhs_archiver.verify import moodle_checks
from test_export_media import FixtureOpener, COURSE, SITE, TOKEN
from verify_archive import main as verify_archive


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ffhs-workflow-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.archive = self.base / "archive"
        self.downloads = self.base / "downloads"
        self.downloads.mkdir()
        opener = FixtureOpener()
        client = MoodleClient(SITE, TOKEN, min_interval=0, retries=0)
        client._opener = opener
        client.site_info_data = {"userid": 1}
        client.functions = set(opener.api)
        with patch.object(client, "download", side_effect=AssertionError("metadata must not download files")), \
                patch("ffhs_archiver.exporter.VideoSession", side_effect=AssertionError("no auto-login")):
            result = Exporter(client, ExportOptions(dest=str(self.archive), download_files=False)).export([COURSE])
        self.assertEqual(result["results"][0]["status"], "ok", result)
        self.course = self.archive / result["results"][0]["folder"]

    def test_fetchv_opens_moodle_then_collects_video_into_explicit_flat_activity(self):
        old = self.downloads / "old.mp4"
        old.write_bytes(b"pre-existing")
        workflow = ExtensionWorkflow(self.archive, COURSE["id"], "fetchv", self.downloads, "cm:102")
        with patch("ffhs_archiver.extension_workflow.open_moodle_page", return_value=True) as browser:
            workflow.open("edge")
        browser.assert_called_once_with(SITE + "/mod/page/view.php?id=102", "edge")
        original = self.downloads / "FetchV completed.mp4"
        original.write_bytes(b"\x00\x00\x00\x18ftypmp42-video-from-fetchv")
        (self.downloads / "unfinished.mp4.crdownload").write_bytes(b"partial")
        (self.downloads / "unrelated.pdf").write_bytes(b"unrelated")
        choices = workflow.preview()
        self.assertEqual([c.path for c in choices], [original])
        result = workflow.collect(choices)
        self.assertEqual(result["new_files"], 1)
        self.assertEqual(result["matched"][0]["activity_key"], "cm:102")
        self.assertEqual(result["files"][0].read_bytes(), original.read_bytes())
        self.assertIn("fetchv", result["files"][0].parts)
        self.assertTrue(original.is_file())
        self.assertNotIn("index_warning", result)
        self.assertIn("FetchV", (self.course / "download_estensioni.md").read_text())
        again = workflow.collect(choices)
        self.assertEqual(again["new_files"], 0)
        self.assertEqual(again["files"], result["files"])
        self.assertEqual(verify_archive(self.archive), 0)

    def test_buddy_zip_and_documents_are_real_files_with_context_and_uncertainty(self):
        workflow = ExtensionWorkflow(self.archive, COURSE["id"], "moodle_buddy", self.downloads)
        folder = self.downloads / "Course" / "Section"
        folder.mkdir(parents=True)
        source = folder / "lecture.pdf"
        source.write_bytes(b"%PDF-1.4-local-file")
        bundle = folder / "attachments.zip"
        with ZipFile(bundle, "w") as zip_file:
            zip_file.writestr("Exercises/unmatched.docx", b"document in a real ZIP")
        candidates = workflow.preview()
        result = workflow.collect(candidates)
        self.assertEqual(result["new_files"], 2)
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(len(result["unmatched"]), 1)
        self.assertIn("Course/Section/attachments/Exercises/unmatched.docx", result["report_path"].read_text())
        self.assertEqual({p.read_bytes() for p in result["files"]},
                         {b"%PDF-1.4-local-file", b"document in a real ZIP"})
        self.assertTrue(source.is_file())
        self.assertTrue(bundle.is_file())
        self.assertEqual(verify_archive(self.archive), 0)

    def test_existing_downloads_can_be_selected_without_redownloading(self):
        original = self.downloads / "lecture.pdf"
        original.write_bytes(b"PDF downloaded before app session")
        workflow = ExtensionWorkflow(self.archive, COURSE["id"], "moodle_buddy", self.downloads)
        self.assertEqual(workflow.preview(), [])
        selected = workflow.review_existing([original])
        self.assertEqual(workflow.collect(selected)["files"][0].read_bytes(), original.read_bytes())

    def test_fetchv_preview_and_import_accept_the_same_final_media_formats(self):
        workflow = ExtensionWorkflow(self.archive, COURSE["id"], "fetchv", self.downloads, "cm:102")
        expected = {}
        for name in ("lecture.mp4", "lecture.wmv", "lecture.mpeg", "audio.m4a", "audio.mp3", "audio.wav"):
            expected[name] = name.encode()
            (self.downloads / name).write_bytes(expected[name])
        (self.downloads / "segment.ts").write_bytes(b"streaming fragment")
        candidates = workflow.preview()
        self.assertEqual({candidate.path.name for candidate in candidates}, set(expected))
        result = workflow.collect(candidates)
        self.assertEqual({path.read_bytes() for path in result["files"]}, set(expected.values()))
        self.assertEqual(result["new_files"], len(expected))
        self.assertTrue(all((self.downloads / name).is_file() for name in expected))

    def test_changed_download_after_preview_is_not_imported(self):
        workflow = ExtensionWorkflow(self.archive, COURSE["id"], "fetchv", self.downloads, "cm:101")
        source = self.downloads / "lecture.mp4"
        source.write_bytes(b"first")
        selected = workflow.preview()
        source.write_bytes(b"still downloading")
        with self.assertRaisesRegex(ValueError, "cambiato"):
            workflow.collect(selected)
        self.assertFalse(list(self.course.rglob("fetchv_imports.json")))

    def test_manual_buddy_association_is_explicit_and_youtube_is_not_a_fetchv_target(self):
        structure_path = self.course / "course_structure.json"
        structure = json.loads(structure_path.read_text())
        structure["sections"][0]["children"][0]["links"] = [{"url": "https://youtu.be/abcdefghijk"}]
        structure_path.write_text(json.dumps(structure))
        self.assertTrue(next(t for t in course_targets(self.archive, COURSE["id"]) if t["key"] == "cm:101")["youtube_only"])
        with self.assertRaisesRegex(ValueError, "YouTube"):
            ExtensionWorkflow(self.archive, COURSE["id"], "fetchv", self.downloads, "cm:101")
        workflow = ExtensionWorkflow(self.archive, COURSE["id"], "moodle_buddy", self.downloads, "cm:102")
        source = self.downloads / "a renamed handout.pdf"
        source.write_bytes(b"handout")
        result = workflow.collect(workflow.preview())
        self.assertEqual(result["matched"][0]["activity_key"], "cm:102")
        self.assertIn("Associati dall'utente", result["report_path"].read_text())

    def test_regular_verification_does_not_request_a_video_auto_login(self):
        from unittest.mock import Mock
        client = Mock()
        client.public_config.return_value = {"enablewebservices": 1, "enablemobilewebservice": 1}
        info = {"userid": 1, "functions": [{"name": fn} for fn in READ_ONLY_FUNCTIONS]}
        client.site_info.return_value = info
        client.site_info_data = info
        client.call.return_value = [dict(COURSE)]
        with patch("ffhs_archiver.verify._video_check", side_effect=AssertionError("must not probe Kaltura")):
            checks, _ = moodle_checks(client, Mock(privatetoken="must-not-be-used"))
        self.assertTrue(any(c.status == "[INFO]" and "FetchV" in c.detail for c in checks))


class MigrationTests(unittest.TestCase):
    def test_existing_login_options_do_not_reenable_auto_login_on_upgrade(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"FFHS_ARCHIVER_HOME": home}):
            Path(home, "settings.json").write_text(json.dumps({"settings_version": 2, "download_videos": True,
                                                              "capture_pages": True, "include_videos": True}))
            settings = Settings.load()
            self.assertFalse(settings.download_videos)
            self.assertFalse(settings.capture_pages)
            self.assertTrue(settings.include_videos)


if __name__ == "__main__":
    unittest.main()

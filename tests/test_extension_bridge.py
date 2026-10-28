"""Real file-session checks for the actual FetchV/Moodle Buddy workflow, no browser/network."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ffhs_archiver.extension_bridge import (
    DownloadCandidate, DownloadSession, EXTENSIONS, is_youtube_url, open_moodle_page, safe_moodle_url,
)


class DownloadSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffhs-extension-session-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_new_nested_moodle_buddy_download_and_changed_file_only(self):
        old = self.root / "old.pdf"
        old.write_bytes(b"old")
        changed = self.root / "updated.pdf"
        changed.write_bytes(b"one")
        session = DownloadSession(self.root)
        changed.write_bytes(b"updated-content")
        nested = self.root / "HRM" / "Vorbereitung" / "file.pdf"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"new-document")
        result = session.candidates()
        self.assertEqual([item.relative_path for item in result], ["HRM/Vorbereitung/file.pdf", "updated.pdf"])
        self.assertEqual(session.validate_candidates(result), [nested, changed])
        self.assertEqual(old.read_bytes(), b"old")

    def test_hidden_temporary_and_symlink_entries_are_excluded(self):
        session = DownloadSession(self.root)
        for name in ("v.mp4.crdownload", "x.part", "a.tmp", "f.download", ".hidden.mp4"):
            (self.root / name).write_bytes(b"unfinished")
        (self.root / ".hidden").mkdir()
        (self.root / ".hidden" / "secret.txt").write_text("not-a-download")
        try:
            (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        except OSError:
            pass
        self.assertEqual(session.candidates(), [])

    def test_changes_since_preview_and_forged_candidates_are_rejected(self):
        session = DownloadSession(self.root)
        video = self.root / "video.mp4"
        video.write_bytes(b"first")
        candidates = session.candidates()
        video.write_bytes(b"still-downloading")
        with self.assertRaisesRegex(ValueError, "cambiato"):
            session.validate_candidates(candidates)
        real = session.candidates()
        forged = DownloadCandidate(self.root / "outside.mp4", "video.mp4", real[0].size, real[0].mtime_ns)
        with self.assertRaises(ValueError):
            session.validate_candidates([forged])
        self.assertEqual(session.validate_candidates(real), [video])
        video.unlink()
        with self.assertRaises(ValueError):
            session.validate_candidates(real)

    def test_partial_rename_is_detected_as_a_new_completed_looking_file(self):
        partial = self.root / "video.mp4.crdownload"
        partial.write_bytes(b"partial")
        session = DownloadSession(self.root)
        final = partial.with_suffix("")
        partial.rename(final)
        candidates = session.candidates()
        self.assertEqual([item.path for item in candidates], [final])

    def test_walk_limits_fail_explicitly_instead_of_returning_partial_results(self):
        for n in range(3):
            (self.root / f"{n}.pdf").write_bytes(b"file")
        with self.assertRaisesRegex(ValueError, "Troppi file"):
            DownloadSession(self.root, max_files=2)
        session = DownloadSession(self.root, max_files=3)
        (self.root / "new.pdf").write_bytes(b"new")
        with self.assertRaises(ValueError):
            session.candidates()
        with self.assertRaisesRegex(ValueError, "Troppi elementi"):
            DownloadSession(self.root, max_entries=1)

    def test_depth_bound_is_explicit_and_empty_or_duplicate_selections_rejected(self):
        session = DownloadSession(self.root)
        video = self.root / "v.mp4"
        video.write_bytes(b"video")
        result = session.candidates()
        with self.assertRaises(ValueError):
            session.validate_candidates([])
        with self.assertRaises(ValueError):
            session.validate_candidates([*result, *result])
        (self.root / "deep").mkdir()
        with self.assertRaisesRegex(ValueError, "profonda"):
            DownloadSession(self.root, max_depth=0)


class BrowserLaunchTests(unittest.TestCase):
    def test_official_metadata_and_moodle_canonicalization(self):
        self.assertEqual(EXTENSIONS["fetchv"]["chrome_id"], "nfmmmhanepmpifddlkkmihkalkoekpfd")
        self.assertEqual(EXTENSIONS["moodle_buddy"]["website"], "https://moodlebuddy.com/")
        self.assertEqual(safe_moodle_url("https://moodle.ffhs.ch/mod/kalvidres/view.php?id=012&token=secret#x"),
                         "https://moodle.ffhs.ch/mod/kalvidres/view.php?id=12")
        for bad in ("https://moodle.ffhs.ch.evil.test/course/view.php?id=1", "javascript:alert(1)",
                    "http://moodle.ffhs.ch/course/view.php?id=1", "https://moodle.ffhs.ch/course/view.php?id=1&id=2",
                    "https://moodle.ffhs.ch/login/index.php", "https://user@moodle.ffhs.ch/course/view.php?id=1",
                    "https://moodle.ffhs.ch/mod/not-a-module/view.php?id=1"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                safe_moodle_url(bad)

    def test_launch_uses_normal_browser_without_shell(self):
        url = "https://moodle.ffhs.ch/course/view.php?id=42"
        with patch("ffhs_archiver.extension_bridge.os.name", "nt"), \
                patch("ffhs_archiver.extension_bridge.os.startfile", create=True) as start:
            self.assertTrue(open_moodle_page(url))
            start.assert_called_once_with("microsoft-edge:" + url)
        with patch("ffhs_archiver.extension_bridge.webbrowser.open", return_value=True) as browser:
            self.assertTrue(open_moodle_page(url, "default"))
            browser.assert_called_once_with(url, new=2)
        with self.assertRaises(ValueError):
            open_moodle_page(url, "unrecognized")

    def test_youtube_page_and_cdn_are_links_only(self):
        for raw in ("https://youtu.be/123", "https://www.youtube.com/watch?v=1",
                    "https://r3.googlevideo.com/video", "https://youtube-nocookie.com/embed/x"):
            self.assertTrue(is_youtube_url(raw))
        self.assertFalse(is_youtube_url("https://youtube.com.evil.test/video"))
        self.assertFalse(is_youtube_url("https://moodle.ffhs.ch/mod/page/view.php?id=1"))


if __name__ == "__main__":
    unittest.main()

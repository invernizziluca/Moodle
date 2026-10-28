"""Readable-naming, course-folder, missing-grouping and app-UA behaviour (pure, no network)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffhs_archiver.exporter import CourseExport, Exporter, Item  # noqa: E402
from ffhs_archiver.moodle import MOODLE_APP_UA  # noqa: E402
from ffhs_archiver.naming import readable, strip_section_number  # noqa: E402


class ReadableNameTests(unittest.TestCase):
    def test_keeps_umlauts_and_spaces(self):
        self.assertEqual(readable("Überblick HRM, Organisation und Strategie"),
                         "Überblick HRM, Organisation und Strategie")

    def test_removes_windows_invalid_chars(self):
        out = readable('Quiz 1: Frage? <wichtig> "Teil"/A')
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, out)
        self.assertTrue(out)

    def test_no_trailing_dot_or_space(self):
        self.assertFalse(readable("Kapitel 1. ").endswith((".", " ")))

    def test_truncates_on_word_boundary_with_ellipsis(self):
        out = readable("Ein sehr langer Titel der definitiv abgeschnitten werden muss hier", 30)
        self.assertLessEqual(len(out), 31)
        self.assertTrue(out.endswith("…"))

    def test_reserved_name_is_escaped(self):
        self.assertTrue(readable("CON").upper().startswith("CON_"))

    def test_strip_section_number(self):
        self.assertEqual(strip_section_number("1 Entscheidungen: Homo Oeconomicus"),
                         "Entscheidungen: Homo Oeconomicus")
        self.assertEqual(strip_section_number("Modulinformationen"), "Modulinformationen")
        # a number that is part of the real title (no following space-word boundary issue) is kept when bare
        self.assertEqual(strip_section_number("2024 Rückblick"), "2024 Rückblick")  # a year is not a section no.


class CourseFolderNameTests(unittest.TestCase):
    def test_strips_admin_tail(self):
        name = Exporter._course_folder_name(
            "BWL8, Decision Making and Change Management, BOEK-A-M014, OL-Mo-5, HS26/27, Stärkle Pamela",
            "BOEK-A-M014.BWL8", 15195)
        self.assertEqual(name, "BWL8 — Decision Making and Change Management")

    def test_short_name_fallback(self):
        self.assertTrue(Exporter._course_folder_name("", "KURS", 7))


class MissingKindTests(unittest.TestCase):
    def _item(self, modname=""):
        return Item(kind="activity", key="k", title="t", modname=modname)

    def test_external_reading_is_esterno(self):
        self.assertEqual(CourseExport._missing_kind(self._item("url"),
                         "Lettura o risorsa esterna: si apre dal link"), "esterno")

    def test_external_video_is_video(self):
        self.assertEqual(CourseExport._missing_kind(self._item("url"),
                         "Video esterno (si guarda online su YouTube)"), "video")

    def test_library_keyword_is_esterno(self):
        self.assertEqual(CourseExport._missing_kind(self._item("page"),
                         "Libro della biblioteca virtuale"), "esterno")


class AppUserAgentTests(unittest.TestCase):
    def test_identifies_as_moodle_app(self):
        # Moodle's autologin endpoint is served only to clients whose UA contains "MoodleMobile".
        self.assertIn("MoodleMobile", MOODLE_APP_UA)

    def test_names_this_tool_honestly(self):
        self.assertIn("FFHS-Moodle-Archiver", MOODLE_APP_UA)


if __name__ == "__main__":
    unittest.main(verbosity=2)

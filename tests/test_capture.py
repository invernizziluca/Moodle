"""The login-only page capture (e.g. the FFHS ffhssyllabus Modulplan).

Exercises CourseExport._capture_page in isolation: with the autologin web session it saves the page's
main content (and nothing from the surrounding navigation/footer); without it, the page is flagged with
a helpful hint instead of being silently dropped.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffhs_archiver.exporter import CAPTURE_TIP, CourseExport, ExportOptions, Item  # noqa: E402
from ffhs_archiver.html2md import Converter  # noqa: E402

# A realistic Moodle Boost page: nav + #region-main (notifications, activity header, role=main) + footer.
BOOST = """<html><body><nav class="navbar">MENU Dashboard</nav>
<div id="region-main"><span class="notifications" id="user-notifications"></span>
<span id="maincontent" tabindex="-1"></span>
<div class="activity-header" data-for="page-activity-header">
  <div data-region="activity-information" data-activityname="Modulplan"></div></div>
<div role="main"><div class="box py-3 generalbox"><div class="no-overflow">
<h3>Lernziele</h3><p>Die Studierenden verstehen <strong>Entscheidungen</strong> und Change Management.</p>
<h3>Literatur</h3>
<ul><li>[1] Core ECON, The Economy</li><li>[2] Economy, Society, and Public Policy</li></ul>
<h3>Studienzeiten</h3><p>ca. 150 Stunden</p>
</div></div></div></div><footer id="page-footer">© 2026 FFHS · Moodle Docs</footer></body></html>"""


class _Session:
    def __init__(self, html):
        self._html = html
        self.asked = []

    def get_text(self, url):
        self.asked.append(url)
        return self._html


class _Exporter:
    def __init__(self, session, reason=""):
        self._session = session
        self.web_reason = reason

    def web_session(self):
        return self._session


def _course_export(exporter, opts):
    ce = CourseExport.__new__(CourseExport)   # bypass the disk/network __init__
    ce.x = exporter
    ce.opts = opts
    return ce


def _item():
    return Item("activity", "cm:1", "Modulplan", modname="ffhssyllabus",
                url="https://moodle.ffhs.ch/mod/ffhssyllabus/view.php?id=5323569",
                raw={"modplural": "Modulpläne"})


def _conv(item):
    return Converter(base_url=item.url, is_site_url=lambda u: True)


class CapturePageTests(unittest.TestCase):
    def test_captures_only_the_main_content(self):
        sess = _Session(BOOST)
        ce = _course_export(_Exporter(sess), ExportOptions(dest="/tmp/x", capture_pages=True, private_token="t"))
        item = _item()
        ce._capture_page(item, {}, _conv(item))
        self.assertEqual(sess.asked, [item.url])
        self.assertIn("Lernziele", item.body)
        self.assertIn("Literatur", item.body)
        self.assertIn("Core ECON", item.body)
        self.assertIn("150 Stunden", item.body)
        self.assertNotIn("Dashboard", item.body)   # nav not captured
        self.assertNotIn("Moodle Docs", item.body)  # footer not captured
        self.assertEqual(item.missing, [])          # captured, so not flagged as missing
        self.assertTrue(any("Modulplan catturato" in v for _, v in item.facts))

    def test_without_session_is_flagged_with_hint(self):
        ce = _course_export(_Exporter(None, reason="serve l'accesso avanzato"),
                            ExportOptions(dest="/tmp/x", capture_pages=True, private_token=""))
        item = _item()
        ce._capture_page(item, {}, _conv(item))
        self.assertIn(CAPTURE_TIP, item.body)
        self.assertTrue(any("non salvata" in reason for reason, _ in item.missing))

    def test_capture_disabled_does_not_open_session(self):
        sess = _Session(BOOST)
        ce = _course_export(_Exporter(sess), ExportOptions(dest="/tmp/x", capture_pages=False))
        item = _item()
        ce._capture_page(item, {}, _conv(item))
        self.assertEqual(sess.asked, [])            # capture off: never opened the session
        self.assertTrue(any("non attiva" in reason for reason, _ in item.missing))

    def test_unrecognised_page_is_flagged(self):
        ce = _course_export(_Exporter(_Session("<html><body>login required</body></html>")),
                            ExportOptions(dest="/tmp/x", capture_pages=True, private_token="t"))
        item = _item()
        ce._capture_page(item, {}, _conv(item))
        self.assertTrue(any("non riconosciuto" in reason for reason, _ in item.missing))


class ExtractRegionMainTests(unittest.TestCase):
    def test_prefers_role_main_inside_region(self):
        from ffhs_archiver.html2md import extract_region_main
        html = ('<div id="region-main"><div class="activity-header">HEADER</div>'
                '<div role="main"><p>Echter <b>Inhalt</b></p></div></div>')
        out = extract_region_main(html)
        self.assertIn("Echter", out)
        self.assertNotIn("HEADER", out)

    def test_balanced_nested_divs(self):
        from ffhs_archiver.html2md import extract_region_main
        html = ('<section id="region-main"><div class="a"><div class="b">X</div></div></section>'
                '<div class="after">Y</div>')
        out = extract_region_main(html)
        self.assertIn("X", out)
        self.assertNotIn("Y", out)

    def test_falls_back_to_role_main_without_region(self):
        from ffhs_archiver.html2md import extract_region_main
        self.assertIn("Z", extract_region_main('<main role="main"><p>Z</p></main>'))

    def test_empty_when_no_main_region(self):
        from ffhs_archiver.html2md import extract_region_main
        self.assertEqual(extract_region_main("<html><body><p>nope</p></body></html>"), "")
        self.assertEqual(extract_region_main(""), "")

    def test_strips_scripts(self):
        from ffhs_archiver.html2md import extract_region_main
        out = extract_region_main('<div role="main"><script>evil()</script><p>ok</p></div>')
        self.assertNotIn("evil", out)
        self.assertIn("ok", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Unit tests (standard library unittest; no network)."""

import base64
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffhs_archiver.exporter import first_line, normalize_heading_levels  # noqa: E402
from ffhs_archiver.html2md import (Converter, classify_media_url, html_to_text, quiz_question_to_markdown,  # noqa: E402
                                   shift_headings, to_markdown, unescape_name)
from ffhs_archiver.moodle import (MoodleClient, MoodleError, encode_params, normalize_site_url,  # noqa: E402
                                  redact)
from ffhs_archiver.naming import UniqueNames, human_size, rel_link, safe_filename, slug  # noqa: E402
from ffhs_archiver.sso import SSOError, extract_token, launch_url, oauth_issuer_from_providers  # noqa: E402

SITE = "https://moodle.ffhs.ch"


class Html2MdTests(unittest.TestCase):
    def test_headings_paragraphs_emphasis(self):
        md = to_markdown("<h2>Titel</h2><p>Das <strong> HRM </strong>ist <em>wichtig</em>.</p>")
        self.assertEqual(md, "## Titel\n\nDas **HRM** ist *wichtig*.")

    def test_nested_and_ordered_lists(self):
        md = to_markdown("<ol start='3'><li>A<ul><li>a1</li><li>a2</li></ul></li><li>B</li></ol>")
        self.assertEqual(md, "3. A\n   - a1\n   - a2\n4. B")

    def test_malformed_html(self):
        md = to_markdown("<p>Eins<p>Zwei<ul><li>x<li>y</ul>Ende")
        self.assertEqual(md, "Eins\n\nZwei\n\n- x\n- y\n\nEnde")

    def test_table_with_pipe_and_colspan(self):
        md = to_markdown("<table><tr><th>A</th><th>B</th></tr><tr><td colspan='2'>x | y</td></tr></table>")
        self.assertEqual(md, "| A | B |\n| --- | --- |\n| x \\| y |  |")

    def test_layout_table_single_column(self):
        self.assertEqual(to_markdown("<table><tr><td><p>Nur Text</p></td></tr></table>"), "Nur Text")

    def test_links_and_moodle_autolinks(self):
        conv = Converter(base_url="https://moodle.ffhs.ch/mod/page/view.php?id=1")
        md = conv.convert('<p><a href="view.php?id=2">Seite</a> <a class="autolink" href="x">Glossar</a> '
                          '<a href="javascript:void(0)">JS</a></p>')
        self.assertEqual(md, "[Seite](https://moodle.ffhs.ch/mod/page/view.php?id=2) Glossar JS")

    def test_link_resolver_and_url_quoting(self):
        conv = Converter(resolve_link=lambda url: "../Datei (2).pdf" if url.endswith("x.pdf") else None)
        self.assertEqual(conv.convert('<a href="https://h/x.pdf">PDF</a>'), "[PDF](../Datei%20%282%29.pdf)")

    def test_images_icons_and_resolver(self):
        conv = Converter(resolve_file=lambda url, label, kind: "_media/a.png")
        md = conv.convert('<p><img src="https://m/theme/image.php/boost/core/1/i/flag"><img src="https://m/a.png" '
                          'alt="Bild [1]"></p>')
        self.assertEqual(md, "![Bild (1)](_media/a.png)")

    def test_iframes_become_canonical_links(self):
        seen = []
        conv = Converter(on_embed=lambda u, t, p: seen.append((u, p)))
        md = conv.convert('<iframe src="https://www.youtube.com/embed/abcdefghijk" title="Clip"></iframe>'
                          '<iframe src="https://tube.switch.ch/embed/xyz12"></iframe>')
        self.assertIn("[Video (YouTube): Clip](https://www.youtube.com/watch?v=abcdefghijk)", md)
        self.assertIn("(https://tube.switch.ch/videos/xyz12)", md)
        self.assertEqual([p for _, p in seen], ["YouTube", "SWITCHtube"])

    def test_video_tag_uses_file_resolver(self):
        conv = Converter(resolve_file=lambda url, label, kind: f"_media/{kind}.mp4")
        md = conv.convert('<video><source src="https://m/pluginfile.php/1/v.mp4">Kurzvideo</video>')
        self.assertEqual(md, "[Video: Kurzvideo](_media/video.mp4)")

    def test_escaping_and_line_starts(self):
        md = to_markdown("<p>5 * 3 und datei_name, _wichtig_</p><p># keine Überschrift</p><p>1. kein Listenpunkt</p>")
        self.assertEqual(md, "5 \\* 3 und datei_name, \\_wichtig\\_\n\n\\# keine Überschrift\n\n\\1. kein Listenpunkt")

    def test_br_blockquote_pre(self):
        md = to_markdown("<p>Zeile 1<br>Zeile 2</p><blockquote><p>Zitat</p></blockquote><pre>a  b\n c</pre>")
        self.assertEqual(md, "Zeile 1\nZeile 2\n\n> Zitat\n\n```\na  b\n c\n```")

    def test_hidden_content_skipped(self):
        md = to_markdown('<p>A<span class="accesshide">versteckt</span><span style="display: none">x</span></p>'
                         '<script>alert(1)</script><div hidden>y</div>')
        self.assertEqual(md, "A")

    def test_entities_and_nbsp(self):
        self.assertEqual(to_markdown("<p>Video&nbsp;&amp;&nbsp;Test&#8203;</p>"), "Video & Test")
        self.assertEqual(unescape_name("Video &amp; Test"), "Video & Test")
        self.assertEqual(html_to_text("<b>3</b>&nbsp;h"), "3 h")

    def test_quiz_question(self):
        html = ('<div class="que multichoice correct"><div class="info"><h3 class="no">Frage <span class="qno">1'
                '</span></h3><div class="state">Richtig</div><div class="grade">Erreichte Punkte 1,00 von 1,00</div>'
                '<div class="questionflag">Markieren</div></div><div class="content"><div class="formulation">'
                '<h4 class="accesshide">Fragetext</h4><div class="qtext"><p>Was ist HRM?</p></div><div class="ablock">'
                '<div class="answer"><div class="r0"><input type="radio" disabled><div><span class="answernumber">a. '
                '</span>Falsch</div></div><div class="r1 correct"><input type="radio" checked="checked"><div>'
                '<span class="answernumber">b. </span>Richtig</div><i class="icon fa-check" title="Richtig"></i>'
                '<div class="specificfeedback">Gut.</div></div></div></div></div><div class="outcome"><div '
                'class="feedback"><div class="rightanswer">Die richtige Antwort ist: Richtig</div></div></div></div>'
                '</div><script>var x;</script>')
        md = quiz_question_to_markdown(html, Converter())
        self.assertEqual(md, "#### Domanda 1 — Richtig · Erreichte Punkte 1,00 von 1,00\n\nWas ist HRM?\n\n"
                             "- ☐ a. Falsch\n- ☑ b. Richtig ✓ (corretta)\n  - *Feedback:* Gut.\n\n"
                             "**Soluzione:** Die richtige Antwort ist: Richtig")

    def test_shift_and_normalize_headings(self):
        self.assertEqual(shift_headings("# A\n```\n# code\n```\n## B", 2), "### A\n```\n# code\n```\n#### B")
        self.assertEqual(normalize_heading_levels("#### A\n##### B", 2, exact=True), "## A\n### B")
        self.assertEqual(normalize_heading_levels("#### A", 2), "#### A")

    def test_classify_media(self):
        self.assertEqual(classify_media_url("https://youtu.be/abcdefghijk")[0], "YouTube")
        self.assertEqual(classify_media_url("https://example.org/x")[0], "")


class NamingTests(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(slug("Thema 1: Video & Test zu den Grundlagen"), "Thema_1_Video_und_Test_zu_den_Grundlagen")
        self.assertEqual(slug("Überblick HRM, Organisation"), "Ueberblick_HRM_Organisation")
        self.assertTrue(len(slug("x" * 30 + " " + "y" * 30, 40)) <= 40)
        self.assertEqual(slug("CON"), "CON_")
        self.assertEqual(slug("   "), "ohne_Titel")

    def test_safe_filename(self):
        self.assertEqual(safe_filename('Bericht: "Q1"/2026?.pdf'), "Bericht_ _Q1__2026_.pdf")
        self.assertEqual(safe_filename("nul.txt"), "nul_.txt")
        self.assertEqual(safe_filename("Vorlage (Version 2).docx"), "Vorlage (Version 2).docx")
        long = safe_filename("a" * 200 + ".pdf", 50)
        self.assertTrue(long.endswith(".pdf") and len(long) <= 50)

    def test_unique_names_case_insensitive(self):
        u = UniqueNames()
        self.assertEqual([u.take("A.pdf"), u.take("a.pdf"), u.take("A.pdf")], ["A.pdf", "a (2).pdf", "A (3).pdf"])

    def test_rel_link_and_size(self):
        self.assertEqual(rel_link("K/S/A/A.md", "K/S/B/B (1).pdf"), "../B/B%20%281%29.pdf")
        self.assertEqual(human_size(1536), "1.5 KB")


class SsoTests(unittest.TestCase):
    def link(self, passport: str, token: str = "a" * 32, private: str = "", site: str = SITE) -> str:
        sig = hashlib.md5((site + passport).encode()).hexdigest()
        payload = f"{sig}:::{token}" + (f":::{private}" if private else "")
        return "moodlemobile://token=" + base64.b64encode(payload.encode()).decode()

    def test_valid_link(self):
        res = extract_token(self.link("p1"), SITE, ["p0", "p1"])
        self.assertEqual((res.token, res.signature_ok), ("a" * 32, True))

    def test_link_variants(self):
        raw = self.link("p1", private="x" * 10)
        for variant in (raw + "/", raw.replace("=", "%3D"), raw.rstrip("="), "  " + raw + "\n",
                        raw.split("://", 1)[1]):
            self.assertEqual(extract_token(variant, SITE, ["p1"]).token, "a" * 32, variant)

    def test_wrong_passport_or_site(self):
        self.assertFalse(extract_token(self.link("other"), SITE, ["p1"]).signature_ok)
        self.assertFalse(extract_token(self.link("p1", site="https://evil.example"), SITE, ["p1"]).signature_ok)

    def test_bare_token_and_garbage(self):
        self.assertIsNone(extract_token("0123456789abcdef0123456789ABCDEF", SITE, []).signature_ok)
        with self.assertRaises(SSOError):
            extract_token("hello", SITE, ["p"])

    def test_launch_url_and_issuer(self):
        url = launch_url(SITE, "pp", 1)
        self.assertTrue(url.startswith(SITE + "/admin/tool/mobile/launch.php?"))
        for part in ("service=moodle_mobile_app", "passport=pp", "urlscheme=moodlemobile", "confirmed=1",
                     "oauthsso=1"):
            self.assertIn(part, url)
        providers = [{"name": "SWITCHaai", "url": SITE + "/auth/shibboleth/index.php"},
                     {"name": "FFHS-ID", "url": SITE + "/auth/oauth2/login.php?id=1&sesskey=abc"}]
        self.assertEqual(oauth_issuer_from_providers(providers), 1)


class MoodleClientTests(unittest.TestCase):
    def setUp(self):
        import os
        self._dev = os.environ.pop("FFHS_ARCHIVER_DEV_SITE", None)

    def tearDown(self):
        import os
        if self._dev:
            os.environ["FFHS_ARCHIVER_DEV_SITE"] = self._dev

    def test_encode_params(self):
        self.assertEqual(encode_params({"courseids": [5], "options": [{"name": "x", "value": True}], "n": None}),
                         [("courseids[0]", "5"), ("options[0][name]", "x"), ("options[0][value]", "1")])

    def test_normalize_site_url(self):
        self.assertEqual(normalize_site_url("moodle.ffhs.ch/"), SITE)
        self.assertEqual(normalize_site_url("https://Moodle.FFHS.ch/login/index.php"), SITE)

    def test_redact(self):
        self.assertEqual(redact("x?token=abc&y=1 wstoken=zzz"), "x?token=***&y=1 wstoken=***")

    def test_file_urls_only_for_site(self):
        c = MoodleClient(SITE, "t" * 32)
        url = c.tokenized_file_url(SITE + "/pluginfile.php/12/mod_resource/content/3/a%20b.pdf?forcedownload=1&token=x")
        self.assertEqual(url, SITE + "/webservice/pluginfile.php/12/mod_resource/content/3/a%20b.pdf?"
                              "forcedownload=1&token=" + "t" * 32)
        self.assertTrue(c.tokenized_file_url(SITE + "/tokenpluginfile.php/abc/12/x/y/0/f.pdf").startswith(
            SITE + "/webservice/pluginfile.php/12/x/y/0/f.pdf?token="))
        with self.assertRaises(MoodleError):
            c.tokenized_file_url("https://evil.example/pluginfile.php/1/a.pdf")
        with self.assertRaises(MoodleError):
            c.tokenized_file_url(SITE + "/mod/page/view.php?id=1")
        self.assertEqual(c.file_key(SITE + "/webservice/pluginfile.php/12/mod_page/content/0/a%20b.png?x=1"),
                         "12/mod_page/content/0/a b.png")


class ExporterHelperTests(unittest.TestCase):
    def test_first_line(self):
        self.assertEqual(first_line("![x](a.png) **Zeitaufwand:** [3 h](@@FILE:1@@) (K2)\n\nmehr"),
                         "Zeitaufwand: 3 h (K2)")
        self.assertEqual(first_line("- *Studienzeit: 1 h*"), "Studienzeit: 1 h")


if __name__ == "__main__":
    unittest.main()

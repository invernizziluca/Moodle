"""Tests for the FFHS lecture-video download (Kaltura/SWITCHcast).

The pure parsing and the download orchestration are tested here with a fake session, so no network
and no real Kaltura are needed. The live autologin + Kaltura path depends on the real FFHS/SWITCH
infrastructure and is exercised only on the user's machine (see the Verifica accesso probe).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffhs_archiver import kaltura as K  # noqa: E402
from ffhs_archiver.moodle import MoodleClient, VideoSession  # noqa: E402

LAUNCH = ("https://moodle.ffhs.ch/filter/kaltura/lti_launch.php?courseid=0&width=576&source="
          "https%3A%2F%2Fkaf.moodle.ffhs.ch%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F0_mtultv7g%2F"
          "showDescription%2Ffalse%2FplayerSkin%2F23448994%2F")

PLAYER_HTML = ('<html><script>var kw = {"partner_id":4357,"ks":"djJ8NDM1N3xABCDEFGHIJKLMNOPqrstuvwx",'
               '"serviceUrl":"https://cdnapisec.kaltura.com","entryId":"0_mtultv7g"};</script></html>')
FORM_HTML = ('<html><body><form action="https://kaf.moodle.ffhs.ch/hosted/index/lti-launch" method="post">'
             '<input type="hidden" name="oauth_consumer_key" value="ffhs">'
             '<input type="hidden" name="oauth_signature" value="a&amp;b"></form>'
             '<script>document.forms[0].submit();</script></body></html>')


class ParseTests(unittest.TestCase):
    def test_launch_url_parsed(self):
        ref = K.parse_kaltura(LAUNCH, "Podcast 1")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.entry_id, "0_mtultv7g")
        self.assertEqual(ref.kaf_host, "kaf.moodle.ffhs.ch")
        self.assertTrue(ref.launch_url.endswith("%2F"))
        self.assertEqual(ref.title, "Podcast 1")

    def test_third_party_is_not_kaltura(self):
        for url in ("https://www.youtube.com/watch?v=abcdef12", "https://vimeo.com/12345",
                    "https://tube.switch.ch/videos/abcd", "https://moodle.ffhs.ch/mod/page/view.php?id=1"):
            self.assertIsNone(K.parse_kaltura(url), url)

    def test_direct_kaf_url(self):
        ref = K.parse_kaltura("https://kaf.moodle.ffhs.ch/browseandembed/index/media/entryid/1_abcd1234/x")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.entry_id, "1_abcd1234")
        self.assertEqual(ref.launch_url, "")  # no Moodle launch URL: will be flagged, not downloaded

    def test_autosubmit_form(self):
        action, fields = K.parse_autosubmit_form(FORM_HTML, "https://moodle.ffhs.ch/x")
        self.assertEqual(action, "https://kaf.moodle.ffhs.ch/hosted/index/lti-launch")
        self.assertEqual(fields["oauth_consumer_key"], "ffhs")
        self.assertEqual(fields["oauth_signature"], "a&b")  # HTML entity decoded

    def test_player_config(self):
        cfg = K.extract_player_config(PLAYER_HTML)
        self.assertEqual(cfg["partner_id"], "4357")
        self.assertEqual(cfg["ks"], "djJ8NDM1N3xABCDEFGHIJKLMNOPqrstuvwx")
        self.assertEqual(cfg["service_url"], "https://cdnapisec.kaltura.com")

    def test_playmanifest_url(self):
        url = K.playmanifest_url("https://cdnapisec.kaltura.com", "4357", "0_mtultv7g", "url", "KS")
        self.assertIn("/p/4357/sp/435700/playManifest/entryId/0_mtultv7g", url)
        self.assertIn("format/url", url)
        self.assertTrue(url.endswith("ks=KS"))

    def test_playlist_parsing(self):
        master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\nlo.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=3000000\nhi.m3u8\n"
        variants = K.parse_master_playlist(master, "https://cdn/p/1/x/")
        self.assertEqual(variants[-1], (3000000, "https://cdn/p/1/x/hi.m3u8"))  # best last
        media = "#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:6,\nseg0.m4s\n#EXTINF:6,\nseg1.m4s\n#EXT-X-ENDLIST\n"
        init, segs = K.parse_media_playlist(media, "https://cdn/p/1/x/hi.m3u8")
        self.assertEqual(init, "https://cdn/p/1/x/init.mp4")
        self.assertEqual(segs, ["https://cdn/p/1/x/seg0.m4s", "https://cdn/p/1/x/seg1.m4s"])


class FakeSession:
    """A stand-in for moodle.VideoSession that answers canned pages, with no network."""

    def __init__(self, media_ok: bool):
        self.available = True
        self.reason = ""
        self.media_ok = media_ok
        self.calls = []

    def ensure_session(self):
        return True

    def get_text(self, url):
        self.calls.append(url)
        if "lti_launch" in url:
            return FORM_HTML
        if "media.m3u8" in url:  # the variant playlist (checked first: its URL also contains 'applehttp')
            return "#EXTM3U\n#EXTINF:6,\nseg0.ts\n#EXTINF:6,\nseg1.ts\n#EXT-X-ENDLIST\n"
        if "applehttp" in url:
            return "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=3000000\nmedia.m3u8\n"
        return PLAYER_HTML

    def post_text(self, url, fields):
        self.calls.append(("POST", url))
        return PLAYER_HTML

    def looks_like_media(self, url):
        return self.media_ok

    def stream(self, url, dest, progress=None):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"PROGRESSIVE-MP4-BYTES")
        if progress:
            progress(21, 21)
        return 21

    def concat(self, dest, urls, progress=None):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = b"".join(f"[{Path(u).name}]".encode() for u in urls)
        dest.write_bytes(data)
        if progress:
            progress(len(data), None)
        return len(data)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ref = K.parse_kaltura(LAUNCH, "Podcast 1")

    def test_progressive_download(self):
        kc = K.KalturaClient(FakeSession(media_ok=True))
        dest = Path(self.tmp) / "podcast.mp4"
        out = kc.download(self.ref, dest)
        self.assertEqual(out.status, "downloaded")
        self.assertEqual(out.suffix, ".mp4")
        self.assertEqual(dest.read_bytes(), b"PROGRESSIVE-MP4-BYTES")

    def test_hls_download_joins_segments(self):
        kc = K.KalturaClient(FakeSession(media_ok=False))
        dest = Path(self.tmp) / "podcast.mp4"
        out = kc.download(self.ref, dest)
        self.assertEqual(out.status, "downloaded")
        self.assertEqual(out.suffix, ".ts")  # TS segments -> .ts container
        self.assertEqual((Path(self.tmp) / "podcast.ts").read_bytes(), b"[seg0.ts][seg1.ts]")

    def test_no_launch_url_is_flagged(self):
        kc = K.KalturaClient(FakeSession(media_ok=True))
        ref = K.parse_kaltura("https://kaf.moodle.ffhs.ch/browseandembed/index/media/entryid/1_abcd1234/x")
        out = kc.download(ref, Path(self.tmp) / "x.mp4")
        self.assertEqual(out.status, "flagged")

    def test_unavailable_session_is_flagged(self):
        session = FakeSession(media_ok=True)
        session.available = False
        session.reason = "autologin non disponibile"
        out = K.KalturaClient(session).download(self.ref, Path(self.tmp) / "x.mp4")
        self.assertEqual(out.status, "flagged")
        self.assertIn("autologin", out.note)


class VideoSessionHostTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.pop("FFHS_ARCHIVER_DEV_SITE", None)

    def tearDown(self):
        if self.old:
            os.environ["FFHS_ARCHIVER_DEV_SITE"] = self.old

    def test_host_allowlist(self):
        vs = VideoSession(MoodleClient("https://moodle.ffhs.ch", "t" * 32), privatetoken="p")
        for ok in ("https://moodle.ffhs.ch/x", "https://kaf.moodle.ffhs.ch/y",
                   "https://cdnapisec.kaltura.com/p/1/a.mp4", "https://cfvod.kaltura.com/x",
                   "https://cast.switch.ch/v"):
            self.assertTrue(vs._host_ok(ok), ok)
        for bad in ("https://evil.example/x", "https://moodle.ffhs.ch.evil.com/x",
                    "http://kaltura.com.evil/x", "https://youtube.com/watch"):
            self.assertFalse(vs._host_ok(bad), bad)


class RobustExtractionTests(unittest.TestCase):
    """Real KAF/Playkit pages carry partnerId/ks in several shapes; extraction should cope."""

    def test_kaltura_iframe_package_data(self):
        html = ('<script>window.kalturaIframePackageData = {"partnerId":4357,'
                '"entryResult":{"meta":{"id":"0_mtultv7g"}},"ks":"djJ8NDM1N3xAAAABBBBCCCCDDDDEEEEFFFF"};'
                '</script>')
        cfg = K.extract_player_config(html)
        self.assertEqual(cfg["partner_id"], "4357")
        self.assertEqual(cfg["entry_id"], "0_mtultv7g")
        self.assertTrue(cfg["ks"].startswith("djJ8"))

    def test_widget_id_and_flashvars_ks(self):
        html = ("kWidget.embed({targetId:'p', wid:'_4357', uiconf_id:'23448994', "
                "flashvars:{ks:'djJ8NDM1N3xZZZZYYYYXXXXWWWWVVVVUUUU'}, entry_id:'1_abcd1234'});")
        cfg = K.extract_player_config(html)
        self.assertEqual(cfg["partner_id"], "4357")
        self.assertEqual(cfg["uiconf_id"], "23448994")
        self.assertEqual(cfg["entry_id"], "1_abcd1234")
        self.assertTrue(cfg["ks"].startswith("djJ8"))

    def test_ks_in_url_param_and_host_only_service(self):
        html = ('<iframe src="https://kaf.moodle.ffhs.ch/p/4357/embedPlaykitJs/uiconf_id/234?'
                'ks=djJ8NDM1N3xQQQQRRRRSSSSTTTTUUUUVVVV&entry_id=0_6xbxx8j4"></iframe>'
                '<script>var c={serviceUrl:"cdnapisec.kaltura.com"}</script>')
        cfg = K.extract_player_config(html)
        self.assertEqual(cfg["partner_id"], "4357")           # from /p/4357/
        self.assertEqual(cfg["entry_id"], "0_6xbxx8j4")
        self.assertTrue(cfg["ks"].startswith("djJ8"))
        self.assertEqual(cfg["service_url"], "https://cdnapisec.kaltura.com")  # host-only -> https://

    def test_extract_iframe_src_absolutised(self):
        html = '<html><body><iframe class="p" src="/hosted/player?x=1"></iframe></body></html>'
        self.assertEqual(K.extract_iframe_src(html, "https://kaf.moodle.ffhs.ch/launch/abc"),
                         "https://kaf.moodle.ffhs.ch/hosted/player?x=1")


class LaunchConfigFollowTests(unittest.TestCase):
    """_launch_config should follow the LTI form and, if needed, one iframe, merging what it finds."""

    class _Sess:
        available = True
        reason = ""

        def __init__(self, pages):
            self.pages = pages          # url-substring -> html
            self.posted = []

        def ensure_session(self):
            return True

        def _match(self, url):
            for key, html in self.pages.items():
                if key in url:
                    return html
            return "<html>nothing</html>"

        def get_text(self, url):
            return self._match(url)

        def post_text(self, url, fields):
            self.posted.append(url)
            return self._match(url)

    def test_partner_from_iframe_after_form(self):
        launch = "https://moodle.ffhs.ch/filter/kaltura/lti_launch.php?source=x/entryid/0_mtultv7g/"
        sess = self._Sess({
            "lti_launch.php": ('<form action="https://kaf.moodle.ffhs.ch/hosted/launch" method="post">'
                               '<input name="oauth_signature" value="s"></form>'
                               '<script>document.forms[0].submit()</script>'),
            "/hosted/launch": '<html><body><iframe src="https://kaf.moodle.ffhs.ch/player/0_mtultv7g"></iframe></body></html>',
            "/player/": ('<script>window.kalturaIframePackageData={"partnerId":4357,'
                         '"ks":"djJ8NDM1N3xKKKKLLLLMMMMNNNNOOOOPPPP"};</script>'),
        })
        kc = K.KalturaClient(sess)
        ref = K.parse_kaltura(launch, "Podcast")
        cfg = kc._launch_config(ref)
        self.assertEqual(cfg["partner_id"], "4357")
        self.assertTrue(cfg["ks"].startswith("djJ8"))
        self.assertIn("partner_id=si", kc.last_diagnostic)


if __name__ == "__main__":
    unittest.main()

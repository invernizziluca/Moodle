"""End-to-end test of the video subsystem over real HTTP against a faithful local mock.

This exercises the *whole* pipeline the way it runs against FFHS, except the servers are mocks on
127.0.0.1 instead of moodle.ffhs.ch / kaf.moodle.ffhs.ch / *.kaltura.com:

  VideoSession.ensure_session()  -> official Moodle autologin WS + autologin.php (cookie set)
  KalturaClient.resolve()        -> /filter/kaltura/lti_launch.php self-submitting form
                                 -> KAF player page (partner_id / ks / serviceUrl)
                                 -> Kaltura playManifest (progressive MP4, else HLS)
  KalturaClient.download()       -> stream a single MP4  /  join HLS segments

The only thing a mock cannot prove is the exact HTML/stream shape of the *real* FFHS Kaltura; that
is why a failed resolve/download is always *flagged* (never silently dropped), which is asserted too.
"""

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffhs_archiver import kaltura  # noqa: E402
from ffhs_archiver.moodle import VideoSession  # noqa: E402

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"VIDEO-PROGRESSIVE-" * 64
SEG = [b"SEGMENT-0-" * 20, b"SEGMENT-1-" * 20, b"SEGMENT-2-" * 20]
INIT = b"INIT-SEGMENT-"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the test quiet
        pass

    def _send(self, code, body=b"", ctype="text/html", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- autologin + launch ------------------------------------------------
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        p = self.path
        if "wsfunction=tool_mobile_get_autologin_key" in p:
            base = f"http://{self.headers['Host']}"
            return self._send(200, '{"key":"AUTOKEY123","autologinurl":"%s/admin/tool/mobile/autologin.php",'
                                   '"warnings":[]}' % base, "application/json")
        if p.startswith("/kaf/launch"):
            host = self.headers["Host"]
            # KAF player page: contains the Kaltura partner id, a KS and the service (CDN) URL.
            page = ("<html><head><script>var kConf={partnerId:1234567,"
                    "ks:'ZmFrZV9rc19rYWx0dXJhX3Rva2VuXzEyMzQ1Njc4OTA',"
                    "serviceUrl:'http://%s/kcdn'};</script></head>"
                    "<body>entryId: %s kWidget ready</body></html>" % (host, self._entry))
            return self._send(200, page)
        return self._send(404)

    def do_GET(self):
        host = self.headers["Host"]
        p = self.path
        if p.startswith("/admin/tool/mobile/autologin.php"):
            # Autologin succeeds: set a session cookie, return a normal page (no login form).
            return self._send(200, "<html><body>Dashboard / Meine Kurse</body></html>",
                              extra={"Set-Cookie": "MoodleSession=abc123; Path=/"})
        if p.startswith("/filter/kaltura/lti_launch.php"):
            # Remember which entry this launch is for, then hand back a self-submitting LTI form.
            self._server_entry()
            form = ("<html><body><form action='http://%s/kaf/launch' method='post'>"
                    "<input type='hidden' name='oauth_signature' value='sig'/>"
                    "<input type='hidden' name='custom_entry_id' value='%s'/>"
                    "</form><script>document.forms[0].submit();</script></body></html>"
                    % (host, self._entry))
            return self._send(200, form)
        path_only = p.split("?", 1)[0]
        if path_only.endswith((".m4s",)) and "/seg" in path_only:        # a media segment
            idx = int(path_only.rsplit("seg", 1)[1].split(".")[0])
            return self._send(200, SEG[idx], "video/mp4")
        if path_only.endswith("/init.mp4"):                              # HLS init segment
            return self._send(200, INIT, "video/mp4")
        if path_only.endswith(("/high/index.m3u8", "/low/index.m3u8")):  # a variant media playlist
            base = path_only.rsplit("/", 1)[0]
            media = "#EXTM3U\n#EXT-X-MAP:URI=\"%s/init.mp4\"\n" % base
            for i in range(len(SEG)):
                media += "#EXTINF:6.0,\n%s/seg%d.m4s\n" % (base, i)
            return self._send(200, media + "#EXT-X-ENDLIST\n", "application/vnd.apple.mpegurl")
        if "/playManifest/" in path_only and path_only.endswith("video.m3u8"):   # HLS master
            master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\nlow/index.m3u8\n"
                      "#EXT-X-STREAM-INF:BANDWIDTH=2000000\nhigh/index.m3u8\n")
            return self._send(200, master, "application/vnd.apple.mpegurl")
        if "/playManifest/" in path_only and "/format/url/" in path_only:        # progressive MP4
            if self._entry == "0_hlsonly":           # progressive not available -> force HLS
                return self._send(404, "no progressive flavor")
            return self._send(206 if "bytes=" in (self.headers.get("Range") or "") else 200,
                              MP4_BYTES, "video/mp4")
        return self._send(404)

    # the launch URL names the entry; keep it on the server so the follow-up POST knows it too
    def _server_entry(self):
        q = self.path.split("source=", 1)[-1]
        self.server.entry = "0_hlsonly" if "0_hlsonly" in q else "0_progressive"

    @property
    def _entry(self):
        return getattr(self.server, "entry", "0_progressive")


class _FakeClient:
    """The few attributes VideoSession reads from a MoodleClient."""
    def __init__(self, base):
        self.site_url = base
        self.token = "0" * 32
        self.timeout = 10
        self.site_info_data = {"userid": 42}

        class _Ev:
            def is_set(self):
                return False
        self.cancel_event = _Ev()

    def site_info(self):
        return self.site_info_data


class VideoEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _session(self):
        return VideoSession(_FakeClient(self.base), privatetoken="PRIVATE-TOKEN-XYZ")

    def _launch_url(self, entry):
        src = (f"https%3A%2F%2Fkaf.example%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F{entry}"
               "%2FshowDescription%2Ffalse")
        return f"{self.base}/filter/kaltura/lti_launch.php?courseid=0&width=576&height=370&source={src}"

    def test_autologin_opens_session(self):
        vs = self._session()
        self.assertTrue(vs.ensure_session(), vs.reason)
        self.assertTrue(vs.available)

    def test_autologin_needs_private_token(self):
        vs = VideoSession(_FakeClient(self.base), privatetoken="")
        self.assertFalse(vs.ensure_session())
        self.assertIn("token privato", vs.reason)

    def test_progressive_download(self):
        vs = self._session()
        ref = kaltura.parse_kaltura(self._launch_url("0_progressive"), "Lezione 1")
        self.assertIsNotNone(ref)
        kc = kaltura.KalturaClient(vs)
        dest = Path(self._tmp()) / "video.mp4"
        outcome = kc.download(ref, dest)
        self.assertEqual(outcome.status, "downloaded", outcome.note)
        self.assertEqual(dest.read_bytes(), MP4_BYTES)

    def test_hls_download_joins_segments(self):
        vs = self._session()
        ref = kaltura.parse_kaltura(self._launch_url("0_hlsonly"), "Lezione 2")
        kc = kaltura.KalturaClient(vs)
        dest = Path(self._tmp()) / "video.mp4"
        outcome = kc.download(ref, dest)
        self.assertEqual(outcome.status, "downloaded", outcome.note)
        self.assertEqual(dest.read_bytes(), INIT + b"".join(SEG))  # best (high) variant, init + segments

    def test_unreachable_entry_is_flagged_not_dropped(self):
        vs = self._session()
        # a launch URL with no resolvable entry id -> resolve fails -> flagged with a reason
        ref = kaltura.KalturaRef(entry_id="0_missing", launch_url=f"{self.base}/filter/kaltura/nope")
        kc = kaltura.KalturaClient(vs)
        outcome = kc.download(ref, Path(self._tmp()) / "v.mp4")
        self.assertEqual(outcome.status, "flagged")
        self.assertTrue(outcome.note)

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""The downloaded lecture video must be a *playable* file, not just the right bytes.

test_video_e2e.py already proves the whole network pipeline (autologin -> LTI launch -> KAF player
-> playManifest -> progressive / HLS) over real HTTP with the real VideoSession, asserting a
byte-exact result. This test adds the one thing byte blobs cannot show: it serves **real media made
with ffmpeg** (a progressive MP4 and a multi-segment HLS stream with MPEG-TS segments, the shape
Kaltura's ``applehttp`` format really serves), downloads it through the same real pipeline, and then
checks with ffprobe that the file on disk decodes and has the expected duration.

ffmpeg/ffprobe are only used by this test; the archiver itself joins HLS segments in pure Python and
needs no ffmpeg on the user's machine. The test skips when ffmpeg is not installed.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffhs_archiver import kaltura  # noqa: E402
from ffhs_archiver.moodle import VideoSession  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _ffprobe(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration,nb_streams",
                          "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout or "{}")


def _make_media(dst: Path) -> None:
    """A real 2s progressive MP4 and a real 3s HLS (3 x 1s MPEG-TS segments)."""
    (dst / "hls").mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-movflags", "+faststart", str(dst / "prog.mp4")], check=True, timeout=60)
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-force_key_frames", "expr:gte(t,n_forced*1)", "-c:a", "aac",
                    "-f", "hls", "-hls_time", "1", "-hls_segment_type", "mpegts",
                    "-hls_playlist_type", "vod", "-hls_list_size", "0",
                    "-hls_segment_filename", str(dst / "hls" / "seg%d.ts"),
                    str(dst / "hls" / "media.m3u8")], check=True, timeout=60)


class _Handler(BaseHTTPRequestHandler):
    media: Path = Path()

    def log_message(self, *a):
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

    def _entry(self):
        return getattr(self.server, "entry", "0_progressive")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        host = self.headers["Host"]
        if "wsfunction=tool_mobile_get_autologin_key" in self.path:
            return self._send(200, '{"key":"K","autologinurl":"http://%s/admin/tool/mobile/autologin.php",'
                                   '"warnings":[]}' % host, "application/json")
        if self.path.startswith("/kaf/launch"):
            page = ("<html><head><script>var kConf={partnerId:1234567,"
                    "ks:'ZmFrZV9rc18xMjM0NTY3ODkw',serviceUrl:'http://%s/kcdn'};</script></head>"
                    "<body>entryId: %s kWidget ready</body></html>" % (host, self._entry()))
            return self._send(200, page)
        return self._send(404)

    def do_GET(self):
        host = self.headers["Host"]
        p = self.path.split("?", 1)[0]
        if p.startswith("/admin/tool/mobile/autologin.php"):
            return self._send(200, "<html><body>Meine Kurse</body></html>",
                              extra={"Set-Cookie": "MoodleSession=s; Path=/"})
        if p.startswith("/filter/kaltura/lti_launch.php"):
            self.server.entry = "0_hlsonly" if "0_hlsonly" in self.path else "0_progressive"
            form = ("<html><body><form action='http://%s/kaf/launch' method='post'>"
                    "<input type='hidden' name='entry' value='%s'/></form>"
                    "<script>document.forms[0].submit();</script></body></html>"
                    % (host, self._entry()))
            return self._send(200, form)
        # progressive MP4 flavor
        if "/playManifest/" in p and "/format/url/" in p:
            if self._entry() == "0_hlsonly":
                return self._send(404, "no progressive flavor")
            data = (self.media / "prog.mp4").read_bytes()
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start = int(rng.split("=")[1].split("-")[0] or 0)
                end = start  # the probe asks for bytes=0-0
                chunk = data[start:end + 1]
                return self._send(206, chunk, "video/mp4",
                                  {"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"})
            return self._send(200, data, "video/mp4")
        # HLS master -> variant media -> TS segments
        if "/playManifest/" in p and p.endswith("video.m3u8"):
            master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000\n%s/low.m3u8\n"
                      "#EXT-X-STREAM-INF:BANDWIDTH=1500000\n%s/hi.m3u8\n"
                      % (f"http://{host}/hls", f"http://{host}/hls"))
            return self._send(200, master, "application/vnd.apple.mpegurl")
        if p in ("/hls/low.m3u8", "/hls/hi.m3u8"):
            text = (self.media / "hls" / "media.m3u8").read_text()
            # rewrite bare segment names to absolute URLs on this host
            text = text.replace("seg", f"http://{host}/hls/seg")
            return self._send(200, text, "application/vnd.apple.mpegurl")
        if p.startswith("/hls/seg") and p.endswith(".ts"):
            seg = self.media / "hls" / p.rsplit("/", 1)[1]
            if seg.is_file():
                return self._send(200, seg.read_bytes(), "video/mp2t")
        return self._send(404)


class _FakeClient:
    def __init__(self, base):
        self.site_url = base
        self.token = "0" * 32
        self.timeout = 15
        self.site_info_data = {"userid": 7}

        class _Ev:
            def is_set(self):
                return False
        self.cancel_event = _Ev()

    def site_info(self):
        return self.site_info_data


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not installed (only this extra test needs them)")
class PlayableVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = Path(tempfile.mkdtemp(prefix="fmamedia"))
        _make_media(cls.media)
        _Handler.media = cls.media
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.media, ignore_errors=True)

    def _launch(self, entry):
        src = (f"https%3A%2F%2Fkaf.example%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F{entry}"
               "%2FshowTitle%2Ffalse")
        return f"{self.base}/filter/kaltura/lti_launch.php?courseid=0&width=576&source={src}"

    def _download(self, entry):
        vs = VideoSession(_FakeClient(self.base), privatetoken="PRIV")
        ref = kaltura.parse_kaltura(self._launch(entry), "Lezione")
        self.assertIsNotNone(ref)
        dest = Path(tempfile.mkdtemp(prefix="fmaout")) / "video.mp4"
        self.addCleanup(lambda: shutil.rmtree(dest.parent, ignore_errors=True))
        outcome = kaltura.KalturaClient(vs).download(ref, dest)
        self.assertEqual(outcome.status, "downloaded", outcome.note)
        real = dest.with_suffix(outcome.suffix) if outcome.suffix else dest
        self.assertTrue(real.is_file())
        return real

    def test_progressive_is_a_playable_mp4(self):
        out = self._download("0_progressive")
        info = _ffprobe(out)
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.6)
        kinds = {s["codec_type"] for s in info.get("streams", [])}
        self.assertIn("video", kinds)

    def test_hls_segments_join_into_a_playable_file(self):
        out = self._download("0_hlsonly")
        # more than one segment was really concatenated
        self.assertGreater(len(list((self.media / "hls").glob("seg*.ts"))), 1)
        info = _ffprobe(out)
        self.assertAlmostEqual(float(info["format"]["duration"]), 3.0, delta=0.8)
        kinds = {s["codec_type"] for s in info.get("streams", [])}
        self.assertIn("video", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)

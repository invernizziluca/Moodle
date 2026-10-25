"""HLS regression tests: real Moodle downloads with an in-memory HTTP transport."""

import io
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ffhs_archiver.hls import download_hls
from ffhs_archiver.moodle import CancelledError, MoodleClient, MoodleError


SITE = "https://moodle.ffhs.ch"
PREFIX = "/31/mod_resource/content/0/"


def url(name):
    return SITE + "/pluginfile.php" + PREFIX + name


class Response(io.BytesIO):
    def __init__(self, data, manifest=False):
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Type": "application/vnd.apple.mpegurl" if manifest else "video/mp2t",
                        "Content-Length": str(len(data))}


class Opener:
    def __init__(self, files):
        self.files = files
        self.seen = []

    def open(self, request, **kwargs):
        parsed = urlsplit(request.full_url)
        assert parsed.netloc == "moodle.ffhs.ch"
        assert parse_qs(parsed.query)["token"] == ["offline-hls-test"]
        if parsed.path == "/webservice/pluginfile.php":
            key = parse_qs(parsed.query)["file"][0]
        else:
            key = parsed.path.removeprefix("/webservice/pluginfile.php")
        assert key.startswith(PREFIX)
        name = key[len(PREFIX):]
        self.seen.append(name)
        if name not in self.files:
            raise AssertionError(f"Unexpected request: {name}")
        data = self.files[name]
        return Response(data.encode() if isinstance(data, str) else data, name.endswith(".m3u8"))


VOD = "#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5,\na.ts\n#EXTINF:4,\nb.ts\n#EXT-X-ENDLIST\n"


class HLSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hls-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dest = self.root / "recording"
        self.client = MoodleClient(SITE, "offline-hls-test", min_interval=0, retries=0)
        self.opener = Opener({"video.m3u8": VOD, "a.ts": b"segment A", "b.ts": b"segment B"})
        self.client._opener = self.opener

    def download(self, **kwargs):
        return download_hls(self.client, url("video.m3u8"), self.dest, **kwargs)

    def assert_no_partial(self):
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_vod_saves_all_bytes_and_offline_playlist(self):
        progress = []
        result = self.download(progress=lambda done, total: progress.append(done))
        self.assertEqual(result.entrypoint, self.dest / "video.m3u8")
        self.assertEqual(result.segment_count, 2)
        self.assertEqual((self.dest / "s00000.ts").read_bytes(), b"segment A")
        self.assertEqual((self.dest / "s00001.ts").read_bytes(), b"segment B")
        text = result.entrypoint.read_text()
        self.assertIn("#EXTINF:5,\ns00000.ts", text)
        self.assertIn("#EXT-X-ENDLIST", text)
        self.assertNotIn("https:", text)
        self.assertNotIn("offline-hls-test", text)
        self.assertEqual(progress[-1], result.total_bytes)
        self.assertEqual(self.opener.seen, ["video.m3u8", "a.ts", "b.ts"])

    def test_master_selects_highest_bandwidth_and_initialization(self):
        self.opener.files = {
            "video.m3u8": '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=10\nlow.m3u8\n'
                          '#EXT-X-STREAM-INF:BANDWIDTH=20\nhigh/list.m3u8\n',
            "high/list.m3u8": '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,\n'
                              'one.m4s\n#EXT-X-ENDLIST\n',
            "high/init.mp4": b"init", "high/one.m4s": b"media"}
        result = self.download()
        self.assertEqual(result.segment_count, 1)
        self.assertIn('#EXT-X-MAP:URI="i00000.mp4"', result.entrypoint.read_text())
        self.assertEqual((self.dest / "i00000.mp4").read_bytes(), b"init")
        self.assertEqual((self.dest / "s00001.m4s").read_bytes(), b"media")
        self.assertNotIn("low.m3u8", self.opener.seen)

    def test_query_style_urls_resolve_relative_paths(self):
        result = download_hls(self.client, SITE + "/pluginfile.php?file=" + PREFIX + "video.m3u8",
                              self.dest)
        self.assertEqual(result.segment_count, 2)
        self.assertEqual(self.opener.seen, ["video.m3u8", "a.ts", "b.ts"])

    def test_size_limit_cleans_staging_and_does_not_publish_manifest(self):
        limit = len(VOD.encode()) + len(b"segment A")
        with self.assertRaises(MoodleError) as caught:
            self.download(max_bytes=limit)
        self.assertEqual(caught.exception.errorcode, "filetoolarge")
        self.assert_no_partial()

    def test_external_segment_is_rejected_before_any_media_download(self):
        self.opener.files["video.m3u8"] = VOD.replace("b.ts", "https://evil.example/b.ts")
        with self.assertRaises(MoodleError) as caught:
            self.download()
        self.assertEqual(caught.exception.errorcode, "hlsexternal")
        self.assertEqual(self.opener.seen, ["video.m3u8"])
        self.assert_no_partial()

    def test_encrypted_and_live_recordings_are_not_declared_downloaded(self):
        for text, code in ((VOD.replace("#EXTINF:5,", '#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:5,'),
                            "hlsencrypted"), (VOD.replace("#EXT-X-ENDLIST\n", ""), "hlslive")):
            with self.subTest(code=code):
                self.opener.files["video.m3u8"] = text
                with self.assertRaises(MoodleError) as caught:
                    self.download()
                self.assertEqual(caught.exception.errorcode, code)
                self.assert_no_partial()

    def test_separate_audio_track_is_rejected_without_silent_audio_loss(self):
        self.opener.files["video.m3u8"] = ('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",'
                                         'URI="audio.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=20,AUDIO="audio"\n'
                                         'high.m3u8\n')
        with self.assertRaises(MoodleError) as caught:
            self.download()
        self.assertEqual(caught.exception.errorcode, "hlsaudio")
        self.assertEqual(self.opener.seen, ["video.m3u8"])
        self.assert_no_partial()

    def test_byterange_is_rejected_without_downloading_incomplete_media(self):
        self.opener.files["video.m3u8"] = VOD.replace("a.ts", "#EXT-X-BYTERANGE:4@0\na.ts")
        with self.assertRaises(MoodleError) as caught:
            self.download()
        self.assertEqual(caught.exception.errorcode, "hlsbyterange")
        self.assert_no_partial()

    def test_cancellation_removes_staging(self):
        def cancel(done, total):
            self.client.cancel_event.set()
        with self.assertRaises(CancelledError):
            self.download(progress=cancel)
        self.assert_no_partial()

    def test_existing_recording_is_never_overwritten(self):
        self.dest.mkdir()
        marker = self.dest / "keep.txt"
        marker.write_text("existing")
        with self.assertRaises(FileExistsError):
            self.download()
        self.assertEqual(marker.read_text(), "existing")
        self.assertEqual(self.opener.seen, [])

    def test_cyclic_master_and_invalid_playlist_are_rejected(self):
        for text in ("not a manifest", "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=20\nvideo.m3u8\n"):
            with self.subTest(text=text):
                self.opener.files["video.m3u8"] = text
                with self.assertRaises(MoodleError):
                    self.download()
                self.assert_no_partial()

    @unittest.skipUnless(shutil.which("ffmpeg"), "optional ffmpeg playback check not available")
    def test_real_audio_video_recording_decodes_offline(self):
        source = self.root / "generated"
        source.mkdir()
        generated = subprocess.run([
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "2", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "mpeg2video", "-g", "5", "-bf", "0", "-c:a", "mp2", "-b:a", "64k",
            "-hls_time", "1", "-hls_playlist_type", "vod", "-hls_segment_filename",
            str(source / "s%03d.ts"), str(source / "video.m3u8")],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr[-2000:])
        self.opener.files = {p.name: p.read_bytes() for p in source.iterdir()}
        result = self.download()
        self.assertGreater(result.segment_count, 0)
        # Do not allow HTTP(S), TCP, or other network protocols during playback.
        # Both mappings are required, so losing the audio cannot silently pass.
        playback = subprocess.run([
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
            "-protocol_whitelist", "file,crypto,data", "-i", str(result.entrypoint),
            "-map", "0:v:0", "-map", "0:a:0", "-progress", "pipe:1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(playback.returncode, 0, playback.stderr[-2000:])
        frames = [int(value) for value in re.findall(r"^frame=(\d+)$", playback.stdout, re.M)]
        self.assertTrue(frames and frames[-1] > 0, playback.stdout)
        self.assertIn("progress=end", playback.stdout)
        print(f"HLS offline playback verified: {frames[-1]} video frames and mapped audio")


if __name__ == "__main__":
    unittest.main()

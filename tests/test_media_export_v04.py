"""Offline end-to-end checks of query URLs and complete Moodle HLS exports."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ffhs_archiver.exporter import Exporter, ExportOptions
from ffhs_archiver.moodle import MoodleClient
from test_export_media import Response


SITE = "https://moodle.ffhs.ch"
TOKEN = "offline-v04-media-test"
COURSE = {"id": 8, "fullname": "Offline Moodle media", "shortname": "MEDIA04"}
AREA = "/123/mod_page/content/0/"


def file_url(name, query=False):
    if query:
        return SITE + "/pluginfile.php?" + urlencode({"file": AREA + name})
    return SITE + "/pluginfile.php" + AREA + quote(name, safe="/")


class MediaOpener:
    """Only fixture REST calls and Moodle files are accepted; no real I/O."""

    def __init__(self, modules, pages=None, urls=None, files=None):
        self.requests = []
        self.file_requests = []
        self.files = files or {}
        self.api = {
            "core_course_get_contents": [{"id": 80, "section": 1, "name": "Media",
                                          "summary": "", "modules": modules}],
            "core_course_get_courses_by_field": {"courses": [COURSE]},
            "mod_url_get_urls_by_courses": {"urls": urls or []},
            "mod_page_get_pages_by_courses": {"pages": pages or []},
        }

    def open(self, request, timeout=None):
        self.requests.append(request)
        parsed = urlsplit(request.full_url)
        assert parsed.scheme == "https" and parsed.netloc == "moodle.ffhs.ch", parsed.netloc
        if parsed.path == "/webservice/rest/server.php":
            args = parse_qs(request.data.decode())
            assert args["wstoken"] == [TOKEN]
            name = args["wsfunction"][0]
            assert name in self.api, name
            return Response(json.dumps(self.api[name]).encode())
        params = parse_qs(parsed.query)
        assert params["token"] == [TOKEN]
        if parsed.path == "/webservice/pluginfile.php":
            argument = params["file"][0]
        else:
            assert parsed.path.startswith("/webservice/pluginfile.php/")
            argument = unquote(parsed.path.removeprefix("/webservice/pluginfile.php"))
        assert argument.startswith(AREA), argument
        name = argument[len(AREA):]
        assert name in self.files, "Unexpected file download: " + name
        self.file_requests.append(name)
        data, content_type = self.files[name]
        return Response(data, content_type)


def module(mid, modname, title):
    return {"id": mid, "instance": mid + 100, "modname": modname, "name": title,
            "uservisible": True, "url": f"{SITE}/mod/{modname}/view.php?id={mid}"}


class MediaExportV04Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffhs-media-v04-")
        self.addCleanup(self.temp.cleanup)
        self.dest = Path(self.temp.name)

    def export(self, opener, **options):
        client = MoodleClient(SITE, TOKEN, min_interval=0, retries=0)
        client._opener = opener
        client.functions = set(opener.api)
        client.site_info_data = {"userid": 1}
        summary = Exporter(client, ExportOptions(dest=str(self.dest), **options)).export([COURSE])
        self.assertFalse(summary["stopped"], summary)
        result = summary["results"][0]
        self.assertEqual(result["status"], "ok", result)
        return result, self.dest / result["folder"]

    def test_query_url_iframe_video_and_embedded_pdf_are_real_local_files(self):
        files = {
            "url-clip.mp4": (b"\x00\x00\x00\x18ftypmp42url-video", "video/mp4"),
            "iframe-clip.mp4": (b"\x00\x00\x00\x18ftypmp42iframe-video", "video/mp4"),
            "handout (1).pdf": (b"%PDF-1.4\nQuery handout\n%%EOF", "application/pdf"),
        }
        html = (f'<iframe src="{file_url("iframe-clip.mp4", True)}" title="Moodle video"></iframe>'
                f'<object data="{file_url("handout (1).pdf", True)}" title="Handout"></object>')
        opener = MediaOpener([module(101, "url", "Linked clip"), module(102, "page", "Embedded files")],
                             pages=[{"coursemodule": 102, "content": html}],
                             urls=[{"coursemodule": 101, "externalurl": file_url("url-clip.mp4", True)}],
                             files=files)
        result, root = self.export(opener)
        self.assertEqual(result["errors"], [])
        self.assertCountEqual(opener.file_requests, files)
        fulltext = (root / "course_fulltext.md").read_text()
        for name, (data, _) in files.items():
            matches = list(root.rglob(name))
            self.assertEqual(len(matches), 1, name)
            self.assertEqual(matches[0].read_bytes(), data)
            relative = quote(matches[0].relative_to(root).as_posix(), safe="/")
            self.assertIn("](" + relative + ")", fulltext)
        self.assertFalse(list(root.rglob("pluginfile.php")))
        inventory = (root / "video_e_media.md").read_text()
        self.assertEqual(inventory.count("| Scaricato |"), 2)
        self.assertNotIn("@@FILE:", fulltext)

    def hls_opener(self, in_page=False, large=False):
        playlist = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
                    "#EXTINF:2.0,\nsegment-a.ts\n#EXTINF:2.0,\nsegment-b.ts\n#EXT-X-ENDLIST\n")
        files = {
            "playlist.m3u8": (playlist.encode(), "application/vnd.apple.mpegurl"),
            "segment-a.ts": (b"\x47" + b"A" * (1024 * 1024 if large else 187), "video/mp2t"),
            "segment-b.ts": (b"\x47" + b"B" * 187, "video/mp2t"),
        }
        if in_page:
            return MediaOpener([module(110, "page", "Moodle streaming lesson")], pages=[{
                "coursemodule": 110, "content": f'<video src="{file_url("playlist.m3u8")}"></video>'}], files=files)
        return MediaOpener([module(111, "url", "Moodle streaming lesson")], urls=[{
            "coursemodule": 111, "externalurl": file_url("playlist.m3u8")}], files=files)

    def test_bulk_resource_files_without_core_contents_or_filenames_are_downloaded(self):
        files = {
            "resource-handout.pdf": (b"%PDF-1.4\nBulk handout\n%%EOF", "application/pdf"),
            "resource-clip.mp4": (b"\x00\x00\x00\x18ftypmp42bulk-video", "video/mp4"),
        }
        opener = MediaOpener([module(130, "resource", "Resource attachments")], files=files)
        opener.api["mod_resource_get_resources_by_courses"] = {"resources": [{
            "coursemodule": 130,
            "contentfiles": [{"fileurl": file_url(name, True), "filesize": len(data),
                              "mimetype": mime, "timemodified": 12345}
                             for name, (data, mime) in files.items()],
        }]}
        result, root = self.export(opener)
        self.assertEqual(result["errors"], [])
        self.assertCountEqual(opener.file_requests, files)
        text = (root / "course_fulltext.md").read_text()
        for name, (data, _) in files.items():
            matches = list(root.rglob(name))
            self.assertEqual(len(matches), 1, name)
            self.assertEqual(matches[0].read_bytes(), data)
            self.assertIn("](" + quote(matches[0].relative_to(root).as_posix(), safe="/") + ")", text)
        self.assertIn("| Scaricato |", (root / "video_e_media.md").read_text())

    def test_moodle_hls_vod_downloads_segments_and_offline_playlist(self):
        opener = self.hls_opener()
        result, root = self.export(opener)
        self.assertEqual(result["errors"], [])
        playlists = list(root.rglob("video.m3u8"))
        self.assertEqual(len(playlists), 1)
        playlist = playlists[0]
        text = playlist.read_text()
        references = [line for line in text.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(references), 2)
        local_data = []
        for ref in references:
            self.assertNotIn("://", ref)
            self.assertNotIn("token", ref)
            local_data.append((playlist.parent / unquote(ref)).read_bytes())
        self.assertCountEqual(local_data, [opener.files["segment-a.ts"][0], opener.files["segment-b.ts"][0]])
        self.assertIn("#EXT-X-ENDLIST", text)
        self.assertTrue({"playlist.m3u8", "segment-a.ts", "segment-b.ts"}.issubset(opener.file_requests))
        inventory = (root / "video_e_media.md").read_text()
        self.assertIn("| Scaricato |", inventory)
        relative = quote(playlist.relative_to(root).as_posix(), safe="/")
        self.assertIn("](" + relative + ")", inventory)
        self.assertIn("](" + relative + ")", (root / "course_fulltext.md").read_text())
        self.assertNotIn("segmenti video non sono stati scaricati", (root / "elementi_mancanti.md").read_text())

    def test_hls_video_disabled_makes_no_manifest_or_segment_requests(self):
        opener = self.hls_opener(in_page=True)
        result, root = self.export(opener, include_videos=False)
        self.assertEqual(result["errors"], [])
        self.assertEqual(opener.file_requests, [])
        self.assertFalse(list(root.rglob("video.m3u8")))
        self.assertIn("| Escluso |", (root / "video_e_media.md").read_text())

    def test_query_style_hls_resolves_relative_segments_in_the_file_argument(self):
        opener = self.hls_opener()
        opener.api["mod_url_get_urls_by_courses"]["urls"][0]["externalurl"] = file_url("playlist.m3u8", True)
        result, root = self.export(opener)
        self.assertEqual(result["errors"], [])
        self.assertCountEqual(opener.file_requests, ["playlist.m3u8", "segment-a.ts", "segment-b.ts"])
        playlist = next(root.rglob("video.m3u8"))
        references = [line for line in playlist.read_text().splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(references), 2)
        self.assertCountEqual([(playlist.parent / unquote(ref)).read_bytes() for ref in references],
                              [opener.files["segment-a.ts"][0], opener.files["segment-b.ts"][0]])

    def test_hls_limit_does_not_report_a_partial_playlist_as_downloaded(self):
        opener = self.hls_opener(large=True)
        result, root = self.export(opener, max_file_mb=1)
        self.assertFalse(list(root.rglob("video.m3u8")))
        inventory = (root / "video_e_media.md").read_text()
        self.assertNotIn("| Scaricato |", inventory)
        self.assertEqual(result["errors"], [])
        self.assertIn("| Escluso |", inventory)
        self.assertIn("limite", (root / "elementi_mancanti.md").read_text().casefold())

    def test_youtube_is_a_link_without_any_video_download_attempt(self):
        opener = MediaOpener([module(120, "url", "YouTube lecture")], urls=[{
            "coursemodule": 120, "externalurl": "https://www.youtube.com/watch?v=abcdefghijk"}])
        result, root = self.export(opener)
        self.assertEqual(result["errors"], [])
        self.assertEqual(opener.file_requests, [])
        fulltext = (root / "course_fulltext.md").read_text()
        self.assertIn("https://www.youtube.com/watch?v=abcdefghijk", fulltext)
        self.assertIn("| Solo link |", (root / "video_e_media.md").read_text())


if __name__ == "__main__":
    unittest.main()

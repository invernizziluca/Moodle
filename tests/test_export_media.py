"""Offline export regression tests with the real Moodle transport and downloader.

Only the opener is replaced: REST serialization, URL tokenization, streaming to
disk, export planning, Markdown generation and saved sync state remain real.
"""

import io
import json
import errno
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffhs_archiver.exporter import Exporter, ExportOptions
from ffhs_archiver.moodle import MoodleClient
from verify_archive import main as verify_archive


SITE = "https://moodle.ffhs.ch"
TOKEN = "offline-test-token-never-used-on-network"
COURSE = {"id": 7, "fullname": "Offline media course", "shortname": "OFFLINE"}
BASE = "/123/mod_page/content/0/"
FILES = {
    "url-clip.mp4": b"\x00\x00\x00\x18ftypmp42offline-url-video",
    "embedded-clip.mp4": b"\x00\x00\x00\x18ftypmp42offline-object-video",
    "lecture.pdf": b"%PDF-1.4\nOffline embedded lecture\n%%EOF\n",
}


def file_url(name):
    return SITE + "/pluginfile.php" + BASE + name


class Response(io.BytesIO):
    def __init__(self, data, content_type="application/json"):
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data)),
                        "Last-Modified": "Sat, 24 Oct 2026 12:00:00 GMT"}


class FixtureOpener:
    """Fail closed on every destination or operation absent from the fixture."""

    def __init__(self, broken_file=None):
        self.requests = []
        self.file_requests = []
        self.broken_file = broken_file
        self.contents = [{"id": 70, "section": 1, "name": "Media", "summary": "",
                          "modules": [
                              {"id": 101, "instance": 201, "modname": "url", "name": "Linked video",
                               "url": SITE + "/mod/url/view.php?id=101", "uservisible": True,
                               "contents": [{"type": "url", "fileurl": file_url("url-clip.mp4")}]},
                              {"id": 102, "instance": 202, "modname": "page", "name": "Embedded materials",
                               "url": SITE + "/mod/page/view.php?id=102", "uservisible": True},
                          ]}]
        page = (f'<iframe src="{file_url("lecture.pdf")}" title="Lecture"></iframe>'
                f'<object data="{file_url("embedded-clip.mp4")}" title="Object video"></object>'
                '<iframe src="https://www.youtube.com/embed/abcdefghijk" title="External video"></iframe>'
                '<video src="https://media.example.org/playlist.m3u8" title="HLS"></video>'
                f'<video src="{file_url("manifest.mpd")}" title="DASH"></video>')
        self.api = {
            "core_course_get_contents": self.contents,
            "core_course_get_courses_by_field": {"courses": [COURSE]},
            "mod_url_get_urls_by_courses": {"urls": [
                {"id": 201, "coursemodule": 101, "externalurl": file_url("url-clip.mp4")}]},
            "mod_page_get_pages_by_courses": {"pages": [
                {"id": 202, "coursemodule": 102, "content": page}]},
        }

    def open(self, request, timeout=None):
        self.requests.append(request)
        parsed = urlsplit(request.full_url)
        assert parsed.scheme == "https" and parsed.netloc == "moodle.ffhs.ch", parsed.netloc
        if parsed.path == "/webservice/rest/server.php":
            args = parse_qs(request.data.decode("utf-8"))
            assert args["wstoken"] == [TOKEN]
            function = args["wsfunction"][0]
            assert function in self.api, function
            return Response(json.dumps(self.api[function]).encode("utf-8"))
        assert parsed.path.startswith("/webservice/pluginfile.php" + BASE), parsed.path
        assert parse_qs(parsed.query)["token"] == [TOKEN]
        name = parsed.path.rsplit("/", 1)[-1]
        assert name in FILES, "Unexpected file request: " + name
        self.file_requests.append(request)
        if name == self.broken_file:
            raise urllib.error.HTTPError(request.full_url, 404, "Not found", {}, io.BytesIO(b""))
        if request.get_header("If-modified-since"):
            raise urllib.error.HTTPError(request.full_url, 304, "Not modified", {}, io.BytesIO(b""))
        return Response(FILES[name], "application/pdf" if name.endswith(".pdf") else "video/mp4")


class ExportMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffhs-export-media-")
        self.addCleanup(self.temp.cleanup)
        self.dest = Path(self.temp.name)
        self.opener = FixtureOpener()
        self.client = MoodleClient(SITE, TOKEN, min_interval=0, retries=0)
        self.client.site_info_data = {"userid": 1}
        self.client.functions = set(self.opener.api)
        self.client._opener = self.opener

    def export(self, **options):
        summary = Exporter(self.client, ExportOptions(dest=str(self.dest), **options)).export([COURSE])
        self.assertFalse(summary["stopped"])
        result = summary["results"][0]
        self.assertEqual(result["status"], "ok", result)
        root = self.dest / result["folder"]
        return result, root

    def test_direct_url_and_embedded_files_are_downloaded_and_linked_locally(self):
        result, root = self.export()
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(self.opener.file_requests), 3)
        fulltext = (root / "course_fulltext.md").read_text(encoding="utf-8")
        for name, expected in FILES.items():
            paths = list(root.rglob(name))
            self.assertEqual(len(paths), 1, name)
            self.assertEqual(paths[0].read_bytes(), expected)
            relative = paths[0].relative_to(root).as_posix()
            self.assertIn("](" + quote(relative, safe="/") + ")", fulltext)
            self.assertNotIn("](" + file_url(name) + ")", fulltext)
        self.assertNotIn("@@FILE:", fulltext)
        self.assertTrue((self.dest / "index.md").is_file())
        self.assertTrue((root / "verifica_corso.md").is_file())
        for note in root.rglob("*.md"):
            self.assertNotIn(TOKEN, note.read_text(encoding="utf-8"))

        missing = (root / "elementi_mancanti.md").read_text(encoding="utf-8")
        self.assertIn("YouTube", missing)
        self.assertIn("https://www.youtube.com/watch?v=abcdefghijk", missing)
        self.assertIn("playlist.m3u8", missing)
        self.assertIn("manifest.mpd", missing)
        self.assertIn("segmenti video non sono stati scaricati", missing)
        self.assertFalse(list(root.rglob("*.m3u8")))
        self.assertFalse(list(root.rglob("*.mpd")))
        inventory = (root / "video_e_media.md").read_text(encoding="utf-8")
        self.assertEqual(inventory.count("| Scaricato |"), 2)
        self.assertIn("url-clip.mp4", inventory)
        self.assertIn("embedded-clip.mp4", inventory)
        self.assertIn("[Apri file]", inventory)
        self.assertIn("[Originale]", inventory)
        self.assertIn("| Solo link |", inventory)
        self.assertIn("playlist.m3u8", inventory)
        self.assertIn("manifest.mpd", inventory)
        self.assertIn("YouTube", inventory)
        self.assertEqual(verify_archive(self.dest), 0)

    def test_course_summary_media_are_included_in_missing_and_inventory(self):
        self.opener.contents[0]["modules"] = []
        self.opener.api["core_course_get_courses_by_field"] = {"courses": [{
            **COURSE, "summary": f'<video src="{file_url("url-clip.mp4")}"></video>'
            '<iframe src="https://www.youtube.com/embed/summaryclip"></iframe>'}]}
        result, root = self.export(include_videos=False)
        self.assertEqual(self.opener.file_requests, [])
        self.assertEqual(result["missing"], 2)
        missing = (root / "elementi_mancanti.md").read_text(encoding="utf-8")
        self.assertIn("YouTube", missing)
        self.assertIn("file video escluso dalle impostazioni", missing)
        inventory = (root / "video_e_media.md").read_text(encoding="utf-8")
        self.assertIn("| Escluso |", inventory)
        self.assertIn("| Solo link |", inventory)
        self.assertEqual(verify_archive(self.dest), 0)

    def test_structure_only_preparation_keeps_metadata_without_file_download_or_autologin(self):
        with patch.object(self.client, "download", side_effect=AssertionError("file download forbidden")), \
                patch("ffhs_archiver.exporter.VideoSession", side_effect=AssertionError("autologin forbidden")):
            result, root = self.export(download_files=False, download_videos=False, capture_pages=False)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.opener.file_requests, [])
        self.assertEqual(result["counts"]["skipped"], 3)
        self.assertEqual(list(root.rglob("*.mp4")), [])
        structure = json.loads((root / "course_structure.json").read_text(encoding="utf-8"))
        records = [child for section in structure["sections"] for child in section["children"]]
        names = {file["name"] for record in records for file in record.get("files", [])}
        self.assertEqual(names, set(FILES))
        self.assertEqual(verify_archive(self.dest), 0)

    def test_no_missing_claim_is_limited_to_the_processed_data(self):
        self.opener.contents[0]["modules"] = self.opener.contents[0]["modules"][:1]
        result, root = self.export()
        self.assertEqual(result["missing"], 0)
        missing = (root / "elementi_mancanti.md").read_text(encoding="utf-8")
        self.assertIn("Nessun elemento mancante rilevato nei dati elaborati", missing)
        self.assertIn("non garantisce", missing)
        self.assertNotIn("tutto ciò che è visibile per il tuo account è stato esportato", missing)

    def test_video_exclusion_keeps_pdf_and_reports_unavailable_offline_videos(self):
        result, root = self.export(include_videos=False)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(self.opener.file_requests), 1)
        self.assertTrue(self.opener.file_requests[0].full_url.split("?", 1)[0].endswith("lecture.pdf"))
        self.assertEqual(list(root.rglob("*.mp4")), [])
        self.assertEqual(next(root.rglob("lecture.pdf")).read_bytes(), FILES["lecture.pdf"])
        missing = (root / "elementi_mancanti.md").read_text(encoding="utf-8")
        self.assertIn("url-clip.mp4", missing)
        self.assertIn("embedded-clip.mp4", missing)
        self.assertIn("file video escluso dalle impostazioni", missing)
        self.assertEqual(result["counts"]["skipped"], 2)
        inventory = (root / "video_e_media.md").read_text(encoding="utf-8")
        self.assertEqual(inventory.count("| Escluso |"), 2)
        self.assertNotIn("[Apri file]", inventory)

    def test_download_error_is_visible_and_other_files_are_preserved(self):
        self.opener.broken_file = "url-clip.mp4"
        result, root = self.export()
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("HTTP 404", result["errors"][0])
        self.assertEqual(list(root.rglob("url-clip.mp4")), [])
        self.assertEqual(next(root.rglob("lecture.pdf")).read_bytes(), FILES["lecture.pdf"])
        missing = (root / "elementi_mancanti.md").read_text(encoding="utf-8")
        self.assertIn("url-clip.mp4", missing)
        self.assertIn("download non riuscito (HTTP 404)", missing)
        fulltext = (root / "course_fulltext.md").read_text(encoding="utf-8")
        self.assertIn(file_url("url-clip.mp4"), fulltext)
        inventory = (root / "video_e_media.md").read_text(encoding="utf-8")
        self.assertIn("| Download fallito |", inventory)
        self.assertIn("HTTP 404", inventory)

    def test_disk_error_is_reported_as_missing_and_does_not_claim_a_local_copy(self):
        real_download = self.client.download
        def download(url, *args, **kwargs):
            if url == file_url("url-clip.mp4"):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_download(url, *args, **kwargs)
        with patch.object(self.client, "download", side_effect=download):
            result, root = self.export()
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(list(root.rglob("url-clip.mp4")), [])
        self.assertEqual(next(root.rglob("lecture.pdf")).read_bytes(), FILES["lecture.pdf"])
        missing = (root / "elementi_mancanti.md").read_text(encoding="utf-8")
        self.assertIn("url-clip.mp4", missing)
        self.assertIn("errore sul disco", missing)
        inventory = (root / "video_e_media.md").read_text(encoding="utf-8")
        self.assertIn("| Download fallito |", inventory)
        self.assertIn("No space left on device", inventory)

    def test_reexport_uses_conditional_downloads_without_duplicate_binaries(self):
        _, root = self.export()
        before = {name: next(root.rglob(name)).stat().st_mtime_ns for name in FILES}
        self.opener.file_requests.clear()
        result, again = self.export()
        self.assertEqual(root, again)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(self.opener.file_requests), 3)
        self.assertTrue(all(req.get_header("If-modified-since") for req in self.opener.file_requests))
        self.assertEqual(result["counts"]["unchanged"], 3)
        for name, expected in FILES.items():
            copies = list(root.rglob(name))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_bytes(), expected)
            self.assertEqual(copies[0].stat().st_mtime_ns, before[name])
        self.assertFalse(list(root.rglob("*.new")))
        self.assertFalse(list(root.rglob("*.part")))

    def test_unknown_api_size_obeys_limit_with_or_without_content_length(self):
        original_open = self.opener.open
        for no_length in (False, True):
            with self.subTest(no_content_length=no_length):
                def open_response(request, **kwargs):
                    response = original_open(request, **kwargs)
                    if no_length and urlsplit(request.full_url).path.endswith("url-clip.mp4"):
                        response.headers.pop("Content-Length", None)
                    return response
                with patch.dict(FILES, {"url-clip.mp4": b"x" * (1024 * 1024 + 1)}), \
                        patch.object(self.opener, "open", side_effect=open_response):
                    result, root = self.export(max_file_mb=1)
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["counts"]["skipped"], 1)
                self.assertEqual(list(root.rglob("url-clip.mp4")), [])
                self.assertFalse(list(root.rglob("*.part")))
                self.assertIn("file più grande di 1 MB", (root / "elementi_mancanti.md").read_text())
                self.assertIn("| Escluso |", (root / "video_e_media.md").read_text())


if __name__ == "__main__":
    unittest.main()

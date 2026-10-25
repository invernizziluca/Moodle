"""Moodle file URLs with slash arguments disabled; no network or real tokens."""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffhs_archiver.moodle import MoodleClient, MoodleError


SITE = "https://moodle.ffhs.ch"
TOKEN = "offline-file-url-test"
FILE_PATH = "/12/mod_resource/content/3/a + %2F.pdf"


class Response(io.BytesIO):
    def __init__(self, data, content_type):
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}


class QueryFileUrlsTests(unittest.TestCase):
    def setUp(self):
        self.client = MoodleClient(SITE, TOKEN, min_interval=0, retries=0)

    def test_slash_and_query_forms_have_the_same_stable_identity(self):
        slash = SITE + "/pluginfile.php/12/mod_resource/content/3/a%20%2B%20%252F.pdf"
        for endpoint in ("/pluginfile.php", "/webservice/pluginfile.php"):
            query = SITE + endpoint + "?" + urlencode({"file": FILE_PATH})
            self.assertTrue(self.client.is_file_url(query))
            self.assertEqual(self.client.file_key(query), self.client.file_key(slash))
            self.assertEqual(self.client.file_key(query), FILE_PATH[1:])

    def test_query_tokenization_keeps_query_access_and_replaces_all_tokens(self):
        query = urlencode([("file", FILE_PATH), ("forcedownload", "1"), ("token", "obsolete"),
                           ("Token", "obsolete-too")])
        result = self.client.tokenized_file_url(SITE + "/pluginfile.php?" + query + "#viewer")
        parsed = urlsplit(result)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "moodle.ffhs.ch")
        self.assertEqual(parsed.path, "/webservice/pluginfile.php")
        self.assertEqual(parsed.fragment, "")
        self.assertEqual(parse_qs(parsed.query), {"file": [FILE_PATH], "forcedownload": ["1"],
                                                 "token": [TOKEN]})
        self.assertIn("%252F.pdf", result)
        self.assertIn("%2B", result)

    def test_tokenpluginfile_query_matches_the_existing_temporary_key_form(self):
        slash = SITE + "/tokenpluginfile.php/temporary-key/12/mod_page/content/0/movie.mp4"
        query = SITE + "/tokenpluginfile.php?" + urlencode({
            "file": "/temporary-key/12/mod_page/content/0/movie.mp4"})
        self.assertEqual(self.client.file_key(query), self.client.file_key(slash))
        parsed = urlsplit(self.client.tokenized_file_url(query))
        self.assertEqual(parse_qs(parsed.query)["file"], ["/12/mod_page/content/0/movie.mp4"])
        self.assertNotIn("temporary-key", self.client.tokenized_file_url(query))

    def test_non_file_and_cross_site_urls_do_not_receive_the_token(self):
        invalid = [
            "https://other.example/pluginfile.php?file=/12/x/y/0/file.pdf",
            SITE + ".other.example/pluginfile.php?file=/12/x/y/0/file.pdf",
            SITE + "/mod/page/view.php?file=/12/x/y/0/file.pdf",
            SITE + "/pluginfile.php?file=relative/path.pdf",
            SITE + "/pluginfile.php?file=/",
            SITE + "/pluginfile.php?file=/1/x&file=/2/y",
            SITE + "/pluginfile.php?forcedownload=1",
        ]
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(self.client.is_file_url(url))
                with self.assertRaises(MoodleError):
                    self.client.tokenized_file_url(url)

    def test_query_file_downloads_write_actual_pdf_and_video_bytes(self):
        files = {"course.pdf": (b"%PDF-1.4\nOffline fixture\n%%EOF\n", "application/pdf"),
                 "clip.mp4": (b"\x00\x00\x00\x18ftypmp42offline-video", "video/mp4")}
        requests = []

        class Opener:
            def open(_self, request, timeout=None):
                parsed = urlsplit(request.full_url)
                self.assertEqual(parsed.netloc, "moodle.ffhs.ch")
                self.assertEqual(parsed.path, "/webservice/pluginfile.php")
                params = parse_qs(parsed.query)
                self.assertEqual(params["token"], [TOKEN])
                name = params["file"][0].rsplit("/", 1)[-1]
                self.assertIn(name, files)
                requests.append(name)
                return Response(*files[name])

        self.client._opener = Opener()
        with tempfile.TemporaryDirectory(prefix="ffhs-query-files-") as folder:
            for name, (data, _) in files.items():
                url = SITE + "/pluginfile.php?" + urlencode({"file": "/12/mod_resource/content/0/" + name})
                target = Path(folder) / name
                result = self.client.download(url, target)
                self.assertEqual(result.status, "downloaded")
                self.assertEqual(result.size, len(data))
                self.assertEqual(target.read_bytes(), data)
                self.assertFalse(target.with_name(target.name + ".part").exists())
        self.assertCountEqual(requests, files)


if __name__ == "__main__":
    unittest.main()

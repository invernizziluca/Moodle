"""Integration tests against a local test Moodle (skipped when it is not running).

Requires FFHS_ARCHIVER_HOME pointing at a home where tests/e2e_login.py stored a token.
"""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffhs_archiver.moodle import MoodleClient, TokenInvalidError  # noqa: E402
from ffhs_archiver.session import Session  # noqa: E402


def _client():
    if not os.environ.get("FFHS_ARCHIVER_HOME"):
        return None
    creds = Session().saved_credentials()
    if not creds:
        return None
    client = MoodleClient(creds.site, creds.token)
    try:
        client.site_info()
    except Exception:
        return None
    return client


CLIENT = _client()


@unittest.skipIf(CLIENT is None, "local test Moodle not available")
class DownloadTests(unittest.TestCase):
    def setUp(self):
        courses = CLIENT.call("core_enrol_get_users_courses", userid=CLIENT.site_info_data["userid"])
        hrm = next(c for c in courses if "HRM4" in c["fullname"])
        contents = CLIENT.call("core_course_get_contents", courseid=hrm["id"])
        files = [c for s in contents for m in s["modules"] for c in m.get("contents", [])
                 if c.get("type") == "file" and c.get("filename", "").endswith(".pdf")]
        self.file = max(files, key=lambda f: f["filesize"])
        self.tmp = Path(tempfile.mkdtemp())

    def test_resume_with_range(self):
        full = self.tmp / "full.pdf"
        CLIENT.download(self.file["fileurl"], full, expected_size=self.file["filesize"])
        data = full.read_bytes()
        self.assertEqual(len(data), self.file["filesize"])
        target = self.tmp / "resumed.pdf"
        (self.tmp / "resumed.pdf.part").write_bytes(data[:300])
        seen = []
        original = CLIENT._opener.open

        def spy(req, *a, **kw):
            seen.append(dict(req.header_items()))
            return original(req, *a, **kw)

        CLIENT._opener.open = spy
        try:
            CLIENT.download(self.file["fileurl"], target, expected_size=self.file["filesize"])
        finally:
            CLIENT._opener.open = original
        self.assertEqual(seen[0].get("Range"), "bytes=300-")
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), hashlib.sha256(data).hexdigest())
        self.assertFalse((self.tmp / "resumed.pdf.part").exists())

    def test_conditional_get(self):
        first = CLIENT.download(self.file["fileurl"], self.tmp / "a.pdf")
        self.assertTrue(first.last_modified)
        again = CLIENT.download(self.file["fileurl"], self.tmp / "b.pdf", if_modified_since=first.last_modified)
        self.assertEqual(again.status, "not_modified")

    def test_invalid_token_detected_on_files(self):
        bad = MoodleClient(CLIENT.site_url, "0" * 32)
        with self.assertRaises(TokenInvalidError):
            bad.download(self.file["fileurl"], self.tmp / "x.pdf")
        with self.assertRaises(TokenInvalidError):
            bad.call("core_webservice_get_site_info")


if __name__ == "__main__":
    unittest.main()

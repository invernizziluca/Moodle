"""Login security against the local test Moodle (skipped when it is not reachable).

Run with FFHS_ARCHIVER_DEV_SITE=http://127.0.0.1:8085 (and a throw-away FFHS_ARCHIVER_HOME)."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from browser_sim import browser_login  # noqa: E402
from ffhs_archiver.moodle import DEV_SITE_ENV, MoodleClient  # noqa: E402
from ffhs_archiver.session import LEGACY_FILES, Session  # noqa: E402
from ffhs_archiver.sso import SSOError  # noqa: E402

SITE = os.environ.get(DEV_SITE_ENV, "")


def _site_up() -> bool:
    if not SITE:
        return False
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(SITE + "/login/index.php", timeout=5):
            return True
    except Exception:
        return False


@unittest.skipUnless(_site_up(), "local test Moodle not available")
class LoginTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fma"))
        self.session = Session(self.dir)
        self.received = []

    def tearDown(self):
        self.session.end_login()

    def start(self):
        return self.session.start_login(open_browser=False, use_handler=True,
                                        on_link=lambda u: self.received.append(u) or True)

    def helper(self, link, spec):
        env = dict(os.environ, FFHS_ARCHIVER_HOME=str(self.dir))
        return subprocess.run([sys.executable, "-I", str(ROOT / "FFHS Moodle Archiver.pyw"), "--sso-callback",
                               link, "--sso-channel", spec], env=env, timeout=60, capture_output=True).returncode

    def wait_received(self, n=1):
        deadline = time.time() + 10
        while len(self.received) < n and time.time() < deadline:
            time.sleep(0.05)

    def test_full_login_through_helper_process(self):
        attempt = self.start()
        self.assertIn("confirmed=1", attempt.url)
        link = browser_login(attempt.url, "luca", "Luca#2026test")
        self.assertEqual(self.helper(link, attempt.channel.spec.encode()), 0)
        self.wait_received()
        creds, client = self.session.finish_login(self.received[0], require_signature=True)
        self.assertEqual(len(creds.token), 32)
        self.assertIsNone(self.session.attempt)
        self.assertFalse(Path(attempt.channel.address).exists())  # channel closed
        self.assertEqual(self.session.store.load().token, creds.token)
        if os.name != "nt":
            self.assertEqual(os.stat(self.session.store.path).st_mode & 0o777, 0o600)
        self.assertFalse(any((self.dir / name).exists() for name in LEGACY_FILES))
        # the same link cannot be used again (passport valid once)
        with self.assertRaises(SSOError):
            self.session.finish_login(link, require_signature=True)
        # after the login the helper finds nobody waiting: it exits without delivering
        self.assertEqual(self.helper(link, attempt.channel.spec.encode()), 4)

    def test_link_of_another_attempt_is_refused(self):
        first = self.start()
        old_link = browser_login(first.url, "luca", "Luca#2026test")
        self.start()  # new attempt: the old passport is gone
        with self.assertRaises(SSOError) as ctx:
            self.session.finish_login(old_link, require_signature=True)
        self.assertIn("firma", str(ctx.exception))

    def test_bare_key_only_when_pasted(self):
        attempt = self.start()
        link = browser_login(attempt.url, "luca", "Luca#2026test")
        creds, _ = self.session.finish_login(link, require_signature=True)
        self.start()
        with self.assertRaises(SSOError):
            self.session.finish_login(creds.token, require_signature=True)
        creds2, _ = self.session.finish_login(creds.token, require_signature=False)
        self.assertEqual(creds2.token, creds.token)

    def test_expired_attempt(self):
        attempt = self.start()
        link = browser_login(attempt.url, "luca", "Luca#2026test")
        attempt.expires = time.time() - 1
        with self.assertRaises(SSOError) as ctx:
            self.session.finish_login(link, require_signature=True)
        self.assertIn("scaduto", str(ctx.exception))
        self.assertIsNone(self.session.attempt)

    def test_unexpected_wwwroot_aborts(self):
        cfg = MoodleClient(SITE).public_config()
        cfg["wwwroot"] = "https://evil.example"
        with mock.patch.object(MoodleClient, "public_config", return_value=cfg):
            with self.assertRaises(SSOError):
                self.start()
        cfg["wwwroot"] = SITE
        cfg["launchurl"] = "https://evil.example/admin/tool/mobile/launch.php"
        with mock.patch.object(MoodleClient, "public_config", return_value=cfg):
            attempt = self.start()
        self.assertTrue(attempt.url.startswith(SITE + "/admin/tool/mobile/launch.php?"))

    def test_leftover_files_of_v01_are_removed(self):
        for name in LEGACY_FILES:
            (self.dir / name).write_text("moodlemobile://token=old")
        done = self.session.cleanup_leftovers()
        self.assertEqual(len([d for d in done if "0.1" in d]), len(LEGACY_FILES))
        self.assertFalse(any((self.dir / name).exists() for name in LEGACY_FILES))


if __name__ == "__main__":
    unittest.main()

"""Security tests: link handler registration (simulated registry), login channel, browser helper process,
token/link checks, site lock, read-only functions, log redaction, archive scrubbing, path safety."""

import io
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from multiprocessing.connection import Listener
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_winreg import FakeWinreg  # noqa: E402
from ffhs_archiver import ipc  # noqa: E402
from ffhs_archiver.exporter import CourseExport, ExportOptions, Exporter, Item  # noqa: E402
from ffhs_archiver.moodle import (DEV_SITE_ENV, READ_ONLY_FUNCTIONS, MoodleClient, MoodleError,  # noqa: E402
                                  RedactingFilter, allowed_site, redact)
from ffhs_archiver.privacy import Pseudonymizer, scrub_secrets  # noqa: E402
from ffhs_archiver.protocol import BACKUP_NAME, COMMAND_PATH, KEY_PATH, LEGACY_STATE, MARKER, ProtocolRegistrar  # noqa: E402
from ffhs_archiver.sso import SSOError, extract_token, handle_callback, handler_command, is_valid_callback_url  # noqa: E402

SITE = "https://moodle.ffhs.ch"


def foreign_handler(reg: FakeWinreg) -> None:
    """A moodlemobile:// registration of another application, with several value types and subkeys."""
    with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, KEY_PATH) as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, "URL:Moodle Desktop")
        reg.SetValueEx(k, "URL Protocol", 0, reg.REG_SZ, "")
        reg.SetValueEx(k, "EditFlags", 0, reg.REG_DWORD, 2)
        reg.SetValueEx(k, "Blob", 0, reg.REG_BINARY, b"\x00\xffabc")
        reg.SetValueEx(k, "Tags", 0, reg.REG_MULTI_SZ, ["a", "b"])
    with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, KEY_PATH + r"\DefaultIcon") as k:
        reg.SetValueEx(k, "", 0, reg.REG_EXPAND_SZ, "%ProgramFiles%\\Moodle\\moodle.exe,0")
    with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, COMMAND_PATH) as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, '"C:\\Program Files\\Moodle\\moodle.exe" "%1"')


class RegistrarTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.reg = FakeWinreg()
        self.r = ProtocolRegistrar(self.dir, reg=self.reg)

    def command(self):
        with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, COMMAND_PATH) as k:
            return self.reg.QueryValueEx(k, "")[0]

    def test_absent_key_is_removed_again(self):
        self.r.register("CMD-A", "a" * 16, time.time() + 900)
        self.assertEqual(self.r.owner()["channel"], "a" * 16)
        self.assertEqual(self.command(), "CMD-A")
        self.assertEqual(self.r.restore(only_channel="b" * 16), "other-attempt")
        self.assertIsNotNone(self.reg.dump(KEY_PATH))
        self.assertEqual(self.r.restore(only_channel="a" * 16), "removed")
        self.assertIsNone(self.reg.dump(KEY_PATH))
        self.assertFalse((self.dir / BACKUP_NAME).exists())

    def test_foreign_registration_restored_exactly(self):
        foreign_handler(self.reg)
        before = self.reg.dump(KEY_PATH)
        self.assertIsNone(self.r.owner())
        self.r.register("CMD-A", "a" * 16, time.time() + 900)
        self.assertNotEqual(self.reg.dump(KEY_PATH), before)
        self.assertIsNone(self.reg.dump(KEY_PATH + r"\DefaultIcon"))  # nothing of the other app is mixed in
        self.assertEqual(self.r.restore(), "restored")
        self.assertEqual(self.reg.dump(KEY_PATH), before)

    def test_crash_then_cleanup_at_next_start(self):
        foreign_handler(self.reg)
        before = self.reg.dump(KEY_PATH)
        self.r.register("CMD-A", "a" * 16, time.time() + 900)
        # program killed: a new process starts
        fresh = ProtocolRegistrar(self.dir, reg=self.reg)
        self.assertEqual(fresh.cleanup_leftover(), "restored")
        self.assertEqual(self.reg.dump(KEY_PATH), before)
        self.assertEqual(fresh.cleanup_leftover(), "clean")

    def test_second_registration_keeps_original_backup(self):
        foreign_handler(self.reg)
        before = self.reg.dump(KEY_PATH)
        self.r.register("CMD-A", "a" * 16, time.time() + 900)
        self.r.register("CMD-B", "b" * 16, time.time() + 900)  # leftover of ours must not become "previous"
        self.assertEqual(self.command(), "CMD-B")
        self.assertEqual(self.r.restore(only_channel="b" * 16), "restored")
        self.assertEqual(self.reg.dump(KEY_PATH), before)

    def test_foreign_takeover_is_never_touched(self):
        self.r.register("CMD-A", "a" * 16, time.time() + 900)
        self.r._delete_tree(KEY_PATH)
        foreign_handler(self.reg)  # another app registered meanwhile
        before = self.reg.dump(KEY_PATH)
        self.assertEqual(self.r.restore(), "not-ours")
        self.assertEqual(self.reg.dump(KEY_PATH), before)
        self.assertFalse((self.dir / BACKUP_NAME).exists())

    def test_interrupted_between_delete_and_create(self):
        foreign_handler(self.reg)
        before = self.reg.dump(KEY_PATH)
        self.r.register("CMD-A", "a" * 16, time.time() + 900)
        self.r._delete_tree(KEY_PATH)  # simulate power cut right after the delete of a new registration
        self.assertEqual(ProtocolRegistrar(self.dir, reg=self.reg).cleanup_leftover(), "restored")
        self.assertEqual(self.reg.dump(KEY_PATH), before)

    def test_failure_while_writing_rolls_back(self):
        foreign_handler(self.reg)
        before = self.reg.dump(KEY_PATH)
        self.reg.fail_on_set = lambda name, data: isinstance(data, str) and data.startswith("CMD")
        with self.assertRaises(PermissionError):
            self.r.register("CMD-A", "a" * 16, time.time() + 900)
        self.reg.fail_on_set = None
        self.assertEqual(self.reg.dump(KEY_PATH), before)

    def test_backup_not_writable_aborts_before_any_change(self):
        foreign_handler(self.reg)
        before = self.reg.dump(KEY_PATH)
        (self.dir / BACKUP_NAME).mkdir()  # cannot be replaced by a file
        calls = self.reg.calls
        with self.assertRaises(OSError):
            self.r.register("CMD-A", "a" * 16, time.time() + 900)
        self.assertEqual(self.reg.dump(KEY_PATH), before)
        self.assertLess(self.reg.calls - calls, 10)  # only reads happened

    def test_legacy_v01_registration(self):
        with self.reg.CreateKeyEx(self.reg.HKEY_CURRENT_USER, COMMAND_PATH) as k:
            self.reg.SetValueEx(k, "", 0, self.reg.REG_SZ,
                                '"C:\\py\\pythonw.exe" "C:\\FFHS Moodle Archiver.pyw" --sso-callback "%1"')
        (self.dir / LEGACY_STATE).write_text(json.dumps(
            {"supported": True, "existed": True, "command": '"C:\\Other\\app.exe" "%1"'}))
        self.assertTrue(self.r.owner()["legacy"])
        self.assertEqual(self.r.cleanup_leftover(), "restored")
        self.assertEqual(self.command(), '"C:\\Other\\app.exe" "%1"')
        self.assertIsNone(self.r.owner())
        self.assertFalse((self.dir / LEGACY_STATE).exists())

    def test_marker_and_status(self):
        self.r.register("CMD-A", "a" * 16, time.time() + 900)
        with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, KEY_PATH) as k:
            marker = json.loads(self.reg.QueryValueEx(k, MARKER)[0])
        self.assertEqual(marker["channel"], "a" * 16)
        self.assertIn("attiva", self.r.status())
        self.r.restore()
        self.assertEqual(self.r.status(), "assente")

    def test_self_test_uses_only_its_test_key(self):
        foreign_handler(self.reg)
        before = self.reg.dump(KEY_PATH)
        self.assertIn("riusciti", self.r.self_test())
        self.assertIsNone(self.reg.dump(r"Software\FFHS Moodle Archiver"))
        self.assertEqual(self.reg.dump(KEY_PATH), before)


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fma"))
        self.got = []
        self.channel = ipc.LoginChannel(self.dir, lifetime=60)
        self.channel.start(lambda m: self.got.append(m) or True)
        self.addCleanup(self.channel.stop)

    def test_delivery_and_wrong_key(self):
        spec = self.channel.spec.encode()
        self.assertEqual(ipc.deliver(spec, "moodlemobile://token=abc", self.dir), "ok")
        forged = ipc.ChannelSpec(self.channel.spec.id, secrets.token_bytes(32), self.channel.spec.expires).encode()
        self.assertEqual(ipc.deliver(forged, "moodlemobile://token=evil", self.dir), "rejected")
        self.assertEqual(ipc.deliver(spec, "moodlemobile://token=again", self.dir), "ok")
        self.assertEqual(self.got, ["moodlemobile://token=abc", "moodlemobile://token=again"])

    def test_silent_client_does_not_block_the_channel(self):
        from multiprocessing.connection import Client
        old = ipc.TIMEOUT
        ipc.TIMEOUT = 0.5
        try:
            silent = Client(self.channel.address, self.channel.family)  # connects and never answers
            time.sleep(0.1)
            t0 = time.monotonic()
            self.assertEqual(ipc.deliver(self.channel.spec.encode(), "moodlemobile://token=x", self.dir,
                                         timeout=5), "ok")
            self.assertLess(time.monotonic() - t0, 4)
            silent.close()
        finally:
            ipc.TIMEOUT = old

    def test_oversized_message_rejected(self):
        self.assertEqual(ipc.deliver(self.channel.spec.encode(), "moodlemobile://token=" + "A" * 5000, self.dir),
                         "rejected")
        self.assertEqual(self.got, [])

    def test_stopped_channel_means_no_app(self):
        spec = self.channel.spec.encode()
        self.channel.stop()
        self.assertEqual(ipc.deliver(spec, "moodlemobile://token=abc", self.dir), "no-app")

    def test_impostor_server_never_receives_the_link(self):
        spec = ipc.ChannelSpec.new(60)
        address, family = ipc.channel_address(spec.id, self.dir)
        received = []

        def impostor():
            with Listener(address, family) as listener, listener.accept() as conn:
                conn.send_bytes(ipc.PROTO + secrets.token_bytes(32))
                conn.recv_bytes(128)
                conn.send_bytes(secrets.token_bytes(32))  # cannot compute the right answer
                if conn.poll(2):
                    try:
                        received.append(conn.recv_bytes(8192))
                    except EOFError:
                        pass

        t = threading.Thread(target=impostor)
        t.start()
        time.sleep(0.2)
        self.assertEqual(ipc.deliver(spec.encode(), "moodlemobile://token=SECRET", self.dir), "bad-peer")
        t.join(5)
        self.assertFalse(any(b"SECRET" in r for r in received))


class HelperProcessTests(unittest.TestCase):
    URL = "moodlemobile://token=" + "QUJD" * 20

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fma"))
        self.reg = FakeWinreg()
        self.registrar = ProtocolRegistrar(self.dir, reg=self.reg)
        self.messages = []

    def run_helper(self, url, spec):
        return handle_callback(["--sso-callback", url, "--sso-channel", spec], state_dir=self.dir,
                               registrar=self.registrar, notify=lambda text, warning=False: self.messages.append(text))

    def test_delivers_to_waiting_program(self):
        got = []
        channel = ipc.LoginChannel(self.dir, lifetime=60)
        channel.start(lambda m: got.append(m) or True)
        self.addCleanup(channel.stop)
        self.assertEqual(self.run_helper(self.URL, channel.spec.encode()), 0)
        self.assertEqual(got, [self.URL])
        self.assertEqual(self.messages, [])

    def test_expired_link_cleans_registry(self):
        spec = ipc.ChannelSpec(secrets.token_hex(8), secrets.token_bytes(32), int(time.time()) - 5)
        self.registrar.register("CMD", spec.id, spec.expires)
        self.assertEqual(self.run_helper(self.URL, spec.encode()), 3)
        self.assertIsNone(self.reg.dump(KEY_PATH))
        self.assertIn("scaduto", self.messages[0])

    def test_program_gone_cleans_registry_and_informs(self):
        spec = ipc.ChannelSpec.new(60)
        self.registrar.register("CMD", spec.id, spec.expires)
        self.assertEqual(self.run_helper(self.URL, spec.encode()), 4)
        self.assertIsNone(self.reg.dump(KEY_PATH))
        self.assertEqual(len(self.messages), 1)

    def test_link_clicked_after_login_ended_is_silent(self):
        spec = ipc.ChannelSpec.new(60)
        self.assertEqual(self.run_helper(self.URL, spec.encode()), 4)
        self.assertEqual(self.messages, [])

    def test_invalid_input_is_refused(self):
        spec = ipc.ChannelSpec.new(60).encode()
        for bad in ('moodlemobile://token=abc" --evil "x', "https://evil.example/?token=" + "A" * 30, "", "x" * 5000):
            self.assertEqual(self.run_helper(bad, spec), 2)
        self.assertEqual(self.run_helper(self.URL, "garbage"), 2)
        self.assertEqual(self.run_helper(self.URL, ""), 2)

    def test_handler_command_shape(self):
        spec = ipc.ChannelSpec.new(60).encode()
        cmd = handler_command(spec)
        self.assertIn('--sso-callback "%1"', cmd)
        self.assertTrue(cmd.endswith("--sso-channel " + spec))
        self.assertIn(" -I ", cmd)
        self.assertTrue(is_valid_callback_url(self.URL + "/"))


class TokenAndSiteTests(unittest.TestCase):
    def test_signature_binds_link_to_attempt(self):
        import base64
        import hashlib
        sig = hashlib.md5((SITE + "p1").encode()).hexdigest()
        link = "moodlemobile://token=" + base64.b64encode(f"{sig}:::{'a' * 32}:::{'p' * 32}".encode()).decode()
        res = extract_token(link, SITE, ["p1"])
        self.assertTrue(res.signature_ok)
        self.assertTrue(res.had_private_token)
        self.assertFalse(extract_token(link, SITE, ["p2"]).signature_ok)
        self.assertFalse(extract_token(link, SITE, []).signature_ok)
        with self.assertRaises(SSOError):
            extract_token("x" * 5000, SITE, ["p1"])

    def test_site_is_locked(self):
        old = os.environ.pop(DEV_SITE_ENV, None)
        try:
            self.assertEqual(allowed_site(), SITE)
            for bad in ("https://evil.example", "http://moodle.ffhs.ch", "https://moodle.ffhs.ch.evil.example"):
                with self.assertRaises(MoodleError):
                    MoodleClient(bad)
            os.environ[DEV_SITE_ENV] = "https://evil.example"
            self.assertEqual(allowed_site(), SITE)  # development override only for localhost
            os.environ[DEV_SITE_ENV] = "http://127.0.0.1:8085"
            self.assertEqual(allowed_site(), "http://127.0.0.1:8085")
        finally:
            os.environ.pop(DEV_SITE_ENV, None)
            if old:
                os.environ[DEV_SITE_ENV] = old

    def test_read_only_functions_only(self):
        old = os.environ.pop(DEV_SITE_ENV, None)
        try:
            client = MoodleClient(SITE, "t" * 32)
            for fn in ("core_user_update_users", "mod_assign_save_submission", "mod_quiz_start_attempt",
                       "mod_forum_add_discussion_post", "core_message_send_instant_messages"):
                with self.assertRaises(MoodleError) as ctx:
                    client.call(fn)
                self.assertEqual(ctx.exception.errorcode, "notreadonly")
            with self.assertRaises(MoodleError) as ctx:
                client._open(urllib.request.Request("https://evil.example/webservice/rest/server.php"))
            self.assertEqual(ctx.exception.errorcode, "blockedhost")
        finally:
            if old:
                os.environ[DEV_SITE_ENV] = old

    def test_every_called_function_is_whitelisted(self):
        used = set()
        for name in ("exporter.py", "verify.py", "session.py", "cli.py", "gui.py"):
            text = (ROOT / "ffhs_archiver" / name).read_text(encoding="utf-8")
            used |= set(re.findall(r"""(?:call|try_call)\(\s*["']((?:core|mod|tool)_[a-z0-9_]+)""", text))
            used |= set(re.findall(r'"(mod_[a-z0-9]+_get_[a-z0-9_]+)"', text))
        self.assertTrue(used)
        self.assertEqual(sorted(used - READ_ONLY_FUNCTIONS), [])
        self.assertFalse(any(re.search(r"_(save|add|update|delete|start|submit|process|send|create|set)_", f)
                             for f in READ_ONLY_FUNCTIONS))


class LoggingAndArchiveTests(unittest.TestCase):
    def test_log_filter_redacts_args_and_tracebacks(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        logger = logging.getLogger("redaction-test")
        logger.addHandler(handler)
        logger.propagate = False
        logger.warning("url %s", "https://m/x.php?token=SECRET1&a=1")
        try:
            raise ValueError("moodlemobile://token=SECRET2 passport=SECRET3")
        except ValueError:
            logger.exception("errore")
        logger.warning("cmd %s", "--sso-channel aaaa.bbbb.1234567890")
        out = stream.getvalue()
        for secret in ("SECRET1", "SECRET2", "SECRET3", "aaaa.bbbb"):
            self.assertNotIn(secret, out)
        self.assertIn("token=***", out)
        self.assertEqual(redact("tokenpluginfile.php/" + "a" * 32 + "/1/x"), "tokenpluginfile.php/***/1/x")

    def test_scrub_secrets(self):
        text = (f"[a]({SITE}/tokenpluginfile.php/{'0f' * 16}/12/mod_page/content/0/x.png) "
                f"[b]({SITE}/webservice/pluginfile.php/1/a/b/0/f.pdf?token=abc&forcedownload=1) "
                f"[c]({SITE}/pluginfile.php/1/f.pdf?forcedownload=1&token=abc) "
                f'"u": "https://m/p.php?x=1&amp;token=zz" sesskey=AbC123')
        out = scrub_secrets(text)
        self.assertNotIn("token=", out)
        self.assertNotIn("tokenpluginfile", out)
        self.assertNotIn("webservice/pluginfile", out)
        self.assertIn(f"{SITE}/pluginfile.php/12/mod_page/content/0/x.png", out)
        self.assertIn("f.pdf?forcedownload=1)", out)
        self.assertIn("sesskey=rimosso", out)

    def test_pseudonymizer(self):
        p = Pseudonymizer(True, keep_ids=[5, 9])
        self.assertEqual(p.name(5, "Io"), "Io")
        self.assertEqual(p.name(7, "Anna"), "Partecipante 1")
        self.assertEqual(p.name(8, "Ben"), "Partecipante 2")
        self.assertEqual(p.name(7, "Anna"), "Partecipante 1")
        self.assertEqual(p.name(11, "Prof", staff=True), "Prof")
        self.assertEqual(Pseudonymizer(False).name(7, "Anna"), "Anna")

    def test_paths_from_moodle_stay_inside_the_course_folder(self):
        old = os.environ.pop(DEV_SITE_ENV, None)
        try:
            exporter = Exporter(MoodleClient(SITE, "t" * 32), ExportOptions(dest=tempfile.mkdtemp()))
            ce = CourseExport(exporter, {"id": 1, "fullname": "Corso"}, "Corso")
            owner = Item(kind="activity", key="cm:1", title="A", rel_dir="01_S/01_A")
            for filepath, filename in (("/../../evil/", "../x.pdf"), ("/..\\..\\", "..\\y.pdf"),
                                       ("/./a/", "CON"), ("/", "..")):
                spec = ce._file_entry_spec({"fileurl": f"{SITE}/webservice/pluginfile.php/1/mod_folder/content/0/z",
                                            "filepath": filepath, "filename": filename, "filesize": 1},
                                           owner, "", "material", f"k{filepath}{filename}")
                parts = spec.rel_path.split("/")
                self.assertEqual(parts[:2], ["01_S", "01_A"], spec.rel_path)
                self.assertFalse(any(p in ("", ".", "..") or "\\" in p for p in parts), spec.rel_path)
        finally:
            if old:
                os.environ[DEV_SITE_ENV] = old


if __name__ == "__main__":
    unittest.main()

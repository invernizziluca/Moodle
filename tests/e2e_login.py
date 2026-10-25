"""Log in to the local test Moodle exactly like on Windows, with a simulated browser:
program waits on its login channel -> browser login -> Moodle link -> helper process started with the link
(what Windows does through the moodlemobile:// handler) -> channel -> signature check -> token stored.

Usage: FFHS_ARCHIVER_HOME=... FFHS_ARCHIVER_DEV_SITE=http://127.0.0.1:8085 \\
       python tests/e2e_login.py <archive_dir> [username] [password]
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from browser_sim import browser_login  # noqa: E402
from ffhs_archiver.appdata import Settings  # noqa: E402
from ffhs_archiver.session import Session  # noqa: E402


def login(archive: str, user: str = "luca", password: str = "Luca#2026test"):
    settings = Settings.load()
    settings.archive_dir = archive
    settings.save()
    session = Session()
    received = []
    attempt = session.start_login(open_browser=False, use_handler=True, on_link=lambda u: received.append(u) or True)
    link = browser_login(attempt.url, user, password)
    helper = subprocess.run([sys.executable, "-I", str(ROOT / "FFHS Moodle Archiver.pyw"), "--sso-callback", link,
                             "--sso-channel", attempt.channel.spec.encode()], env=os.environ, timeout=60)
    deadline = time.time() + 10
    while not received and time.time() < deadline:
        time.sleep(0.05)
    if helper.returncode != 0 or not received:
        raise SystemExit(f"helper failed (exit {helper.returncode})")
    return session.finish_login(received[0], require_signature=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    creds, _ = login(*args)
    print(f"logged in as {creds.fullname} ({creds.release}), token storage: {creds.protection}")

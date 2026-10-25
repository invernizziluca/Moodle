"""Login through the normal browser (FFHS-ID / Microsoft Authenticator), like the official Moodle app.

Flow (Moodle's documented "login via browser" for apps, admin/tool/mobile/launch.php):
1. The program opens  https://moodle.ffhs.ch/admin/tool/mobile/launch.php?service=moodle_mobile_app
   &passport=<random>&urlscheme=moodlemobile[&oauthsso=<FFHS-ID>]&confirmed=1  in the user's browser.
   The user logs in there as usual; password and MFA never pass through this program.
2. Moodle answers with  moodlemobile://token=<base64(md5(wwwroot + passport) ::: TOKEN [::: private])>.
3. The link reaches the waiting program window either through the temporary link handler
   (protocol.py + ipc.py: authenticated, in memory) or because the user pastes it.
4. The signature md5(wwwroot + passport) proves that the link answers *this* login attempt: the passport
   is random, kept only in memory, valid once and for 15 minutes. Only the TOKEN is kept; the
   optional "private token" (which could open a full browser session) is discarded.

Notes:
- ``confirmed=1`` only makes Moodle show a page with the app link instead of a silent redirect (Moodle uses
  it after an e-mail confirmation); it does not skip any security check (see launch.php).
- Moodle forces the scheme "moodlemobile" by default (tool_mobile/forcedurlscheme): tools that wait for
  another scheme (e.g. Moodle-DL's moodledl://) never receive the link, hence their blank page.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

SCHEME = "moodlemobile"
LOGIN_LIFETIME = 15 * 60  # seconds; Moodle's own launch cookie also lasts 15 minutes
_CALLBACK_RE = re.compile(r"moodlemobile://token=[A-Za-z0-9+/=%_\-]{20,2048}/?", re.I)


class SSOError(Exception):
    pass


@dataclass
class TokenResult:
    token: str
    signature_ok: bool | None  # None when a bare 32-character key was pasted (nothing to verify)
    had_private_token: bool = False


def new_passport() -> str:
    return secrets.token_hex(16)


def launch_url(site_url: str, passport: str, oauth_issuer_id: int | None = None,
               launch_endpoint: str = "") -> str:
    params = {"service": "moodle_mobile_app", "passport": passport, "urlscheme": SCHEME, "confirmed": "1"}
    if oauth_issuer_id:
        params["oauthsso"] = str(oauth_issuer_id)
    base = launch_endpoint or f"{site_url.rstrip('/')}/admin/tool/mobile/launch.php"
    return base + "?" + urllib.parse.urlencode(params)


def oauth_issuer_from_providers(providers: list, preferred_name: str = "FFHS-ID") -> int | None:
    """OAuth 2 issuer id (e.g. FFHS-ID) from tool_mobile_get_public_config identityproviders."""
    candidates = []
    for p in providers or []:
        m = re.search(r"/auth/oauth2/login\.php\?(?:.*&)?id=(\d+)", p.get("url", ""))
        if m:
            candidates.append((p.get("name", ""), int(m.group(1))))
    for name, issuer in candidates:
        if name.strip().lower() == preferred_name.lower():
            return issuer
    return candidates[0][1] if len(candidates) == 1 else None


def _b64decode_loose(data: str) -> bytes:
    data = data.strip().replace(" ", "+").replace("-", "+").replace("_", "/")
    data += "=" * (-len(data) % 4)
    return base64.b64decode(data, validate=False)


def extract_token(text: str, wwwroot: str, passports: list) -> TokenResult:
    """Parse the moodlemobile://token=… link (or its token=… part) or a bare 32-character key."""
    text = (text or "").strip().strip('"').strip("'").strip("<>").strip()
    if not text:
        raise SSOError("Nessun testo incollato.")
    if len(text) > 4096:
        raise SSOError("Il testo incollato è troppo lungo per essere un link di accesso.")
    if re.fullmatch(r"[0-9a-fA-F]{32}", text):
        return TokenResult(token=text.lower(), signature_ok=None)
    text = urllib.parse.unquote(text)
    m = re.search(r"token=([A-Za-z0-9+/=_\-\s]+)", text)
    payload = (m.group(1) if m else text).strip().rstrip("/").strip()
    try:
        decoded = _b64decode_loose(payload).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise SSOError("Il link non contiene un token Moodle valido.") from None
    parts = decoded.split(":::")
    if len(parts) < 2 or not re.fullmatch(r"[0-9a-fA-F]{32}", parts[1].strip()):
        raise SSOError("Il link non contiene un token Moodle valido.")
    signature, token = parts[0].strip().lower(), parts[1].strip().lower()
    ok = any(secrets.compare_digest(hashlib.md5((wwwroot + p).encode("utf-8")).hexdigest(), signature)
             for p in passports)
    return TokenResult(token=token, signature_ok=ok, had_private_token=len(parts) > 2 and bool(parts[2]))


def is_valid_callback_url(url: str) -> bool:
    return bool(url) and _CALLBACK_RE.fullmatch(url.strip()) is not None


# --------------------------------------------------------------------------- helper process (browser → app)

def handler_command(channel_spec: str) -> str:
    """Command Windows runs when the browser opens moodlemobile://… during a login attempt."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --sso-callback "%1" --sso-channel {channel_spec}'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    launcher = Path(__file__).resolve().parent.parent / "FFHS Moodle Archiver.pyw"
    # -I: isolated interpreter (ignores PYTHON* variables and user site-packages).
    if launcher.exists():
        return f'"{exe}" -I "{launcher}" --sso-callback "%1" --sso-channel {channel_spec}'
    return f'"{exe}" -I -m ffhs_archiver --sso-callback "%1" --sso-channel {channel_spec}'


def show_message(text: str, warning: bool = False) -> None:
    """Message for the browser-launched helper (it has no window of its own)."""
    if os.name == "nt":
        try:
            import ctypes

            flags = (0x30 if warning else 0x40) | 0x10000 | 0x40000  # icon | SETFOREGROUND | TOPMOST
            ctypes.windll.user32.MessageBoxW(None, text, "FFHS Moodle Archiver", flags)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def _arg_after(argv: list, flag: str) -> str:
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return ""


def handle_callback(argv: list, state_dir=None, registrar=None, notify=show_message) -> int:
    """Entry point of the helper started by the browser. It never starts the program window and never
    writes the link anywhere: it only hands it to a program that is waiting for this login attempt."""
    from .appdata import app_dir
    from .ipc import ChannelSpec, deliver
    from .protocol import ProtocolRegistrar

    state_dir = Path(state_dir) if state_dir else app_dir()
    registrar = registrar or ProtocolRegistrar(state_dir)
    url = _arg_after(argv, "--sso-callback").strip()
    spec_text = _arg_after(argv, "--sso-channel")
    try:
        spec = ChannelSpec.parse(spec_text)
    except ValueError:
        if registrar.owner() is not None:
            registrar.restore()  # leftover of an older version: clean it up
        notify("Questo link di accesso non è più valido.\n\nApri FFHS Moodle Archiver e clicca "
               "«Accedi con FFHS-ID».", True)
        return 2
    if not is_valid_callback_url(url):
        notify("Il link ricevuto dal browser non è un link di accesso Moodle valido: ignorato.", True)
        return 2
    if time.time() > spec.expires:
        registrar.restore(only_channel=spec.id)
        notify("Il tempo per completare l'accesso (15 minuti) è scaduto.\n\nTorna in FFHS Moodle Archiver e "
               "clicca di nuovo «Accedi con FFHS-ID».", True)
        return 3
    result = deliver(spec_text, url, state_dir)
    if result == "ok":
        return 0
    if result == "no-app":
        current = registrar.owner()
        if current is not None and current.get("channel") == spec.id:
            # The program is gone (closed or crashed) but its registration is still there.
            registrar.restore(only_channel=spec.id)
            notify("FFHS Moodle Archiver non è più in attesa di questo accesso.\n\nApri il programma e "
                   "clicca «Accedi con FFHS-ID».", True)
        return 4  # otherwise the login already ended normally (e.g. link clicked twice)
    notify("Il link di accesso non è stato accettato dal programma. Ripeti l'accesso.", True)
    return 5

"""Login session shared by the GUI and the command line."""

from __future__ import annotations

import logging
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .appdata import app_dir
from .ipc import LoginChannel
from .moodle import MoodleClient, MoodleError, TokenInvalidError, allowed_site, normalize_site_url
from .protocol import ProtocolRegistrar
from .securestore import Credentials, TokenStore
from .sso import (LOGIN_LIFETIME, SSOError, extract_token, handler_command, launch_url, new_passport,
                  oauth_issuer_from_providers)

log = logging.getLogger(__name__)

# Files written by version 0.1 that are no longer used (one could contain a login link).
LEGACY_FILES = ("sso_callback.txt", "sso_callback.tmp", "sso_pending.json")


@dataclass
class LoginAttempt:
    site: str
    url: str
    passport: str  # kept only in memory, valid for this attempt only
    expires: float
    issuer: int | None = None
    channel: LoginChannel | None = None
    handler_registered: bool = False
    handler_error: str = ""

    @property
    def expired(self) -> bool:
        return time.time() > self.expires


class Session:
    def __init__(self, directory: Path | None = None, registrar: ProtocolRegistrar | None = None) -> None:
        self.dir = Path(directory) if directory else app_dir()
        self.store = TokenStore(self.dir)
        self.registrar = registrar or ProtocolRegistrar(self.dir)
        self.attempt: LoginAttempt | None = None

    # ------------------------------------------------------------------ saved login
    def saved_credentials(self) -> Credentials | None:
        creds = self.store.load()
        if creds and normalize_site_url(creds.site) == allowed_site():
            return creds
        return None

    @staticmethod
    def client_for(creds: Credentials) -> MoodleClient:
        return MoodleClient(creds.site, creds.token)

    def logout(self) -> None:
        self.store.clear()

    def cleanup_leftovers(self) -> list:
        """Called when the program starts: no login can be in progress, so remove anything left over
        by an interrupted login (temporary link handler, files of version 0.1)."""
        done = []
        try:
            result = self.registrar.cleanup_leftover()
            if result in ("restored", "removed"):
                done.append("gestore temporaneo dei link moodlemobile:// rimosso (era rimasto da un accesso "
                            "interrotto)")
        except OSError as exc:
            log.warning("Pulizia del gestore moodlemobile:// non riuscita: %s", exc)
        for name in LEGACY_FILES:
            try:
                (self.dir / name).unlink()
                done.append(f"file temporaneo della versione 0.1 eliminato ({name})")
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("Impossibile eliminare %s: %s", name, exc)
        return done

    # ------------------------------------------------------------------ browser login
    def start_login(self, method: str = "ffhs-id", open_browser: bool = True, use_handler: bool = True,
                    on_link: Callable[[str], bool] | None = None) -> LoginAttempt:
        self.end_login()
        site = allowed_site()
        probe = MoodleClient(site)
        try:
            cfg = probe.public_config()
        except MoodleError as exc:
            raise SSOError(f"Impossibile contattare {site}: {exc}") from None
        if not cfg.get("enablewebservices") or not cfg.get("enablemobilewebservice"):
            raise SSOError("Su Moodle l'accesso tramite app (servizio mobile) è disattivato: "
                           "il login dal programma non è possibile.")
        wwwroot = normalize_site_url(cfg.get("wwwroot") or site)
        if wwwroot != site:
            raise SSOError(f"Moodle ha risposto con un indirizzo inatteso ({wwwroot}): accesso annullato "
                           "per sicurezza.")
        endpoint = cfg.get("launchurl") or ""
        if endpoint and not (probe.is_site_url(endpoint) and endpoint.split("?")[0].endswith(
                "/admin/tool/mobile/launch.php")):
            endpoint = ""  # only Moodle's own launch page on the same site is acceptable
        issuer = oauth_issuer_from_providers(cfg.get("identityproviders") or []) if method == "ffhs-id" else None
        passport = new_passport()
        attempt = LoginAttempt(site=site, url=launch_url(site, passport, issuer, endpoint), passport=passport,
                               expires=time.time() + LOGIN_LIFETIME, issuer=issuer)
        if use_handler and on_link is not None:
            channel = LoginChannel(self.dir, lifetime=LOGIN_LIFETIME)
            try:
                channel.start(on_link)
                attempt.channel = channel
                if self.registrar.supported:
                    self.registrar.register(handler_command(channel.spec.encode()), channel.spec.id,
                                            channel.spec.expires)
                    attempt.handler_registered = True
            except OSError as exc:  # not fatal: the user can still paste the link
                log.warning("Apertura automatica dal browser non disponibile: %s", exc)
                attempt.handler_error = str(exc)
                channel.stop()
                attempt.channel = None
        self.attempt = attempt
        if open_browser:
            webbrowser.open(attempt.url)
        return attempt

    def reopen_browser(self) -> None:
        if self.attempt and not self.attempt.expired:
            webbrowser.open(self.attempt.url)

    def finish_login(self, text: str, require_signature: bool = True) -> tuple:
        """Verify the link (signature = this attempt's passport) or a pasted key; then store the token.

        ``require_signature``: links arriving from the browser must be signed; a bare 32-character key is
        accepted only when the user pastes it explicitly.
        """
        attempt = self.attempt
        if attempt is not None and attempt.expired:
            self.end_login()
            raise SSOError("Il tempo per completare l'accesso (15 minuti) è scaduto. Clicca di nuovo "
                           "«Accedi con FFHS-ID».")
        site = allowed_site()
        result = extract_token(text, site, [attempt.passport] if attempt else [])
        if result.signature_ok is None:
            if require_signature:
                raise SSOError("Link di accesso non valido.")
        elif not result.signature_ok:
            if attempt is None:
                raise SSOError("Prima clicca «Accedi con FFHS-ID», completa il login nel browser e poi incolla "
                               "il link della pagina finale.")
            raise SSOError("Questo link non appartiene all'accesso avviato adesso (firma non valida). "
                           "Usa il link della pagina aperta da questo accesso o ripeti il login.")
        client = MoodleClient(site, result.token)
        try:
            info = client.site_info()
        except TokenInvalidError:
            raise SSOError("Moodle non ha accettato il token. Ripeti il login.") from None
        except MoodleError as exc:
            raise SSOError(f"Verifica del token non riuscita: {exc}") from None
        if info.get("siteurl") and normalize_site_url(info["siteurl"]) != site:
            raise SSOError("Il token appartiene a un altro sito Moodle: rifiutato.")
        creds = Credentials(site=site, token=result.token, userid=int(info.get("userid") or 0),
                            fullname=info.get("fullname", ""), username=info.get("username", ""),
                            sitename=info.get("sitename", ""), release=info.get("release", ""))
        try:
            creds.protection = self.store.save(creds)
        except OSError as exc:  # e.g. DPAPI unavailable: keep the token for this session only
            log.warning("Token non salvato: %s", exc)
            creds.protection = "memory"
        self.end_login()
        return creds, client

    def end_login(self) -> None:
        """Close the login channel and remove the temporary moodlemobile:// handler (restoring any previous
        one). Safe to call several times."""
        attempt, self.attempt = self.attempt, None
        if attempt is None:
            return
        if attempt.channel is not None:
            attempt.channel.stop()
        if attempt.handler_registered:
            try:
                self.registrar.restore(only_channel=attempt.channel.spec.id if attempt.channel else None)
            except OSError as exc:
                log.warning("Ripristino del gestore moodlemobile:// non riuscito: %s", exc)

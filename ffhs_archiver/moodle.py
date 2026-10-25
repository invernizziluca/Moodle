"""Moodle Web Service client (the same official mobile API the Moodle app uses).

Standard library only. Safety rules enforced here, in one place:
- the only site is https://moodle.ffhs.ch (a local http test site can be enabled for development only);
- every request is checked to go to that site before it is sent, so the token never leaves it;
- only a fixed list of *read-only* web service functions can be called: the program cannot post,
  submit, start a quiz attempt or change anything in Moodle, even by mistake;
- tokens and similar secrets are removed from every log line.
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable

from . import USER_AGENT, fsutil

log = logging.getLogger(__name__)


class MoodleError(Exception):
    def __init__(self, message: str, errorcode: str = "", function: str = "") -> None:
        super().__init__(message)
        self.errorcode = errorcode
        self.function = function


class TokenInvalidError(MoodleError):
    """The stored token is no longer accepted: the user must log in again."""


class NetworkError(MoodleError):
    pass


class CancelledError(Exception):
    pass


FFHS_SITE = "https://moodle.ffhs.ch"
DEV_SITE_ENV = "FFHS_ARCHIVER_DEV_SITE"  # development/tests only; accepted for localhost addresses only

# Web service functions this program may call. All of them only read data.
READ_ONLY_FUNCTIONS = frozenset({
    "core_webservice_get_site_info", "core_enrol_get_users_courses", "core_course_get_contents",
    "core_course_get_courses_by_field",
    "mod_page_get_pages_by_courses", "mod_resource_get_resources_by_courses", "mod_label_get_labels_by_courses",
    "mod_url_get_urls_by_courses", "mod_folder_get_folders_by_courses", "mod_book_get_books_by_courses",
    "mod_assign_get_assignments", "mod_assign_get_submission_status",
    "mod_quiz_get_quizzes_by_courses", "mod_quiz_get_user_attempts", "mod_quiz_get_attempt_review",
    "mod_forum_get_forums_by_courses", "mod_forum_get_forum_discussions", "mod_forum_get_discussion_posts",
    "mod_glossary_get_glossaries_by_courses", "mod_glossary_get_entries_by_letter",
    "mod_lesson_get_lessons_by_courses", "mod_lti_get_ltis_by_courses",
    "mod_h5pactivity_get_h5pactivities_by_courses", "mod_scorm_get_scorms_by_courses",
    "mod_choice_get_choices_by_courses", "mod_choice_get_choice_options",
    "mod_feedback_get_feedbacks_by_courses", "mod_survey_get_surveys_by_courses",
    "mod_wiki_get_wikis_by_courses", "mod_wiki_get_subwikis", "mod_wiki_get_subwiki_pages",
    "mod_data_get_databases_by_courses", "mod_workshop_get_workshops_by_courses",
    "mod_imscp_get_imscps_by_courses", "mod_bigbluebuttonbn_get_bigbluebuttonbns_by_courses",
    "mod_chat_get_chats_by_courses",
})


def allowed_site() -> str:
    dev = os.environ.get(DEV_SITE_ENV, "").strip()
    if dev:
        url = normalize_site_url(dev)
        if urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1"):
            return url
    return FFHS_SITE


def check_site(url: str) -> str:
    """The normalised site URL if it is the allowed one, else MoodleError."""
    url = normalize_site_url(url)
    if url != allowed_site():
        raise MoodleError(f"Indirizzo non consentito: {url}. Il programma funziona solo con {FFHS_SITE}.",
                          "sitenotallowed")
    return url


def normalize_site_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    for suffix in ("/login/index.php", "/my", "/index.php"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def encode_params(params: dict, prefix: str = "") -> list:
    """Flatten nested params into Moodle's REST form encoding (a[0][name]=x)."""
    out = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.extend(encode_params(value, name))
        elif isinstance(value, (list, tuple)):
            out.extend(encode_params({i: v for i, v in enumerate(value)}, name))
        elif isinstance(value, bool):
            out.append((name, "1" if value else "0"))
        elif value is None:
            continue
        else:
            out.append((name, str(value)))
    return out


_SECRET_PATTERNS = (
    (re.compile(r"((?:ws|private)?token=)[^&\s\"'<>)\]]+", re.I), r"\1***"),
    (re.compile(r"(passport=)[^&\s\"'<>)\]]+", re.I), r"\1***"),
    (re.compile(r"(--sso-channel[\s=]+)\S+"), r"\1***"),
    (re.compile(r"(tokenpluginfile\.php/)[0-9a-f]{32}", re.I), r"\1***"),
    (re.compile(r"(sesskey=)[^&\s\"'<>]+", re.I), r"\1***"),
)


def redact(text: str, token: str = "") -> str:
    text = str(text)
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    if token:
        text = text.replace(token, "***")
    return text


class RedactingFilter(logging.Filter):
    """Removes secrets from the complete log line (message, arguments and traceback)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info:
            import traceback

            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


@dataclass
class DownloadResult:
    status: str  # "downloaded" | "not_modified"
    size: int = 0
    last_modified: str = ""
    content_type: str = ""


RETRY_HTTP = {429, 500, 502, 503, 504}


class MoodleClient:
    def __init__(self, site_url: str, token: str = "", *, timeout: float = 60.0,
                 min_interval: float = 0.05, retries: int = 3,
                 cancel_event: threading.Event | None = None) -> None:
        self.site_url = check_site(site_url)
        self._site = urllib.parse.urlsplit(self.site_url)
        self.token = token
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self.cancel_event = cancel_event or threading.Event()
        self.functions: set = set()
        self.site_info_data: dict = {}
        self._last_call = 0.0
        self._lock = threading.Lock()
        ctx = ssl.create_default_context()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler(),
                                                   urllib.request.HTTPSHandler(context=ctx))
        self._opener.addheaders = [("User-Agent", USER_AGENT)]

    # ------------------------------------------------------------------ basics
    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError()

    _check_cancel = check_cancel

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _sleep(self, seconds: float) -> None:
        if self.cancel_event.wait(seconds):
            raise CancelledError()

    def _open(self, request: urllib.request.Request, timeout: float | None = None):
        """urlopen with retries for transient network/server errors."""
        if not self.is_site_url(request.full_url):  # the token must never be sent to another host
            raise MoodleError("Richiesta bloccata: destinazione diversa da Moodle FFHS", "blockedhost")
        delays = [2, 5, 12][: self.retries]
        attempt = 0
        while True:
            self._check_cancel()
            self._throttle()
            try:
                return self._opener.open(request, timeout=timeout or self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_HTTP and attempt < len(delays):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else delays[attempt]
                    log.warning("HTTP %s, nuovo tentativo tra %ss", exc.code, delay)
                    attempt += 1
                    self._sleep(delay)
                    continue
                raise
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                if attempt < len(delays):
                    log.warning("Problema di rete (%s), nuovo tentativo tra %ss", redact(exc, self.token),
                                delays[attempt])
                    self._sleep(delays[attempt])
                    attempt += 1
                    continue
                reason = getattr(exc, "reason", exc)
                raise NetworkError(f"Connessione a {self._site.netloc} non riuscita: {reason}") from None

    # ------------------------------------------------------------------ web service calls
    def call(self, function: str, **params: Any) -> Any:
        if function not in READ_ONLY_FUNCTIONS:
            raise MoodleError(f"Funzione {function} non consentita: il programma usa solo funzioni di lettura",
                              "notreadonly", function)
        if not self.token:
            raise TokenInvalidError("Non connesso", "invalidtoken", function)
        data = [("wstoken", self.token), ("wsfunction", function), ("moodlewsrestformat", "json"),
                ("moodlewssettingfilter", "true"), ("moodlewssettingfileurl", "true")]
        data += encode_params(params)
        body = urllib.parse.urlencode(data).encode("utf-8")
        url = f"{self.site_url}/webservice/rest/server.php?moodlewsrestformat=json&wsfunction={function}"
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8", "Accept": "application/json"})
        started = time.monotonic()
        try:
            with self._open(req) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise MoodleError(f"HTTP {exc.code} da {function}", f"http{exc.code}", function) from None
        log.debug("WS %s %.2fs", function, time.monotonic() - started)
        try:
            result = json.loads(raw.decode("utf-8"))
        except ValueError:
            snippet = raw[:200].decode("utf-8", "replace")
            raise MoodleError(f"Risposta non valida da {function}: {redact(snippet, self.token)}",
                              "invalidresponse", function) from None
        if isinstance(result, dict) and "exception" in result and "errorcode" in result:
            self._raise_ws_error(result, function)
        return result

    def _raise_ws_error(self, result: dict, function: str) -> None:
        code = result.get("errorcode", "")
        message = redact(result.get("message", code), self.token)
        lowered = message.lower()
        if code == "invalidtoken" or (code == "accessexception" and "token" in lowered and
                                      ("expired" in lowered or "invalid" in lowered)):
            raise TokenInvalidError("La sessione Moodle è scaduta: effettua di nuovo l'accesso.", code, function)
        raise MoodleError(message, code, function)

    def try_call(self, function: str, default: Any = None, **params: Any) -> tuple:
        """Call that turns 'not allowed / not available' errors into (default, error)."""
        if self.functions and function not in self.functions:
            return default, MoodleError(f"Funzione {function} non disponibile su questo Moodle",
                                        "functionnotavailable", function)
        try:
            return self.call(function, **params), None
        except TokenInvalidError:
            raise
        except CancelledError:
            raise
        except NetworkError:
            raise
        except MoodleError as exc:
            return default, exc

    def site_info(self) -> dict:
        info = self.call("core_webservice_get_site_info")
        self.site_info_data = info
        self.functions = {f.get("name") for f in info.get("functions", []) if f.get("name")}
        return info

    def has_function(self, name: str) -> bool:
        return not self.functions or name in self.functions

    def public_config(self) -> dict:
        """Public site configuration (no login needed): login type, identity providers, wwwroot."""
        args = json.dumps([{"index": 0, "methodname": "tool_mobile_get_public_config", "args": {}}])
        url = (f"{self.site_url}/lib/ajax/service-nologin.php?info=tool_mobile_get_public_config&args="
               + urllib.parse.quote(args))
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self._open(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MoodleError(f"HTTP {exc.code} leggendo la configurazione pubblica", f"http{exc.code}") from None
        except ValueError:
            raise MoodleError("Il sito non sembra un Moodle (configurazione pubblica non leggibile)",
                              "notmoodle") from None
        if not isinstance(data, list) or not data or data[0].get("error"):
            exc = (data[0].get("exception") if isinstance(data, list) and data else None) or {}
            raise MoodleError(exc.get("message", "Configurazione pubblica non disponibile"),
                              exc.get("errorcode", "publicconfig"))
        return data[0]["data"]

    # ------------------------------------------------------------------ files
    def is_site_url(self, url: str) -> bool:
        try:
            p = urllib.parse.urlsplit(url)
        except ValueError:
            return False
        if p.scheme not in ("http", "https") or p.netloc.lower() != self._site.netloc:
            return False
        return p.path.startswith(self._site.path + "/") or p.path == self._site.path or not self._site.path

    def _relpath(self, url: str) -> str:
        path = urllib.parse.urlsplit(url).path
        base = self._site.path
        return path[len(base):] if base and path.startswith(base) else path

    def is_file_url(self, url: str) -> bool:
        if not self.is_site_url(url):
            return False
        rel = self._relpath(url)
        return rel.startswith(("/pluginfile.php/", "/webservice/pluginfile.php/", "/tokenpluginfile.php/"))

    def file_key(self, url: str) -> str:
        """Stable identity of a Moodle file: '<contextid>/<component>/<area>/<itemid>/<path>'."""
        rel = urllib.parse.unquote(self._relpath(url))
        for prefix in ("/webservice/pluginfile.php/", "/pluginfile.php/"):
            if rel.startswith(prefix):
                return rel[len(prefix):]
        if rel.startswith("/tokenpluginfile.php/"):
            return rel.split("/", 3)[3] if rel.count("/") >= 3 else rel
        return rel

    def tokenized_file_url(self, url: str) -> str:
        """webservice/pluginfile.php URL with the token, for a file on this Moodle site only."""
        if not self.is_file_url(url):
            raise MoodleError("URL di file non appartenente al sito Moodle", "notsitefile")
        p = urllib.parse.urlsplit(url)
        rel = self._relpath(url)
        if rel.startswith("/pluginfile.php/"):
            rel = "/webservice/pluginfile.php/" + rel[len("/pluginfile.php/"):]
        elif rel.startswith("/tokenpluginfile.php/"):
            parts = rel.split("/", 3)
            rel = "/webservice/pluginfile.php/" + (parts[3] if len(parts) > 3 else "")
        query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k.lower() != "token"]
        query.append(("token", self.token))
        return urllib.parse.urlunsplit((self._site.scheme, self._site.netloc, self._site.path + rel,
                                        urllib.parse.urlencode(query), ""))

    def fetch_text(self, url: str) -> str:
        """Small text file (e.g. a book chapter) from the site, with the token."""
        req = urllib.request.Request(self.tokenized_file_url(url))
        try:
            with self._open(req) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise MoodleError(f"HTTP {exc.code}", f"http{exc.code}") from None
        self._check_error_payload(raw[:4096], ctype)
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        return raw.decode(charset, "replace")

    def _check_error_payload(self, head: bytes, ctype: str, expect_json: bool = False) -> None:
        if "json" in ctype.lower() and not expect_json:
            try:
                data = json.loads(head.decode("utf-8"))
            except ValueError:
                data = None
            if isinstance(data, dict) and "errorcode" in data:
                code = data.get("errorcode", "")
                msg = redact(data.get("error") or data.get("message") or code, self.token)
                if code == "invalidtoken":
                    raise TokenInvalidError("La sessione Moodle è scaduta: effettua di nuovo l'accesso.", code)
                raise MoodleError(msg, code)
        if ctype.lower().startswith("text/html") and b"login/index.php" in head and b"<form" in head:
            raise MoodleError("Moodle ha risposto con la pagina di login (file non accessibile)", "requirelogin")

    def download(self, url: str, dest: Path, *, expected_size: int | None = None,
                 if_modified_since: str = "", progress: Callable[[int, int | None], None] | None = None
                 ) -> DownloadResult:
        """Stream a Moodle file to ``dest`` (via ``dest.part``; resumes with HTTP Range when possible)."""
        dest = Path(dest)
        part = dest.with_name(dest.name + ".part")
        fsutil.ensure_dir(dest.parent)
        have = fsutil.getsize(part) if fsutil.exists(part) else 0
        headers = {}
        if have and (expected_size is None or have < expected_size):
            headers["Range"] = f"bytes={have}-"
        elif have:
            fsutil.remove(part)
            have = 0
        if if_modified_since and not have:
            headers["If-Modified-Since"] = if_modified_since
        req = urllib.request.Request(self.tokenized_file_url(url), headers=headers)
        try:
            resp = self._open(req, timeout=max(self.timeout, 120))
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                exc.close()
                return DownloadResult("not_modified", last_modified=if_modified_since)
            if exc.code == 416 and have:
                fsutil.remove(part)
                return self.download(url, dest, expected_size=expected_size, progress=progress)
            body = b""
            try:
                body = exc.read(4096)
            except Exception:  # pragma: no cover
                pass
            if body:
                self._check_error_payload(body, exc.headers.get("Content-Type", "") if exc.headers else "")
            raise MoodleError(f"HTTP {exc.code}", f"http{exc.code}") from None
        with resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            last_modified = resp.headers.get("Last-Modified", "")
            total = resp.headers.get("Content-Length")
            total_size = int(total) if total and total.isdigit() else None
            if status == 206:
                mode = "ab"
                if total_size is not None:
                    total_size += have
            else:
                mode = "wb"
                have = 0
            first = resp.read(64 * 1024)
            if have == 0:
                self._check_error_payload(first[:4096], ctype, expect_json=dest.suffix.lower() == ".json")
            written = have
            with open(fsutil.lp(part), mode) as fh:
                chunk = first
                while chunk:
                    fh.write(chunk)
                    written += len(chunk)
                    if progress:
                        progress(written, total_size or expected_size)
                    if self.cancel_event.is_set():
                        raise CancelledError()
                    try:
                        chunk = resp.read(256 * 1024)
                    except (ConnectionError, TimeoutError) as exc:
                        raise NetworkError(f"Download interrotto: {exc}") from None
        if total_size is not None and written < total_size:
            raise NetworkError(f"Download incompleto ({written}/{total_size} byte)")
        if fsutil.exists(dest):
            fsutil.remove(dest)
        fsutil.move(part, dest)
        return DownloadResult("downloaded", size=written, last_modified=last_modified, content_type=ctype)


def http_date(ts: float) -> str:
    return email.utils.formatdate(ts, usegmt=True)

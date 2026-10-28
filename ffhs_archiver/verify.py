"""«Verifica accesso» and «Anteprima del corso»: read-only checks that download no course file and do not
touch the archive. They are the first test on the real FFHS Moodle before any export."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .exporter import LINK_ONLY_NOTES, MOD_LABELS, VIDEO_MODS, CourseExport, ExportOptions, Exporter, fmt_d, fmt_dt
from .html2md import html_to_text, unescape_name
from .kaltura import KalturaClient, parse_kaltura
from .moodle import (AUTOLOGIN_FUNCTION, FFHS_SITE, READ_ONLY_FUNCTIONS, MoodleClient, MoodleError, VideoSession,
                     allowed_site)
from .naming import human_size

_KALTURA_LAUNCH_RE = re.compile(r'https?:\\?/\\?/[^"\'\s]+?/filter/kaltura/lti_launch\.php\?[^"\'\s]+')

OK, WARN, FAIL = "[OK]", "[!!]", "[NO]"  # plain text: readable in any font
REQUIRED_FUNCTIONS = ("core_webservice_get_site_info", "core_enrol_get_users_courses", "core_course_get_contents")


@dataclass
class Check:
    status: str
    title: str
    detail: str = ""

    def line(self) -> str:
        return f"{self.status} {self.title}" + (f": {self.detail}" if self.detail else "")


# --------------------------------------------------------------------------- this computer

def system_checks(state_dir) -> list:
    from . import ipc, securestore
    from .protocol import ProtocolRegistrar

    checks = []
    tk_version = ""
    try:
        import tkinter

        tk_version = f", Tk {tkinter.TkVersion}"
    except Exception:
        pass
    py_ok = sys.version_info >= (3, 10)
    checks.append(Check(OK if py_ok else FAIL, "Python", f"{sys.version.split()[0]}{tk_version}"))
    try:
        checks.append(Check(OK, "Protezione del token", securestore.self_test()))
    except Exception as exc:
        checks.append(Check(FAIL, "Protezione del token", f"non funziona ({exc}): il token non verrà salvato"))
    registrar = ProtocolRegistrar(Path(state_dir))
    if registrar.supported:
        try:
            checks.append(Check(OK, "Registro di Windows", registrar.self_test()))
        except Exception as exc:
            checks.append(Check(WARN, "Registro di Windows",
                                f"prova non riuscita ({exc}): usa «incolla il link» per l'accesso"))
        status = registrar.status()
        checks.append(Check(OK if status.startswith(("assente", "appartiene")) else WARN,
                            "Gestore dei link moodlemobile://", status))
    try:
        checks.append(Check(OK, "Canale locale per il login", ipc.self_test(state_dir)))
    except Exception as exc:
        checks.append(Check(WARN, "Canale locale per il login",
                            f"prova non riuscita ({exc}): usa «incolla il link» per l'accesso"))
    leftovers = [n for n in ("sso_callback.txt", "sso_pending.json") if (Path(state_dir) / n).exists()]
    checks.append(Check(WARN if leftovers else OK, "File temporanei di login",
                        "presenti: " + ", ".join(leftovers) if leftovers else "nessuno"))
    return checks


# --------------------------------------------------------------------------- Moodle

def moodle_checks(client: MoodleClient | None, creds=None) -> tuple:
    """Returns (checks, courses). Without a client only the public part is checked."""
    checks = []
    courses: list = []
    site = allowed_site()
    started = time.monotonic()
    try:
        cfg = MoodleClient(site, timeout=30).public_config()
        ms = int((time.monotonic() - started) * 1000)
        checks.append(Check(OK, "Moodle raggiungibile", f"{site} ({ms} ms)"))
        app = bool(cfg.get("enablewebservices")) and bool(cfg.get("enablemobilewebservice"))
        checks.append(Check(OK if app else FAIL, "Servizio dell'app Moodle",
                            "attivo" if app else "disattivato: il programma non può funzionare"))
        login = {1: "nell'app", 2: "tramite browser", 3: "browser integrato"}.get(int(cfg.get("typeoflogin") or 0),
                                                                                 "sconosciuto")
        names = ", ".join(p.get("name", "") for p in cfg.get("identityproviders") or []) or "nessuno"
        mfa = " · MFA attiva" if cfg.get("tool_mfa_enabled") else ""
        checks.append(Check(OK, "Tipo di accesso", f"{login} · provider: {names}{mfa}"))
    except MoodleError as exc:
        checks.append(Check(FAIL, "Moodle raggiungibile", str(exc)))
        return checks, courses
    if client is None:
        checks.append(Check(WARN, "Accesso", "non ancora effettuato"))
        return checks, courses
    try:
        info = client.site_info()
    except MoodleError as exc:
        checks.append(Check(FAIL, "Accesso", str(exc)))
        return checks, courses
    checks.append(Check(OK, "Accesso valido", f"{info.get('fullname', '')} · {info.get('sitename', '')} · "
                                              f"Moodle {info.get('release', '?')}"))
    if creds is not None:
        protection = {"dpapi": "cifrato con DPAPI di Windows (solo il tuo utente su questo computer)",
                      "file-permissions": "file leggibile solo dal tuo utente (non Windows)",
                      "memory": "NON salvato (solo in memoria fino alla chiusura): protezione non disponibile"}.get(
            creds.protection, creds.protection or "sconosciuta")
        checks.append(Check(OK if creds.protection == "dpapi" or os.name != "nt" else WARN, "Token salvato",
                            protection))
    downloads = info.get("downloadfiles")
    checks.append(Check(OK if downloads else FAIL, "Download dei file",
                        "consentito dal servizio" if downloads else "NON consentito: nessun documento scaricabile"))
    available = {f.get("name") for f in info.get("functions", [])}
    missing_required = [f for f in REQUIRED_FUNCTIONS if f not in available]
    usable = sorted(READ_ONLY_FUNCTIONS & available)
    unavailable = sorted(READ_ONLY_FUNCTIONS - available)
    detail = f"{len(usable)} di {len(READ_ONLY_FUNCTIONS)} funzioni di lettura disponibili"
    if unavailable:
        detail += " (non disponibili: " + ", ".join(unavailable[:6]) + ("…" if len(unavailable) > 6 else "") + ")"
    checks.append(Check(FAIL if missing_required else OK, "Funzioni dell'API", detail))
    try:
        courses = Exporter(client, ExportOptions(dest=".")).list_courses()
        checks.append(Check(OK if courses else WARN, "Corsi", f"{len(courses)} trovati"))
    except MoodleError as exc:
        checks.append(Check(FAIL, "Corsi", str(exc)))
    checks.append(Check("[INFO]", "Video delle lezioni (Kaltura)",
                        "gestiti con FetchV nel browser; nessuna richiesta di auto-login eseguita. "
                        "Il download va verificato nella pagina Moodle."))
    return checks, courses


def _find_kaltura_ref(client: MoodleClient, courses: list, max_courses: int = 5):
    """Scan a few courses for one Kaltura/SWITCHcast lecture-video link (no download), for the probe."""
    for c in courses[:max_courses]:
        try:
            contents = client.call("core_course_get_contents", courseid=int(c["id"]))
        except (MoodleError, Exception):  # a probe must never break the verification
            continue
        m = _KALTURA_LAUNCH_RE.search(json.dumps(contents))
        if m:
            ref = parse_kaltura(m.group(0).replace("\\/", "/"))
            if ref and ref.launch_url:
                return ref, c
    return None, None


def _video_check(client: MoodleClient, creds, courses: list, autologin_ok: bool) -> "Check":
    """Report whether the FFHS lecture videos (Kaltura/SWITCHcast) can be downloaded. When the private
    token is present this actually *resolves* one video (without downloading it) to be sure."""
    if not autologin_ok:
        return Check(WARN, "Video delle lezioni (Kaltura)",
                     "questo Moodle non espone l'autologin dell'app: i video resteranno come link")
    has_private = bool(getattr(creds, "privatetoken", "")) if creds is not None else False
    if not has_private:
        return Check(OK, "Video delle lezioni (Kaltura)",
                     "scaricabili con l'opzione «Scarica i video e cattura le pagine» · attivala e accedi di "
                     "nuovo per usarla")
    # Private token present: do a real, lightweight resolve of one entry (no bytes downloaded).
    try:
        session = VideoSession(client, creds.privatetoken)
        if not session.ensure_session():
            return Check(WARN, "Video delle lezioni (Kaltura)",
                         f"accesso alla piattaforma video non riuscito: {session.reason}")
        ref, course = _find_kaltura_ref(client, courses)
        if ref is None:
            return Check(OK, "Video delle lezioni (Kaltura)",
                         "accesso alla piattaforma video riuscito (nessun video di prova trovato nei corsi campione)")
        outcome = KalturaClient(session).probe(ref)
        if outcome.status == "downloaded":
            return Check(OK, "Video delle lezioni (Kaltura)",
                         f"pronti al download (verificato su «{unescape_name(course.get('shortname', ''))}»)")
        return Check(WARN, "Video delle lezioni (Kaltura)",
                     f"accesso riuscito ma non ho potuto preparare un video di prova: {outcome.note}. "
                     "Procedi pure: i video non scaricabili verranno comunque segnalati con il loro link")
    except Exception as exc:  # the probe is best-effort
        return Check(WARN, "Video delle lezioni (Kaltura)", f"controllo non completato: {exc}")


def access_report(state_dir, client: MoodleClient | None, creds=None) -> str:
    sys_checks = system_checks(state_dir)
    m_checks, courses = moodle_checks(client, creds)
    lines = [f"VERIFICA ACCESSO — {fmt_dt(time.time())} — FFHS Moodle Archiver {__version__}",
             "Solo controlli e letture: nessun file dei corsi è stato scaricato e l'archivio non è stato modificato.",
             "", "Questo computer"]
    lines += [f"  {c.line()}" for c in sys_checks]
    lines += ["", f"Moodle FFHS ({FFHS_SITE if allowed_site() == FFHS_SITE else allowed_site()})"]
    lines += [f"  {c.line()}" for c in m_checks]
    if courses:
        lines += ["", "Corsi visibili per il tuo account"]
        for c in courses:
            start, end = fmt_d(c.get("startdate")), fmt_d(c.get("enddate"))
            period = f"{start} – {end}" if start and end else start
            lines.append(f"  • {c.get('fullname', '')}  [{c.get('shortname', '')}]" + (f"  {period}" if period else ""))
    failed = [c for c in sys_checks + m_checks if c.status == FAIL]
    warned = [c for c in sys_checks + m_checks if c.status == WARN]
    lines.append("")
    if failed:
        lines.append(f"Esito: {len(failed)} problemi da risolvere prima di esportare (righe [NO]).")
    elif client is None:
        lines.append("Esito: computer e connessione a Moodle a posto" + (f", con {len(warned) - 1} avvisi"
                                                                         if len(warned) > 1 else "") + ".")
        lines.append("Prossimo passo: clicca «Accedi con FFHS-ID»; dopo l'accesso la verifica completa (account e "
                     "corsi) parte da sola.")
        return "\n".join(lines) + "\n"
    elif warned:
        lines.append(f"Esito: accesso verificato, con {len(warned)} avvisi (righe [!!]).")
    else:
        lines.append("Esito: tutto a posto.")
    lines.append("Prossimo passo: evidenzia un corso (per esempio BWL8) e clicca «Anteprima del corso»: vedrai "
                 "sezioni, attività e documenti che verrebbero esportati, sempre senza scaricare nulla.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- course preview

def _n(count: int, singular: str, plural: str = "") -> str:
    return f"{count} {singular if count == 1 else (plural or singular)}"


def _excerpt(html: str, limit: int = 90) -> str:
    text = " ".join(html_to_text(html or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def course_preview(client: MoodleClient, course: dict) -> str:
    """What an export of this course would contain, from the course structure only (no files, no writes)."""
    exporter = Exporter(client, ExportOptions(dest=os.devnull))
    ce = CourseExport(exporter, course, folder=".anteprima")
    contents = client.call("core_course_get_contents", courseid=ce.cid,
                           options=[{"name": "includestealthmodules", "value": 1}])
    details, _ = client.try_call("core_course_get_courses_by_field", field="id", value=ce.cid)
    if details and details.get("courses"):
        ce.details = details["courses"][0]
    root = ce._build_tree(contents)
    stats = {"sections": 0, "subsections": 0, "activities": 0, "labels": 0, "files": 0, "bytes": 0,
             "restricted": 0}
    types: dict = {}
    not_text: list = []
    tree: list = []

    def files_of(module: dict) -> list:
        return [c for c in module.get("contents") or [] if c.get("type") == "file"
                and c.get("filename") != "index.html" and module.get("modname") not in ("page", "book")]

    def walk(node, depth: int) -> None:
        pad = "   " * depth
        for child in node.children:
            m = child.raw or {}
            if child.kind == "section":
                stats["subsections" if child.module else "sections"] += 1
                marker = "▸ " if child.module else ""
                num = "" if child.module else f"{int(m.get('section') or 0):02d} "
                restricted = "" if m.get("uservisible", True) else "  [accesso limitato]"
                tree.append(f"{pad}{marker}{num}{child.title}" + (" (sottosezione)" if child.module else "")
                            + restricted)
                summary = _excerpt(m.get("summary") or "")
                if summary:
                    tree.append(f"{pad}   «{summary}»")
                walk(child, depth + 1)
                continue
            if child.kind == "label":
                stats["labels"] += 1
                tree.append(f"{pad}   • [Testo] {_excerpt(m.get('description') or child.title, 110)}")
                continue
            stats["activities"] += 1
            label = MOD_LABELS.get(child.modname, child.modname)
            types[label] = types.get(label, 0) + 1
            parts = [f"{pad}   • [{label}] {child.title}"]
            fl = files_of(m)
            if fl:
                size = sum(int(f.get("filesize") or 0) for f in fl)
                stats["files"] += len(fl)
                stats["bytes"] += size
                parts.append(f" — {len(fl)} file ({human_size(size)})")
            if m.get("uservisible") is False:
                stats["restricted"] += 1
                parts.append(f"  [accesso limitato: {_excerpt(m.get('availabilityinfo') or '', 60)}]")
            dates = [f"{html_to_text(d.get('label') or '').rstrip(':')} {fmt_dt(d.get('timestamp'))}"
                     for d in m.get("dates") or [] if d.get("timestamp")]
            if dates:
                parts.append("  (" + "; ".join(dates) + ")")
            tree.append("".join(parts))
            desc = _excerpt(m.get("description") or "")
            if desc:
                tree.append(f"{pad}       «{desc}»")
            if child.modname in LINK_ONLY_NOTES:
                kind = "video" if child.modname in VIDEO_MODS else "interattivo"
                not_text.append(f"  • {child.title} — {label} ({kind}: verranno salvati descrizione e link)")

    walk(root, 0)
    title = unescape_name(course.get("fullname") or course.get("shortname") or str(ce.cid))
    lines = [f"ANTEPRIMA — {title} — {fmt_dt(time.time())}",
             "Solo la struttura del corso: nessun file è stato scaricato, nell'archivio non è stato scritto nulla.",
             f"Moodle: {ce.course_url}", "",
             f"Riepilogo: {_n(stats['sections'], 'sezione', 'sezioni')}, "
             f"{_n(stats['subsections'], 'sottosezione', 'sottosezioni')}, {_n(stats['activities'], 'attività')}, "
             f"{_n(stats['labels'], 'testo', 'testi')}, {_n(stats['files'], 'documento', 'documenti')} "
             f"({human_size(stats['bytes']) or '0 B'}), "
             f"{_n(stats['restricted'], 'elemento', 'elementi')} con accesso limitato",
             "Tipi di attività: " + (", ".join(f"{k} {v}" for k, v in sorted(types.items(), key=lambda kv: -kv[1]))
                                     or "nessuna"),
             "", "Struttura (come in Moodle)", *tree]
    if not_text:
        lines += ["", "Non esportabili come testo (resteranno descrizione e link a Moodle)", *not_text]
    lines += ["", "Confronta questa struttura con la pagina del corso in Moodle. Se corrisponde, esporta solo "
                  "questo corso e controlla il file «verifica_corso.md» che verrà creato nella sua cartella."]
    return "\n".join(lines) + "\n"

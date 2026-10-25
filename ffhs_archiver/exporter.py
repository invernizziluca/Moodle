"""Course export engine: Moodle structure + texts + files -> local archive (Markdown, JSON, files)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import re
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from collections.abc import Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, fsutil
from .html2md import (Converter, classify_media_url, escape_md, html_to_text, is_audio_filename,
                      is_video_filename, md_url, quiz_question_to_markdown, shift_headings, unescape_name)
from .moodle import CancelledError, MoodleClient, MoodleError, NetworkError, TokenInvalidError
from .naming import UniqueNames, human_size, rel_link, safe_filename, slug
from .privacy import Pseudonymizer, scrub_secrets
from .state import STATE_DIR, ArchiveIndex, CourseState

log = logging.getLogger(__name__)

MOD_LABELS = {
    "page": "Pagina (Textseite)",
    "resource": "File (Datei)",
    "folder": "Cartella (Verzeichnis)",
    "url": "Link (URL)",
    "book": "Libro (Buch)",
    "assign": "Compito (Aufgabe)",
    "quiz": "Quiz (Test)",
    "forum": "Forum",
    "glossary": "Glossario (Glossar)",
    "lesson": "Lezione (Lektion)",
    "h5pactivity": "Attività interattiva H5P",
    "hvp": "Contenuto interattivo H5P",
    "lti": "Strumento esterno (LTI)",
    "scorm": "Pacchetto SCORM",
    "label": "Testo (Text- und Medienfeld)",
    "subsection": "Sottosezione (Unterabschnitt)",
    "choice": "Sondaggio (Abstimmung)",
    "feedback": "Questionario (Feedback)",
    "survey": "Questionario (Umfrage)",
    "wiki": "Wiki",
    "data": "Database (Datenbank)",
    "workshop": "Valutazione tra pari (Workshop)",
    "bigbluebuttonbn": "Aula virtuale (BigBlueButton)",
    "imscp": "Pacchetto IMS",
    "chat": "Chat",
    "kalvidres": "Video Kaltura",
    "kalvidassign": "Consegna video Kaltura",
    "mumie": "Esercizi MUMIE",
    "turnitintooltwo": "Consegna Turnitin",
    "questionnaire": "Questionario",
    "attendance": "Presenze",
    "checklist": "Checklist",
    "journal": "Diario",
    "zoom": "Riunione Zoom",
    "pdfannotator": "PDF annotabile",
}

# modname -> (function, result key, course-module id field)
BULK = {
    "page": ("mod_page_get_pages_by_courses", "pages", "coursemodule"),
    "resource": ("mod_resource_get_resources_by_courses", "resources", "coursemodule"),
    "label": ("mod_label_get_labels_by_courses", "labels", "coursemodule"),
    "url": ("mod_url_get_urls_by_courses", "urls", "coursemodule"),
    "folder": ("mod_folder_get_folders_by_courses", "folders", "coursemodule"),
    "book": ("mod_book_get_books_by_courses", "books", "coursemodule"),
    "assign": ("mod_assign_get_assignments", "#assign", "cmid"),
    "quiz": ("mod_quiz_get_quizzes_by_courses", "quizzes", "coursemodule"),
    "forum": ("mod_forum_get_forums_by_courses", "#list", "cmid"),
    "glossary": ("mod_glossary_get_glossaries_by_courses", "glossaries", "coursemodule"),
    "lesson": ("mod_lesson_get_lessons_by_courses", "lessons", "coursemodule"),
    "lti": ("mod_lti_get_ltis_by_courses", "ltis", "coursemodule"),
    "h5pactivity": ("mod_h5pactivity_get_h5pactivities_by_courses", "h5pactivities", "coursemodule"),
    "scorm": ("mod_scorm_get_scorms_by_courses", "scorms", "coursemodule"),
    "choice": ("mod_choice_get_choices_by_courses", "choices", "coursemodule"),
    "feedback": ("mod_feedback_get_feedbacks_by_courses", "feedbacks", "coursemodule"),
    "survey": ("mod_survey_get_surveys_by_courses", "surveys", "coursemodule"),
    "wiki": ("mod_wiki_get_wikis_by_courses", "wikis", "coursemodule"),
    "data": ("mod_data_get_databases_by_courses", "databases", "coursemodule"),
    "workshop": ("mod_workshop_get_workshops_by_courses", "workshops", "coursemodule"),
    "imscp": ("mod_imscp_get_imscps_by_courses", "imscps", "coursemodule"),
    "bigbluebuttonbn": ("mod_bigbluebuttonbn_get_bigbluebuttonbns_by_courses", "bigbluebuttonbns", "coursemodule"),
    "chat": ("mod_chat_get_chats_by_courses", "chats", "coursemodule"),
}

LINK_ONLY_NOTES = {
    "lesson": "Lezione interattiva: le pagine si vedono solo avviando la lezione in Moodle.",
    "h5pactivity": "Contenuto interattivo H5P: si usa solo in Moodle (non esportabile come testo).",
    "hvp": "Contenuto interattivo H5P: si usa solo in Moodle (non esportabile come testo).",
    "lti": "Strumento esterno (LTI): il contenuto è su un altro sistema e si apre solo da Moodle.",
    "scorm": "Pacchetto SCORM interattivo: si apre solo in Moodle.",
    "bigbluebuttonbn": "Aula virtuale: le sessioni e le eventuali registrazioni si aprono in Moodle.",
    "data": "Database: le voci non vengono esportate in questa versione.",
    "workshop": "Workshop: solo la descrizione viene esportata.",
    "feedback": "Questionario: le domande si vedono solo compilandolo in Moodle.",
    "survey": "Questionario: le domande si vedono solo compilandolo in Moodle.",
    "chat": "Chat: le sessioni non vengono esportate.",
    "kalvidres": "Player Kaltura: questa versione conserva il link; verifica nel player "
                 "se è disponibile un download ufficiale.",
    "kalvidassign": "Consegna video Kaltura: si gestisce in Moodle.",
    "mumie": "Esercizi MUMIE: si aprono da Moodle sulla piattaforma MUMIE.",
    "turnitintooltwo": "Consegna Turnitin: istruzioni esportate, consegna e rapporti restano in Moodle.",
    "questionnaire": "Questionario: le domande si vedono solo compilandolo in Moodle.",
    "attendance": "Registro presenze: non esportato.",
    "checklist": "Checklist interattiva: si usa in Moodle.",
    "journal": "Diario: le voci non vengono esportate in questa versione.",
    "zoom": "Riunione Zoom: link e orari in Moodle; le registrazioni non vengono scaricate.",
}

MANUAL_TIP = ("Per conservarne il contenuto: aprilo in Moodle e salva la pagina come PDF (Ctrl+P → «Salva come "
              "PDF») nella cartella di questa attività. Alla prossima esportazione il file verrà collegato qui, "
              "nella panoramica e nel testo completo.")
VIDEO_TIP = ("Questa versione scarica i file video accessibili sul sito Moodle, ma non i player esterni "
             "o gli streaming HLS/DASH. Apri il link e verifica se il servizio offre un download ufficiale; "
             "puoi salvare il file o i tuoi appunti nella cartella dell'attività. Le aggiunte manuali "
             "verranno collegate automaticamente, senza essere considerate una verifica di completezza.")
VIDEO_MODS = {"kalvidres", "kalvidassign", "bigbluebuttonbn", "zoom"}
MISSING_GROUPS = (
    ("interattivo", "Contenuti interattivi (H5P, lezioni, SCORM, strumenti esterni, questionari)",
     "Si usano solo dentro Moodle. " + MANUAL_TIP.replace("di questa attività", "dell'attività")),
    ("video", "Video/audio rimasti online e aule virtuali", VIDEO_TIP.replace("di questa attività", "dell'attività")),
    ("quiz", "Quiz senza revisione disponibile",
     "Il programma non avvia tentativi e non recupera risposte nascoste: se svolgi il quiz in Moodle e il docente "
     "permette la revisione, la prossima esportazione la salverà."),
    ("limitato", "Elementi con accesso limitato o nascosti",
     "Diventeranno esportabili quando Moodle li renderà visibili al tuo account (date, condizioni)."),
    ("errore", "Download non riusciti", "Riprova l'esportazione; se l'errore resta, apri il link in Moodle."),
    ("impostazioni", "Esclusi dalle impostazioni", "Attiva l'opzione corrispondente e riesporta."),
    ("collegamento", "Contenuti conservati solo come link",
     "Apri il link originale: il contenuto della destinazione non è stato verificato né copiato offline."),
    ("altro", "Altri elementi", "Aprili in Moodle tramite il link."),
)

MEDIA_DIR = "_media"
VERSIONS_DIR = "_versioni_precedenti"
PH_RE = re.compile(r"@@(FILE|ITEM|PATH):([^@\s)]+)@@")


def fmt_dt(ts: Any) -> str:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def fmt_d(ts: Any) -> str:
    return fmt_dt(ts)[:10]


def yaml_str(value: Any) -> str:
    return json.dumps("" if value is None else value, ensure_ascii=False)


def first_line(md: str, limit: int = 140) -> str:
    """First meaningful line of a Markdown snippet as plain text (for one-line summaries)."""
    for line in (md or "").split("\n"):
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"^[#>\-\s|]+", "", line)
        line = line.replace("**", "").replace("`", "").replace("~~", "")
        line = re.sub(r"(?<![\w\\])\*(?=\S)|(?<=\S)\*(?![\w])", "", line)
        line = line.replace("\\", "")
        line = re.sub(r"\s+", " ", line).strip(" |")
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


def join_rel(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def normalize_heading_levels(md: str, top: int, exact: bool = False) -> str:
    """Shift headings so the highest one in ``md`` is at least (or, with ``exact``, exactly) level ``top``."""
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6})\s", md, re.M)]
    if not levels:
        return md
    delta = top - min(levels)
    if delta >= 0:
        return shift_headings(md, delta)
    if not exact:
        return md
    out, fence = [], False
    for line in md.split("\n"):
        if line.strip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence:
            m = re.match(r"^(#{1,6})(\s.*)$", line)
            if m:
                line = "#" * max(1, len(m.group(1)) + delta) + m.group(2)
        out.append(line)
    return "\n".join(out)


@dataclass
class ExportOptions:
    dest: str
    include_forums: bool = True
    include_quiz_reviews: bool = True
    include_submissions: bool = True
    include_videos: bool = True
    max_file_mb: int = 0
    keep_versions: bool = True
    save_raw_json: bool = False
    anonymize_others: bool = True
    max_forum_discussions: int = 500


@dataclass
class FileSpec:
    key: str
    url: str
    rel_path: str
    size: int | None = None
    mtime: int | None = None
    mimetype: str = ""
    kind: str = "material"  # material | media | video | attachment | submission | feedback | linked
    label: str = ""
    owner: str = ""
    conditional: bool = False
    index: int = -1
    status: str = "pending"  # pending | new | updated | unchanged | skipped | error
    message: str = ""

    @property
    def local_ok(self) -> bool:
        return self.status in ("new", "updated", "unchanged")


@dataclass
class Item:
    kind: str  # course | section | activity | label
    key: str
    title: str
    modname: str = ""
    url: str = ""
    raw: dict = field(default_factory=dict)
    module: dict | None = None  # subsection module record for delegated sections
    parent: Item | None = None
    children: list = field(default_factory=list)
    rel_dir: str = ""
    md_path: str = ""
    number: int = 0
    description: str = ""
    body: str = ""
    facts: list = field(default_factory=list)
    links: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    restricted: str = ""
    inst: dict | None = None
    manual_files: list = field(default_factory=list)

    @property
    def type_label(self) -> str:
        if self.kind == "section":
            return "Sottosezione (Unterabschnitt)" if self.module else "Sezione (Abschnitt)"
        return MOD_LABELS.get(self.modname, self.raw.get("modplural") or self.modname or "Attività")

    def breadcrumb(self) -> list:
        parts = []
        node = self.parent
        while node is not None and node.kind != "course":
            parts.append(node.title)
            node = node.parent
        return list(reversed(parts))

    def depth(self) -> int:
        d = 0
        node = self.parent
        while node is not None and node.kind != "course":
            d += 1
            node = node.parent
        return d


class Exporter:
    """Exports a list of courses into the archive folder, reporting progress through ``emit``."""

    def __init__(self, client: MoodleClient, options: ExportOptions,
                 emit: Callable[[dict], None] | None = None,
                 cancel_event: threading.Event | None = None) -> None:
        self.client = client
        self.options = options
        self._emit = emit or (lambda event: None)
        self.cancel_event = cancel_event or client.cancel_event
        client.cancel_event = self.cancel_event

    def emit(self, type_: str, **data: Any) -> None:
        data["type"] = type_
        try:
            self._emit(data)
        except Exception:  # never let the UI break the export
            log.exception("emit failed")

    def log(self, message: str, level: str = "info") -> None:
        getattr(log, "warning" if level == "warning" else "error" if level == "error" else "info")(message)
        self.emit("log", level=level, message=message)

    # ------------------------------------------------------------------ courses
    def list_courses(self) -> list:
        if not self.client.site_info_data:
            self.client.site_info()
        uid = self.client.site_info_data.get("userid")
        courses = self.client.call("core_enrol_get_users_courses", userid=uid, returnusercount=0)
        for c in courses:
            c["fullname"] = unescape_name(c.get("fullname") or c.get("displayname") or c.get("shortname"))
            c["shortname"] = unescape_name(c.get("shortname"))
        courses.sort(key=lambda c: (-(c.get("startdate") or 0), c.get("fullname", "").lower()))
        return courses

    def _folder_for(self, course: dict, index: ArchiveIndex) -> str:
        cid = int(course["id"])
        existing = index.folder_for(cid)
        if existing:
            return existing
        base = slug(course.get("fullname") or course.get("shortname") or f"Kurs_{cid}", 40)
        used = index.used_folders()
        folder = base if base.lower() not in used else f"{base}_{cid}"
        return folder

    def export(self, courses: list) -> dict:
        dest = Path(self.options.dest)
        fsutil.ensure_dir(dest)
        index = ArchiveIndex(dest)
        started = time.time()
        results = []
        stop_reason = ""
        auth_error = False
        total = len(courses)
        for i, course in enumerate(courses, 1):
            cid = int(course["id"])
            name = unescape_name(course.get("fullname") or course.get("shortname"))
            folder = self._folder_for(course, index)
            index.update(cid, folder=folder, fullname=name, shortname=unescape_name(course.get("shortname")))
            self.emit("course_start", index=i, total=total, name=name, course_id=cid)
            self.log(f"Corso {i}/{total}: {name}")
            result = {"id": cid, "name": name, "folder": folder, "status": "error"}
            try:
                summary = CourseExport(self, course, folder).run()
                result.update(summary)
                result["status"] = "ok"
                index.update(cid, last_export=time.time(), status="ok", counts=summary.get("counts", {}),
                             missing=summary.get("missing", 0), startdate=course.get("startdate"),
                             enddate=course.get("enddate"))
                self.log(f"Completato: {name} — {self._summary_line(summary)}", "success")
            except TokenInvalidError as exc:
                stop_reason = str(exc)
                auth_error = True
                result["error"] = stop_reason
                self.log(stop_reason, "error")
            except CancelledError:
                stop_reason = "Interrotto dall'utente: alla prossima esportazione si riprende da qui."
                result["status"] = "cancelled"
                index.update(cid, status="interrotto")
                self.log(stop_reason, "warning")
            except NetworkError as exc:
                stop_reason = f"{exc}. Riprova più tardi: i file già scaricati restano e il download riprende."
                result["error"] = str(exc)
                index.update(cid, status="errore di rete")
                self.log(stop_reason, "error")
            except MoodleError as exc:
                result["error"] = str(exc)
                index.update(cid, status=f"errore: {exc}")
                self.log(f"Corso {name} non esportato: {exc}", "error")
            except Exception as exc:  # keep going with other courses
                log.error("Unexpected error\n%s", traceback.format_exc())
                result["error"] = f"{type(exc).__name__}: {exc}"
                index.update(cid, status=f"errore interno: {exc}")
                self.log(f"Errore inatteso nel corso {name}: {exc}", "error")
            results.append(result)
            index.save()
            self._write_index(dest, index)
            self.emit("course_done", index=i, total=total, result=result)
            if stop_reason:
                break
        report = self._write_report(dest, results, started, stop_reason)
        summary = {"results": results, "stopped": stop_reason, "report": report, "auth_error": auth_error,
                   "duration": time.time() - started}
        self.emit("done", summary=summary)
        return summary

    @staticmethod
    def _summary_line(summary: dict) -> str:
        c = summary.get("counts", {})
        parts = [f"{c.get('new', 0)} file nuovi", f"{c.get('updated', 0)} aggiornati",
                 f"{c.get('unchanged', 0)} invariati"]
        if c.get("error"):
            parts.append(f"{c['error']} errori")
        if summary.get("missing"):
            parts.append(f"{summary['missing']} elementi non esportabili")
        return ", ".join(parts)

    def _write_index(self, dest: Path, index: ArchiveIndex) -> None:
        rows = []
        for cid, c in sorted(index.courses.items(), key=lambda kv: (-(kv[1].get("startdate") or 0),
                                                                     kv[1].get("fullname", ""))):
            rows.append({
                "id": int(cid), "fullname": c.get("fullname", ""), "shortname": c.get("shortname", ""),
                "folder": c.get("folder", ""), "overview": f"{c.get('folder', '')}/course_overview.md",
                "fulltext": f"{c.get('folder', '')}/course_fulltext.md",
                "last_export": c.get("last_export"), "status": c.get("status", ""),
                "files": sum((c.get("counts") or {}).get(k, 0) for k in ("new", "updated", "unchanged")),
                "missing_items": c.get("missing", 0),
                "startdate": c.get("startdate"), "enddate": c.get("enddate"),
            })
        fsutil.write_text_atomic(dest / "courses.json", json.dumps(
            {"generator": f"FFHS Moodle Archiver {__version__}", "site": self.client.site_url,
             "courses": rows}, ensure_ascii=False, indent=1))
        lines = ["# Archivio Moodle FFHS", "",
                 f"Sito: {self.client.site_url} · Generato da FFHS Moodle Archiver {__version__}", "",
                 "| Corso | Codice | Periodo | Ultima esportazione | File | Non esportati | Stato |",
                 "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in rows:
            period = " – ".join(x for x in (fmt_d(r["startdate"]), fmt_d(r["enddate"])) if x)
            name = escape_md(r["fullname"]).replace("|", "\\|")
            lines.append(f"| [{name}]({md_url(r['overview'])}) | {escape_md(r['shortname']).replace('|', '/')} | "
                         f"{period} | {fmt_dt(r['last_export'])} | {r['files']} | {r['missing_items']} | "
                         f"{r['status']} |")
        lines += ["", "## Come usare questo archivio", "",
                  "- Ogni corso ha `course_overview.md` (struttura, obiettivi, attività con link ai file) e "
                  "`course_fulltext.md` (tutti i testi del corso in un unico file, nell'ordine di Moodle).",
                  "- Per studiare con un assistente AI carica `course_fulltext.md` e i PDF che ti servono; "
                  "per la struttura completa usa anche `course_structure.json`.",
                  "- `elementi_mancanti.md` elenca ciò che esiste in Moodle ma non è nell'archivio, con il link.",
                  "- Le versioni sostituite dei file sono in `_versioni_precedenti/` dentro ogni corso.",
                  "- La cartella si può aprire come vault in Obsidian.", ""]
        fsutil.write_text_atomic(dest / "index.md", "\n".join(lines))

    def _write_report(self, dest: Path, results: list, started: float, stop_reason: str) -> str:
        stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(started))
        rel = f"_registro/{stamp}_esportazione.md"
        lines = [f"# Esportazione del {fmt_dt(started)}", ""]
        if stop_reason:
            lines += [f"**Interrotta:** {stop_reason}", ""]
        for r in results:
            lines.append(f"## {r['name']}")
            if r.get("status") != "ok":
                lines.append(f"- Stato: {r.get('status')} {r.get('error', '')}")
            else:
                lines.append(f"- {self._summary_line(r)}")
            for title in r.get("new_items", [])[:200]:
                lines.append(f"- Nuovo in Moodle: {title}")
            for title in r.get("changed_files", [])[:200]:
                lines.append(f"- File aggiornato: {title}")
            for title in r.get("removed_items", [])[:200]:
                lines.append(f"- Non più disponibile in Moodle (copia locale conservata): {title}")
            for err in r.get("errors", [])[:200]:
                lines.append(f"- Errore: {err}")
            lines.append("")
        fsutil.write_text_atomic(dest / rel, "\n".join(lines))
        return rel

    # ------------------------------------------------------------------ diagnostics
    def diagnose(self, course_id: int) -> dict:
        """Structure-only snapshot (no course texts) to help adapt the exporter to a Moodle site."""
        info = self.client.site_info_data or self.client.site_info()
        wanted = sorted({fn for fn, _, _ in BULK.values()} | {
            "core_course_get_contents", "core_enrol_get_users_courses", "core_course_get_courses_by_field",
            "mod_quiz_get_user_attempts", "mod_quiz_get_attempt_review", "mod_assign_get_submission_status",
            "mod_forum_get_forum_discussions", "mod_forum_get_discussion_posts",
            "mod_glossary_get_entries_by_letter", "mod_wiki_get_subwikis", "mod_wiki_get_subwiki_pages"})
        contents = self.client.call("core_course_get_contents", courseid=course_id,
                                    options=[{"name": "includestealthmodules", "value": 1}])

        def module_shape(m: dict) -> dict:
            return {
                "id": m.get("id"), "modname": m.get("modname"), "name": unescape_name(m.get("name", ""))[:80],
                "uservisible": m.get("uservisible"), "has_description": bool(m.get("description")),
                "availability": bool(m.get("availabilityinfo")),
                "customdata_keys": sorted(json.loads(m["customdata"]).keys())
                if isinstance(m.get("customdata"), str) and m["customdata"].startswith("{") else [],
                "dates": [d.get("label") for d in m.get("dates") or []],
                "contents": [{"type": c.get("type"), "ext": os.path.splitext(c.get("filename") or "")[1],
                              "size": c.get("filesize")} for c in m.get("contents") or []][:20],
                "keys": sorted(m.keys()),
            }

        return {
            "generator": f"FFHS Moodle Archiver {__version__}",
            "site": {"release": info.get("release"), "version": info.get("version"),
                     "sitename": info.get("sitename"), "lang": info.get("lang")},
            "functions": {fn: self.client.has_function(fn) for fn in wanted},
            "sections": [{"id": s.get("id"), "section": s.get("section"),
                          "name": unescape_name(s.get("name", ""))[:80], "component": s.get("component"),
                          "itemid": s.get("itemid"), "uservisible": s.get("uservisible"),
                          "summary_chars": len(s.get("summary") or ""),
                          "modules": [module_shape(m) for m in s.get("modules", [])]} for s in contents],
        }


class CourseExport:
    def __init__(self, exporter: Exporter, course: dict, folder: str) -> None:
        self.x = exporter
        self.client: MoodleClient = exporter.client
        self.opts = exporter.options
        self.course = course
        self.cid = int(course["id"])
        self.title = unescape_name(course.get("fullname") or course.get("displayname") or course.get("shortname"))
        self.shortname = unescape_name(course.get("shortname") or "")
        self.folder = folder
        self.root = Path(self.opts.dest) / folder
        self.state = CourseState.load(self.root)
        self.course_url = f"{self.client.site_url}/course/view.php?id={self.cid}"
        self.items: dict = {}
        self.alias: dict = {}
        self.specs: list = []
        self.spec_by_key: dict = {}
        self.spec_by_url: dict = {}
        self.dir_names: dict = {}
        self.bulk: dict = {}
        self.raw: dict = {}
        self.details: dict = {}
        self.counts: Counter = Counter()
        self.texts_now: dict = {}
        self.errors: list = []
        self.changed_files: list = []
        self.new_items: list = []
        self.removed_items: list = []
        self.root_item: Item | None = None
        self.pseudo = Pseudonymizer(False)
        self.summary_md = ""
        self.now = time.time()

    # ------------------------------------------------------------------ pipeline
    def run(self) -> dict:
        self.x.emit("phase", message=f"Lettura della struttura di «{self.title}»…")
        fsutil.ensure_dir(self.root)
        contents = self.client.call("core_course_get_contents", courseid=self.cid,
                                    options=[{"name": "includestealthmodules", "value": 1}])
        self.raw["core_course_get_contents"] = contents
        details, _ = self.client.try_call("core_course_get_courses_by_field", field="id", value=self.cid)
        if details and details.get("courses"):
            self.details = details["courses"][0]
            self.raw["core_course_get_courses_by_field"] = details
        me = (self.client.site_info_data or {}).get("userid")
        teachers = [c.get("id") for c in self.details.get("contacts") or []]
        self.pseudo = Pseudonymizer(self.opts.anonymize_others, [me, *teachers])
        self.root_item = self._build_tree(contents)
        self.items[self.root_item.key] = self.root_item
        self._plan_paths()
        self._collect_content_files()
        self._fetch_bulk()
        self.x.emit("phase", message="Elaborazione dei testi e delle attività…")
        activities = [it for it in self._walk() if it.kind in ("activity", "section")]
        for n, item in enumerate(activities, 1):
            self.client.check_cancel()
            self.x.emit("progress", scope="items", done=n, total=len(activities), message=item.title)
            if item.kind == "section":
                self._render_section(item)
            else:
                self._render_activity(item)
        self.summary_md = self._course_summary_md()
        self._apply_file_policies()
        self.x.emit("phase", message="Download dei file…")
        self._reconcile_moves()
        self._download_all()
        self._scan_manual_files()
        self.x.emit("phase", message="Scrittura dell'archivio…")
        self._track_items()
        self._write_all()
        self._annotate_removed()
        self._cleanup_stale_texts()
        self.state.data["runs"] = (self.state.data.get("runs", []) + [{
            "time": self.now, "counts": dict(self.counts), "version": __version__}])[-20:]
        self.state.data["course"] = {"id": self.cid, "fullname": self.title, "shortname": self.shortname}
        self.state.save()
        missing = sum(len(it.missing) for it in self.items.values())
        return {"counts": dict(self.counts), "missing": missing, "new_items": self.new_items,
                "removed_items": self.removed_items, "changed_files": self.changed_files, "errors": self.errors}

    # ------------------------------------------------------------------ tree
    def _walk(self, node: Item | None = None):
        node = node or self.root_item
        for child in node.children:
            yield child
            yield from self._walk(child)

    @staticmethod
    def _subsection_target(m: dict) -> int | None:
        try:
            data = json.loads(m.get("customdata") or "{}")
            if isinstance(data, dict) and data.get("sectionid"):
                return int(data["sectionid"])
        except (ValueError, TypeError):
            pass
        return None

    def _section_title(self, s: dict) -> str:
        name = unescape_name(s.get("name") or "")
        if name:
            return name
        return "Allgemeines" if int(s.get("section") or 0) == 0 else f"Abschnitt {s.get('section')}"

    def _build_tree(self, contents: list) -> Item:
        delegated = {int(s["id"]): s for s in contents if s.get("component")}
        used: set = set()
        root = Item(kind="course", key="course", title=self.title, url=self.course_url)

        def section_item(s: dict, parent: Item, module: dict | None = None) -> Item:
            sec = Item(kind="section", key=f"section:{s['id']}", title=self._section_title(s), raw=s,
                       module=module, parent=parent,
                       url=f"{self.client.site_url}/course/section.php?id={s['id']}")
            if module is not None:
                sec.title = unescape_name(module.get("name")) or sec.title
            self.items[sec.key] = sec
            for m in s.get("modules", []):
                if m.get("modname") == "subsection":
                    target = self._subsection_target(m)
                    if target in delegated and target not in used:
                        used.add(target)
                        child = section_item(delegated[target], sec, module=m)
                        sec.children.append(child)
                        self.alias[f"cm:{m['id']}"] = child.key
                        continue
                kind = "label" if m.get("modname") == "label" else "activity"
                title = unescape_name(m.get("name") or "")
                if kind == "label" and m.get("description"):
                    # Moodle names labels with their text squeezed together: use the spaced text instead.
                    text = html_to_text(m["description"])
                    title = text if len(text) <= 60 else text[:59].rstrip() + "…"
                item = Item(kind=kind, key=f"cm:{m['id']}", title=title,
                            modname=m.get("modname", ""), url=m.get("url") or "", raw=m, parent=sec)
                if not item.title:
                    item.title = MOD_LABELS.get(item.modname, item.modname)
                if not item.url and kind == "activity":
                    item.url = f"{self.client.site_url}/mod/{item.modname}/view.php?id={m['id']}"
                self.items[item.key] = item
                sec.children.append(item)
            return sec

        for s in contents:
            if not s.get("component"):
                root.children.append(section_item(s, root))
        for sid, s in delegated.items():
            if sid not in used:
                root.children.append(section_item(s, root))
        return root

    def _plan_paths(self) -> None:
        top = UniqueNames()
        for sec in self.root_item.children:
            num = int(sec.raw.get("section") or 0)
            title = re.sub(rf"^\s*0*{num}\s*[.:)\-–]?\s+", "", sec.title) if num else sec.title
            self._assign(sec, top.take(f"{num:02d}_{slug(title or sec.title, 40)}"))

    def _assign(self, item: Item, rel_dir: str) -> None:
        item.rel_dir = rel_dir
        base = posixpath.basename(rel_dir)
        item.md_path = f"{rel_dir}/{base}.md"
        names = self.dir_names.setdefault(rel_dir.lower(), UniqueNames())
        names.reserve(MEDIA_DIR)
        names.reserve(f"{base}.md")
        n = 0
        for child in item.children:
            if child.kind == "label":
                child.rel_dir, child.md_path = rel_dir, item.md_path
                continue
            n += 1
            child.number = n
            self._assign(child, f"{rel_dir}/{names.take(f'{n:02d}_{slug(child.title, 36)}')}")

    # ------------------------------------------------------------------ files
    @staticmethod
    def _norev(file_key: str) -> str:
        parts = file_key.split("/")
        if len(parts) >= 5 and parts[1] in ("mod_resource", "mod_page", "mod_folder", "mod_imscp") and \
                parts[2] == "content":
            parts[3] = "*"
        return "/".join(parts)

    def _unique_rel(self, rel: str) -> str:
        directory, name = posixpath.split(rel)
        names = self.dir_names.setdefault(directory.lower(), UniqueNames())
        return posixpath.join(directory, names.take(name))

    def _add_spec(self, spec: FileSpec) -> FileSpec:
        existing = self.spec_by_key.get(spec.key)
        if existing is not None:
            return existing
        spec.rel_path = self._unique_rel(spec.rel_path)
        spec.index = len(self.specs)
        self.specs.append(spec)
        self.spec_by_key[spec.key] = spec
        if spec.url and self.client.is_file_url(spec.url):
            fk = self.client.file_key(spec.url)
            self.spec_by_url.setdefault(fk, spec)
            self.spec_by_url.setdefault(self._norev(fk), spec)
        return spec

    def _media_spec(self, url: str, owner: Item, kind: str = "media", name: str = "",
                    size: int | None = None, mtime: int | None = None, subdir: str = MEDIA_DIR) -> FileSpec:
        fk = self.client.file_key(url)
        existing = self.spec_by_url.get(fk) or self.spec_by_url.get(self._norev(fk))
        if existing is not None:
            return existing
        host = owner.parent if owner.kind == "label" and owner.parent else owner
        filename = safe_filename(name or unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "datei", 80)
        if is_video_filename(filename) or is_audio_filename(filename):
            kind = "video"
        spec = FileSpec(key="media:" + self._norev(fk), url=url, rel_path=join_rel(host.rel_dir, subdir, filename),
                        size=size, mtime=mtime, kind=kind, label=filename, owner=owner.key,
                        conditional=size is None or mtime is None)
        return self._add_spec(spec)

    def _file_entry_spec(self, f: dict, owner: Item, subdir: str, kind: str, key_prefix: str) -> FileSpec | None:
        url = f.get("fileurl")
        if not url or not self.client.is_file_url(url):
            return None
        filepath = f.get("filepath") or "/"
        segs = [safe_filename(seg, 60) for seg in filepath.strip("/").split("/") if seg]
        folder = join_rel(owner.rel_dir, subdir, *segs)
        spec = FileSpec(key=f"{key_prefix}:{filepath}{f.get('filename')}", url=url,
                        rel_path=join_rel(folder, safe_filename(f.get("filename") or "datei", 80)),
                        size=f.get("filesize"), mtime=f.get("timemodified"), mimetype=f.get("mimetype") or "",
                        kind=kind, label=f.get("filename") or "", owner=owner.key)
        if is_video_filename(spec.label) or is_audio_filename(spec.label):
            spec.kind = "video" if kind == "material" else kind
        return self._add_spec(spec)

    def _collect_content_files(self) -> None:
        for item in list(self._walk()):
            if item.kind != "activity" or not item.raw.get("contents"):
                continue
            if item.modname == "book":
                continue
            for c in item.raw["contents"]:
                if c.get("type") != "file" or not c.get("fileurl"):
                    continue
                if self._stream_manifest(c["fileurl"]):
                    self._missing_stream(item, c["fileurl"])
                    continue
                if item.modname == "page":
                    if c.get("filename") != "index.html":
                        self._media_spec(c["fileurl"], item, name=c.get("filename") or "",
                                         size=c.get("filesize"), mtime=c.get("timemodified"))
                    continue
                self._file_entry_spec(c, item, "", "material", f"cm:{item.raw['id']}")

    def _apply_file_policies(self) -> None:
        limit = int(self.opts.max_file_mb or 0) * 1024 * 1024
        for spec in self.specs:
            owner = self.items.get(spec.owner)
            if spec.kind == "video" and not self.opts.include_videos:
                spec.status, spec.message = "skipped", "file video escluso dalle impostazioni"
            elif limit and spec.size and spec.size > limit:
                spec.status, spec.message = "skipped", f"file più grande di {self.opts.max_file_mb} MB"
            if spec.status == "skipped" and owner is not None:
                self._missing(owner, f"{spec.label}: {spec.message}", spec.url)
                self.counts["skipped"] += 1

    # ------------------------------------------------------------------ link/file resolution for HTML
    @staticmethod
    def _stream_manifest(url: str) -> bool:
        return unquote(urlsplit(url).path).lower().endswith((".m3u8", ".mpd"))

    def _missing_stream(self, item: Item, url: str) -> None:
        self._missing(item, "Video in streaming HLS/DASH: playlist/manifest conservato solo come link; "
                      "i segmenti video non sono stati scaricati", url)

    def _online_media(self, item: Item, url: str, title: str, provider: str) -> None:
        entry = (title or provider, url, provider)
        if entry not in item.links:
            item.links.append(entry)
        if self._stream_manifest(url):
            self._missing_stream(item, url)
        elif provider == "H5P":
            self._missing(item, "Contenuto interattivo H5P conservato solo come link", url)
        elif provider not in ("embed", "link") or is_video_filename(unquote(urlsplit(url).path)) or \
                is_audio_filename(unquote(urlsplit(url).path)):
            self._missing(item, f"Video/audio disponibile solo online ({provider}): "
                          "file non scaricato da questa versione", url)
        else:
            self._missing(item, "Contenuto collegato conservato solo come link; "
                          "disponibilità offline non verificata", url)

    def _converter(self, item: Item, base_url: str = "", heading_offset: int = 0) -> Converter:
        def on_embed(url: str, title: str, provider: str) -> None:
            self._online_media(item, url, title, provider)

        return Converter(base_url=base_url or item.url or self.course_url,
                         resolve_link=lambda url: self._link_target(url, item),
                         resolve_file=lambda url, label, kind: self._file_target(url, label, kind, item),
                         on_embed=on_embed, heading_offset=heading_offset, is_site_url=self.client.is_site_url)

    def _file_target(self, url: str, label: str, kind: str, item: Item) -> str | None:
        if not self.client.is_file_url(url):
            return None
        if self._stream_manifest(url):
            self._missing_stream(item, url)
            return None
        spec = self._media_spec(url, item, kind="video" if kind in ("video", "audio") else "media")
        return f"@@FILE:{spec.index}@@"

    def _link_target(self, url: str, item: Item) -> str | None:
        if not self.client.is_site_url(url):
            return None
        parts = urlsplit(url)
        rel = parts.path[len(self.client._site.path):] if self.client._site.path else parts.path
        q = parse_qs(parts.query)
        m = re.match(r"^/mod/(\w+)/view\.php$", rel)
        if m and q.get("id"):
            key = f"cm:{q['id'][0]}"
            key = self.alias.get(key, key)
            if key in self.items and self.items[key].kind != "label":
                return f"@@ITEM:{key}@@"
            return None
        if rel == "/course/section.php" and q.get("id"):
            key = f"section:{q['id'][0]}"
            return f"@@ITEM:{key}@@" if key in self.items else None
        if rel == "/course/view.php" and q.get("id") == [str(self.cid)]:
            if q.get("section"):
                for it in self.items.values():
                    if it.kind == "section" and not it.module and str(it.raw.get("section")) == q["section"][0]:
                        return f"@@ITEM:{it.key}@@"
            return "@@ITEM:course@@"
        if self.client.is_file_url(url):
            if self._stream_manifest(url):
                self._missing_stream(item, url)
                return None
            spec = self._media_spec(url, item, kind="linked")
            return f"@@FILE:{spec.index}@@"
        return None

    def _resolve(self, text: str, from_rel: str) -> str:
        def repl(m: re.Match) -> str:
            kind, ref = m.group(1), m.group(2)
            if kind == "PATH":
                return rel_link(from_rel, unquote(ref))
            if kind == "FILE":
                spec = self.specs[int(ref)]
                if spec.local_ok or fsutil.is_file(self.root / spec.rel_path):
                    return rel_link(from_rel, spec.rel_path)
                return md_url(spec.url)
            if ref == "course":
                return rel_link(from_rel, "course_overview.md")
            key = self.alias.get(ref, ref)
            target = self.items.get(key)
            if target is not None and target.md_path:
                return rel_link(from_rel, target.md_path)
            return md_url(target.url) if target is not None and target.url else "#"

        return PH_RE.sub(repl, text)

    def _missing(self, item: Item, reason: str, url: str = "") -> None:
        entry = (reason, url or item.url)
        if entry not in item.missing:
            item.missing.append(entry)

    # ------------------------------------------------------------------ bulk data
    def _fetch_bulk(self) -> None:
        present = {it.modname for it in self.items.values() if it.kind in ("activity", "label")}
        for mod in sorted(present):
            spec = BULK.get(mod)
            if not spec:
                continue
            fn, key, idf = spec
            result, err = self.client.try_call(fn, courseids=[self.cid])
            if err is not None:
                log.info("%s non disponibile: %s", fn, err)
                continue
            self.raw[fn] = result
            if key == "#assign":
                rows = [a for c in (result or {}).get("courses", []) for a in c.get("assignments", [])]
            elif key == "#list":
                rows = result if isinstance(result, list) else []
            else:
                rows = (result or {}).get(key, [])
            self.bulk[mod] = {int(r.get(idf) or 0): r for r in rows}

    # ------------------------------------------------------------------ rendering: sections
    def _render_section(self, sec: Item) -> None:
        conv = self._converter(sec, base_url=self.course_url)
        s = sec.raw
        module = sec.module or {}
        if s.get("uservisible") is False or module.get("uservisible") is False:
            info = html_to_text(module.get("availabilityinfo") or s.get("availabilityinfo") or "")
            sec.restricted = info or "non disponibile"
            self._missing(sec, f"Sezione con accesso limitato: {sec.restricted}")
        elif module.get("availabilityinfo") or s.get("availabilityinfo"):
            sec.facts.append(("Condizioni", html_to_text(module.get("availabilityinfo") or s.get("availabilityinfo"))))
        sec.description = conv.convert(s.get("summary") or "")
        for child in sec.children:
            if child.kind != "label":
                continue
            inst = self.bulk.get("label", {}).get(int(child.raw["id"]))
            html = child.raw.get("description") or (inst or {}).get("intro") or ""
            child.description = self._converter(child, base_url=self.course_url).convert(html)
            if child.raw.get("uservisible") is False:
                info = html_to_text(child.raw.get("availabilityinfo") or "")
                child.restricted = info or "non disponibile"
                self._missing(child, f"Testo con accesso limitato: {child.restricted}")

    # ------------------------------------------------------------------ rendering: activities
    def _render_activity(self, item: Item) -> None:
        m = item.raw
        mod = item.modname
        inst = self.bulk.get(mod, {}).get(int(m["id"]))
        item.inst = inst
        conv = self._converter(item)
        if m.get("uservisible") is False:
            info = html_to_text(m.get("availabilityinfo") or "")
            item.restricted = info or "non disponibile per il tuo account"
            self._missing(item, f"Accesso limitato: {item.restricted}")
            item.description = conv.convert(m.get("description") or "")
            return
        if m.get("availabilityinfo"):
            item.facts.append(("Condizioni", html_to_text(m["availabilityinfo"])))
        for d in m.get("dates") or []:
            label = html_to_text(d.get("label") or "").rstrip(":").strip()
            if label and d.get("timestamp"):
                item.facts.append((label, fmt_dt(d["timestamp"])))
        cd = m.get("completiondata") or {}
        if cd.get("hascompletion"):
            item.facts.append(("Completamento", "completato" if cd.get("state") in (1, 2) else "da completare"))
        intro = (inst or {}).get("intro")
        item.description = conv.convert(intro if intro else (m.get("description") or ""))
        handler = getattr(self, f"_h_{mod}", None) or self._h_generic
        try:
            handler(item, inst or {}, conv)
        except (TokenInvalidError, NetworkError, CancelledError):
            raise
        except MoodleError as exc:
            self._missing(item, f"Errore di Moodle durante l'esportazione: {exc}")
            self.errors.append(f"{item.title}: {exc}")
        except Exception as exc:
            log.error("Handler %s failed\n%s", mod, traceback.format_exc())
            self._missing(item, f"Errore interno durante l'esportazione: {exc}")
            self.errors.append(f"{item.title}: {exc}")

    def _h_page(self, item: Item, inst: dict, conv: Converter) -> None:
        html = inst.get("content")
        if html is None:
            idx = next((c for c in item.raw.get("contents", []) if c.get("filename") == "index.html"), None)
            if idx and idx.get("fileurl"):
                html = self.client.fetch_text(idx["fileurl"])
        if html is None:
            self._missing(item, "Contenuto della pagina non disponibile tramite l'API")
            return
        body = normalize_heading_levels(conv.convert(html), 3)
        if inst.get("timemodified"):
            item.facts.append(("Ultima modifica", fmt_dt(inst["timemodified"])))
        item.body = "## Contenuto\n\n" + body if body else ""

    def _h_resource(self, item: Item, inst: dict, conv: Converter) -> None:
        if not any(s.owner == item.key for s in self.specs):
            self._missing(item, "Nessun file scaricabile per questa risorsa")

    _h_folder = _h_resource
    _h_imscp = _h_resource

    def _h_url(self, item: Item, inst: dict, conv: Converter) -> None:
        ext = inst.get("externalurl") or next((c.get("fileurl") for c in item.raw.get("contents", [])
                                               if c.get("type") == "url"), "")
        if not ext:
            self._missing(item, "Indirizzo del link non disponibile")
            return
        provider, canonical = classify_media_url(ext)
        if self.client.is_file_url(ext) and not self._stream_manifest(ext):
            spec = self._media_spec(ext, item, kind="material", subdir="")
            item.body = f"**File collegato:** [{escape_md(spec.label)}](@@FILE:{spec.index}@@)"
            return
        lines = [f"**Link:** [{escape_md(ext)}]({md_url(ext)})"]
        if provider and provider != "H5P":
            lines.append(f"*Video su {provider}: conservato solo come link da questa versione.*")
        item.body = "\n\n".join(lines)
        self._online_media(item, canonical, item.title, provider or "link")

    def _h_book(self, item: Item, inst: dict, conv: Converter) -> None:
        contents = item.raw.get("contents") or []
        structure = next((c for c in contents if c.get("filename") == "structure"), None)
        chapters = []

        def walk(entries: list, level: int) -> None:
            for e in entries:
                chapters.append((e, level))
                walk(e.get("subitems") or [], level + 1)

        try:
            walk(json.loads(structure.get("content") or "[]") if structure else [], 0)
        except ValueError:
            pass
        files_by_dir: dict = {}
        for c in contents:
            if c.get("type") == "file":
                files_by_dir.setdefault((c.get("filepath") or "/").strip("/"), []).append(c)
        parts = []
        number = [0, 0]
        numbered = str(inst.get("numbering", 1)) == "1"
        for chapter, level in chapters:
            if str(chapter.get("hidden")) == "1":
                continue
            chap_dir = (chapter.get("href") or "").rsplit("/", 1)[0].strip("/")
            files = files_by_dir.get(chap_dir, [])
            index = next((f for f in files if f.get("filename") == "index.html"), None)
            title = unescape_name(chapter.get("title") or "")
            if level == 0:
                number = [number[0] + 1, 0]
                label = f"{number[0]}. {title}" if numbered else title
            else:
                number[1] += 1
                label = f"{number[0]}.{number[1]} {title}" if numbered else title
            heading = "#" * min(6, 3 + level)
            text = ""
            if index and index.get("fileurl"):
                html = self.client.fetch_text(index["fileurl"])
                html = re.sub(r"^\s*(?:<h[1-6][^>]*>.*?</h[1-6]>\s*)+", "", html, count=1, flags=re.S)
                cconv = self._converter(item, base_url=index["fileurl"])
                text = normalize_heading_levels(cconv.convert(html), 4 + level)
            for f in files:
                if f.get("filename") != "index.html" and f.get("fileurl"):
                    self._media_spec(f["fileurl"], item, name=f.get("filename") or "",
                                     size=f.get("filesize"), mtime=f.get("timemodified"))
            parts.append(f"{heading} {escape_md(label)}\n\n{text}".rstrip())
        if parts:
            item.body = "## Capitoli\n\n" + "\n\n".join(parts)
        else:
            self._missing(item, "Il libro non contiene capitoli visibili")

    def _h_assign(self, item: Item, inst: dict, conv: Converter) -> None:
        lines = []
        if inst.get("activity"):
            act = conv.convert(inst["activity"])
            if act:
                lines.append("## Istruzioni per la consegna\n\n" + act)
        dates = [("Consegna possibile da", inst.get("allowsubmissionsfromdate")), ("Scadenza", inst.get("duedate")),
                 ("Termine ultimo", inst.get("cutoffdate")), ("Valutazione prevista entro", inst.get("gradingduedate"))]
        for label, ts in dates:
            if ts and fmt_dt(ts) not in {v for _, v in item.facts}:
                item.facts.append((label, fmt_dt(ts)))
        configs = {(c.get("plugin"), c.get("subtype"), c.get("name")): c.get("value")
                   for c in inst.get("configs") or []}
        kinds = []
        if configs.get(("onlinetext", "assignsubmission", "enabled")) == "1":
            kinds.append("testo online")
        if configs.get(("file", "assignsubmission", "enabled")) == "1":
            n = configs.get(("file", "assignsubmission", "maxfilesubmissions"))
            kinds.append(f"file (max {n})" if n else "file")
        if kinds:
            item.facts.append(("Tipo di consegna", ", ".join(kinds)))
        attach = [f for f in (inst.get("introattachments") or [])]
        status = None
        if self.opts.include_submissions and inst.get("id"):
            status, err = self.client.try_call("mod_assign_get_submission_status", assignid=inst["id"])
            if err is not None:
                log.info("submission status non disponibile: %s", err)
                status = None
        if status:
            self.raw.setdefault("mod_assign_get_submission_status", {})[str(inst["id"])] = status
            att = (status.get("assignmentdata") or {}).get("attachments") or {}
            for f in att.get("intro") or []:
                if not any(a.get("fileurl") == f.get("fileurl") for a in attach):
                    attach.append(f)
            act_files = att.get("activity") or []
        else:
            act_files = []
        for f in attach:
            self._file_entry_spec(f, item, "", "material", f"cm:{item.raw['id']}:intro")
        for f in act_files:
            self._file_entry_spec(f, item, "", "material", f"cm:{item.raw['id']}:activity")
        if status:
            sub = (status.get("lastattempt") or {}).get("submission") or (
                (status.get("lastattempt") or {}).get("teamsubmission")) or {}
            if sub:
                block = [f"- **Stato:** {sub.get('status', '')}"]
                if sub.get("timemodified"):
                    block.append(f"- **Ultima modifica:** {fmt_dt(sub['timemodified'])}")
                body = []
                for plugin in sub.get("plugins") or []:
                    for ef in plugin.get("editorfields") or []:
                        txt = conv.convert(ef.get("text") or "")
                        if txt:
                            body.append(txt)
                    for area in plugin.get("fileareas") or []:
                        for f in area.get("files") or []:
                            spec = self._file_entry_spec(f, item, "consegna", "submission",
                                                         f"sub:{area.get('area')}")
                            if spec:
                                block.append(f"- File consegnato: [{escape_md(spec.label)}](@@FILE:{spec.index}@@)")
                lines.append("## La mia consegna\n\n" + "\n".join(block) + ("\n\n" + "\n\n".join(body) if body else ""))
            fb = status.get("feedback") or {}
            if fb:
                block = []
                if fb.get("gradefordisplay"):
                    block.append(f"- **Valutazione:** {html_to_text(fb['gradefordisplay'])}")
                if fb.get("gradeddate"):
                    block.append(f"- **Valutato il:** {fmt_dt(fb['gradeddate'])}")
                body = []
                for plugin in fb.get("plugins") or []:
                    for ef in plugin.get("editorfields") or []:
                        txt = conv.convert(ef.get("text") or "")
                        if txt:
                            body.append(txt)
                    for area in plugin.get("fileareas") or []:
                        for f in area.get("files") or []:
                            spec = self._file_entry_spec(f, item, "feedback", "feedback", f"fb:{area.get('area')}")
                            if spec:
                                block.append(f"- File di feedback: [{escape_md(spec.label)}](@@FILE:{spec.index}@@)")
                if block or body:
                    lines.append("## Valutazione e feedback\n\n" + "\n".join(block) +
                                 ("\n\n" + "\n\n".join(body) if body else ""))
        if status and not inst.get("activity"):
            act = (status.get("assignmentdata") or {}).get("activity")
            if act:
                text = conv.convert(act)
                if text:
                    lines.insert(0, "## Istruzioni per la consegna\n\n" + text)
        item.body = "\n\n".join(lines)

    def _h_quiz(self, item: Item, inst: dict, conv: Converter) -> None:
        if inst.get("timelimit"):
            item.facts.append(("Tempo a disposizione", f"{int(inst['timelimit']) // 60} min"))
        if "attempts" in inst:
            item.facts.append(("Tentativi consentiti", str(inst["attempts"]) if inst["attempts"] else "illimitati"))
        if inst.get("grade"):
            item.facts.append(("Punteggio massimo", f"{float(inst['grade']):g}"))
        for label, key in (("Apertura", "timeopen"), ("Chiusura", "timeclose")):
            if inst.get(key) and fmt_dt(inst[key]) not in {v for _, v in item.facts}:
                item.facts.append((label, fmt_dt(inst[key])))
        if not self.opts.include_quiz_reviews or not inst.get("id"):
            return
        attempts, err = self.client.try_call("mod_quiz_get_user_attempts", quizid=inst["id"], status="all",
                                             includepreviews=0)
        if err is not None:
            self._missing(item, f"Tentativi non leggibili: {err}")
            return
        finished = [a for a in (attempts or {}).get("attempts", []) if a.get("state") == "finished"]
        if not finished:
            self._missing(item, "Nessun tentativo concluso: le domande si vedono solo svolgendo il quiz in Moodle")
            item.body = ("> Il programma non avvia tentativi: se svolgi il quiz in Moodle e la revisione è permessa, "
                         "domande, risposte e feedback verranno salvati alla prossima esportazione.")
            return
        parts = []
        for att in finished:
            review, err = self.client.try_call("mod_quiz_get_attempt_review", attemptid=att["id"], page=-1)
            head = f"### Tentativo {att.get('attempt')} — concluso il {fmt_dt(att.get('timefinish'))}"
            if err is not None:
                reason = ("revisione non consentita dal docente" if err.errorcode in ("noreview", "noreviewattempt")
                          else str(err))
                parts.append(f"{head}\n\n*Revisione non disponibile: {reason}.*")
                self._missing(item, f"Tentativo {att.get('attempt')}: {reason}")
                continue
            self.raw.setdefault("mod_quiz_get_attempt_review", {})[str(att["id"])] = review
            grade = review.get("grade")
            lines = [head]
            if grade not in (None, ""):
                try:
                    lines.append(f"**Voto:** {float(grade):.2f} / {float(inst.get('grade') or 0):g}")
                except (TypeError, ValueError):
                    lines.append(f"**Voto:** {grade}")
            qconv = self._converter(item)
            for q in review.get("questions") or []:
                lines.append(quiz_question_to_markdown(q.get("html") or "", qconv, heading_level=4))
            for extra in review.get("additionaldata") or []:
                txt = conv.convert(extra.get("content") or "")
                if txt:
                    lines.append(f"**{escape_md(html_to_text(extra.get('title') or ''))}:** {txt}")
            parts.append("\n\n".join(lines))
        item.body = "## I miei tentativi\n\n" + "\n\n".join(parts)

    def _h_forum(self, item: Item, inst: dict, conv: Converter) -> None:
        if not self.opts.include_forums:
            self._missing(item, "Discussioni del forum escluse dalle impostazioni")
            return
        if not inst.get("id"):
            self._missing(item, "Forum non leggibile tramite l'API")
            return
        discussions = []
        page = 0
        while len(discussions) < self.opts.max_forum_discussions:
            res, err = self.client.try_call("mod_forum_get_forum_discussions", forumid=inst["id"], sortorder=-1,
                                            page=page, perpage=100)
            if err is not None:
                self._missing(item, f"Discussioni non leggibili: {err}")
                break
            batch = (res or {}).get("discussions") or []
            discussions.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        if not discussions:
            item.body = "*Nessuna discussione.*" if not item.missing else ""
            return
        discussions.sort(key=lambda d: (not d.get("pinned"), d.get("created") or 0))
        parts = []
        for n, d in enumerate(discussions, 1):
            self.client.check_cancel()
            posts_res, err = self.client.try_call("mod_forum_get_discussion_posts", discussionid=d["discussion"],
                                                  sortby="created", sortdirection="ASC")
            title = html_to_text(d.get("name") or d.get("subject") or "Discussione")
            lines = [f"### {escape_md(title)}"]
            if err is not None:
                lines.append(f"*Messaggi non leggibili: {err}*")
                parts.append("\n\n".join(lines))
                continue
            for post in (posts_res or {}).get("posts") or []:
                author_info = post.get("author") or {}
                author = self.pseudo.name(author_info.get("id"), author_info.get("fullname") or "",
                                          staff=inst.get("type") == "news")
                subject = html_to_text(post.get("subject") or "")
                lines.append(f"#### {escape_md(subject)} — {escape_md(author)}, {fmt_dt(post.get('timecreated'))}")
                text = normalize_heading_levels(conv.convert(post.get("message") or ""), 5)
                if text:
                    lines.append(text)
                for f in post.get("attachments") or []:
                    spec = self._file_entry_spec(f, item, f"allegati/{n:03d}", "attachment", f"post:{post.get('id')}")
                    if spec:
                        lines.append(f"- Allegato: [{escape_md(spec.label)}](@@FILE:{spec.index}@@)")
            parts.append("\n\n".join(lines))
        note = ("*I nomi degli altri studenti sono sostituiti da «Partecipante N» (impostazione privacy); "
                "restano visibili il tuo nome e quelli dei docenti.*\n\n") if self.pseudo.used else ""
        item.body = "## Discussioni\n\n" + note + "\n\n".join(parts)
        item.facts.append(("Discussioni", str(len(discussions))))

    def _h_glossary(self, item: Item, inst: dict, conv: Converter) -> None:
        if not inst.get("id"):
            return
        entries = []
        start = 0
        while True:
            res, err = self.client.try_call("mod_glossary_get_entries_by_letter", id=inst["id"], letter="ALL",
                                            **{"from": start}, limit=100, options={"includenotapproved": 0})
            if err is not None:
                self._missing(item, f"Voci del glossario non leggibili: {err}")
                break
            batch = (res or {}).get("entries") or []
            entries.extend(batch)
            start += len(batch)
            if not batch or start >= int((res or {}).get("count") or 0):
                break
        entries.sort(key=lambda e: html_to_text(e.get("concept") or "").lower())
        parts = []
        for e in entries:
            text = normalize_heading_levels(conv.convert(e.get("definition") or ""), 4)
            block = [f"### {escape_md(html_to_text(e.get('concept') or ''))}", text]
            for f in e.get("attachments") or []:
                spec = self._file_entry_spec(f, item, "allegati", "attachment", f"glossary:{e.get('id')}")
                if spec:
                    block.append(f"- Allegato: [{escape_md(spec.label)}](@@FILE:{spec.index}@@)")
            parts.append("\n\n".join(b for b in block if b))
        if parts:
            item.body = f"## Voci ({len(parts)})\n\n" + "\n\n".join(parts)

    def _h_wiki(self, item: Item, inst: dict, conv: Converter) -> None:
        if not inst.get("id"):
            return
        subwikis, err = self.client.try_call("mod_wiki_get_subwikis", wikiid=inst["id"])
        if err is not None:
            self._missing(item, f"Pagine del wiki non leggibili: {err}")
            return
        parts = []
        for sw in (subwikis or {}).get("subwikis") or []:
            pages, err = self.client.try_call("mod_wiki_get_subwiki_pages", wikiid=inst["id"],
                                              groupid=sw.get("groupid", 0), userid=sw.get("userid", 0),
                                              options={"sortby": "title", "sortdirection": "ASC", "includecontent": 1})
            for p in (pages or {}).get("pages") or []:
                text = normalize_heading_levels(conv.convert(p.get("cachedcontent") or ""), 4)
                parts.append(f"### {escape_md(html_to_text(p.get('title') or ''))}\n\n{text}".rstrip())
        if parts:
            item.body = "## Pagine\n\n" + "\n\n".join(parts)

    def _h_choice(self, item: Item, inst: dict, conv: Converter) -> None:
        if not inst.get("id"):
            return
        res, err = self.client.try_call("mod_choice_get_choice_options", choiceid=inst["id"])
        options = (res or {}).get("options") or []
        if options:
            item.body = "## Opzioni\n\n" + "\n".join(
                f"- {'☑' if o.get('checked') else '☐'} {escape_md(html_to_text(o.get('text') or ''))}"
                for o in options)

    def _h_generic(self, item: Item, inst: dict, conv: Converter) -> None:
        note = LINK_ONLY_NOTES.get(item.modname)
        has_files = any(s.owner == item.key for s in self.specs)
        if note:
            tip = VIDEO_TIP if item.modname in VIDEO_MODS else MANUAL_TIP
            item.body = f"*{note}*\n\n> {tip}"
            if not has_files:
                self._missing(item, note)
        elif not has_files and not item.description:
            self._missing(item, f"Tipo di attività «{item.modname}» non supportato: aprirlo in Moodle")

    # ------------------------------------------------------------------ downloads
    def _version_out(self, rel: str) -> None:
        src = self.root / rel
        if not fsutil.exists(src):
            return
        if not self.opts.keep_versions:
            fsutil.remove(src)
            return
        p = PurePosixPath(rel)
        try:
            stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(os.path.getmtime(fsutil.lp(src))))
        except OSError:
            stamp = time.strftime("%Y-%m-%d_%H%M")
        dst = self.root / VERSIONS_DIR / p.parent / f"{p.stem}__{stamp}{p.suffix}"
        n = 2
        while fsutil.exists(dst):
            dst = self.root / VERSIONS_DIR / p.parent / f"{p.stem}__{stamp}_{n}{p.suffix}"
            n += 1
        fsutil.move(src, dst)
        self.counts["versioned"] += 1

    def _reconcile_moves(self) -> None:
        moves = []
        for spec in self.specs:
            entry = self.state.files.get(spec.key)
            if entry and entry.get("path") and entry["path"] != spec.rel_path and \
                    fsutil.is_file(self.root / entry["path"]):
                moves.append((spec, entry["path"]))
        if not moves:
            return
        staging = self.root / STATE_DIR / "staging"
        staged = []
        for spec, old in moves:
            tmp = staging / hashlib.sha1(spec.key.encode("utf-8")).hexdigest()
            fsutil.move(self.root / old, tmp)
            staged.append((spec, old, tmp))
        for spec, old, tmp in staged:
            dest = self.root / spec.rel_path
            if fsutil.exists(dest):
                self._version_out(spec.rel_path)
            fsutil.move(tmp, dest)
            self.state.files[spec.key]["path"] = spec.rel_path
            fsutil.remove_empty_dirs(self.root, (self.root / old).parent)
            self.counts["moved"] += 1
        self.state.save()

    def _download_all(self) -> None:
        todo = [s for s in self.specs if s.status == "pending"]
        total = len(todo)
        for n, spec in enumerate(todo, 1):
            self.client.check_cancel()
            self.x.emit("progress", scope="files", done=n - 1, total=total,
                        message=f"{spec.label} {('(' + human_size(spec.size) + ')') if spec.size else ''}".strip())
            self._download_one(spec, n, total)
            self.state.save(force=False)
        if total:
            self.x.emit("progress", scope="files", done=total, total=total, message="")
        self.state.save()

    def _record(self, spec: FileSpec, size: int | None, last_modified: str) -> None:
        entry = self.state.files.setdefault(spec.key, {})
        entry.update({"path": spec.rel_path, "size": spec.size if spec.size is not None else size,
                      "mtime": spec.mtime, "url": self.client.file_key(spec.url)})
        if last_modified:
            entry["last_modified"] = last_modified
        entry["checked"] = self.now
        self.state.touch()

    def _download_one(self, spec: FileSpec, n: int, total: int) -> None:
        try:
            self._download_one_inner(spec)
        except OSError as exc:  # e.g. file open in another program, disk full, path problems
            spec.status, spec.message = "error", f"errore sul disco: {exc.strerror or exc}"
            self.counts["error"] += 1
            owner = self.items.get(spec.owner)
            if owner is not None:
                self._missing(owner, f"{spec.label}: download non riuscito ({spec.message})", spec.url)
            self.errors.append(f"{spec.rel_path}: {exc}")
            self.x.log(f"Impossibile salvare {spec.rel_path}: {exc.strerror or exc} "
                       "(il file è forse aperto in un altro programma?)", "warning")

    def _download_one_inner(self, spec: FileSpec) -> None:
        dest = self.root / spec.rel_path
        entry = self.state.files.get(spec.key)
        have = fsutil.is_file(dest)
        if have and entry and entry.get("path") == spec.rel_path and not spec.conditional and \
                entry.get("size") == spec.size and entry.get("mtime") == spec.mtime:
            spec.status = "unchanged"
            self.counts["unchanged"] += 1
            return
        if have and not entry and spec.size is not None and fsutil.getsize(dest) == spec.size:
            self._record(spec, spec.size, "")
            spec.status = "unchanged"
            self.counts["unchanged"] += 1
            return
        ims = entry.get("last_modified", "") if (have and entry and spec.conditional) else ""
        target = dest.with_name(dest.name + ".new") if have else dest

        def progress(done: int, size: int | None) -> None:
            limit = int(self.opts.max_file_mb or 0) * 1024 * 1024
            if limit and max(done, size or 0) > limit:
                raise MoodleError(f"file più grande di {self.opts.max_file_mb} MB", "filetoolarge")
            self.x.emit("progress", scope="bytes", done=done, total=size or 0, message=spec.label)

        try:
            res = self.client.download(spec.url, target, expected_size=spec.size, if_modified_since=ims,
                                       progress=progress)
        except (TokenInvalidError, NetworkError, CancelledError):
            raise
        except MoodleError as exc:
            if exc.errorcode == "filetoolarge":
                fsutil.remove(target.with_name(target.name + ".part"))
                spec.status, spec.message = "skipped", str(exc)
                self.counts["skipped"] += 1
                owner = self.items.get(spec.owner)
                if owner is not None:
                    self._missing(owner, f"{spec.label}: {spec.message}", spec.url)
                return
            spec.status, spec.message = "error", str(exc)
            self.counts["error"] += 1
            owner = self.items.get(spec.owner)
            if owner is not None:
                self._missing(owner, f"{spec.label}: download non riuscito ({exc})", spec.url)
            self.errors.append(f"{spec.label}: {exc}")
            self.x.log(f"Download non riuscito: {spec.label} ({exc})", "warning")
            return
        if res.status == "not_modified":
            self._record(spec, entry.get("size") if entry else None, ims)
            spec.status = "unchanged"
            self.counts["unchanged"] += 1
            return
        if have:
            if fsutil.sha256_file(target) == fsutil.sha256_file(dest):
                fsutil.remove(target)
                spec.status = "unchanged"
                self.counts["unchanged"] += 1
            else:
                self._version_out(spec.rel_path)
                fsutil.move(target, dest)
                spec.status = "updated"
                self.counts["updated"] += 1
                self.changed_files.append(spec.rel_path)
        else:
            spec.status = "new"
            self.counts["new"] += 1
        self._record(spec, res.size, res.last_modified)

    def _scan_manual_files(self) -> None:
        """Files the user saved into an activity folder (e.g. a PDF printout of an H5P activity)."""
        known = {s.rel_path.lower() for s in self.specs}
        known |= {str(e.get("path", "")).lower() for e in self.state.files.values()}
        root_lp = fsutil.lp(self.root)
        for item in self._walk():
            if item.kind != "activity" or not item.rel_dir:
                continue
            folder = self.root / item.rel_dir
            if not fsutil.exists(folder):
                continue
            found = []
            for dirpath, dirnames, filenames in os.walk(fsutil.lp(folder)):
                dirnames[:] = sorted(d for d in dirnames if d != MEDIA_DIR and not d.startswith("."))
                for name in sorted(filenames):
                    if name.startswith(".") or name.endswith((".part", ".new", ".tmp")):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, name), root_lp).replace(os.sep, "/")
                    if rel.lower() in known or rel == item.md_path:
                        continue
                    found.append(rel)
            item.manual_files = found

    # ------------------------------------------------------------------ writing
    def _write_text(self, rel: str, content: str, owner: str, version: bool = True) -> str:
        try:
            return self._write_text_inner(rel, content, owner, version)
        except OSError as exc:
            self.errors.append(f"{rel}: {exc}")
            self.x.log(f"Impossibile scrivere {rel}: {exc.strerror or exc} (file aperto in un altro programma?)",
                       "warning")
            return "error"

    def _write_text_inner(self, rel: str, content: str, owner: str, version: bool = True) -> str:
        path = self.root / rel
        data = scrub_secrets(content).encode("utf-8")
        sha = fsutil.sha256_bytes(data)
        self.texts_now[rel] = owner
        prev = self.state.texts.get(rel) or {}
        status = "new"
        if fsutil.is_file(path):
            if prev.get("sha") == sha:
                return "unchanged"
            if fsutil.sha256_file(path) == sha:
                self.state.texts[rel] = {"sha": sha, "owner": owner}
                return "unchanged"
            if version and self.opts.keep_versions:
                self._version_out(rel)
            status = "updated"
        fsutil.write_bytes_atomic(path, data)
        self.state.texts[rel] = {"sha": sha, "owner": owner}
        self.counts[f"texts_{status}"] += 1
        return status

    def _frontmatter(self, item: Item) -> str:
        lines = ["---", f"title: {yaml_str(item.title)}"]
        if item.kind == "section":
            lines.append(f"type: {'subsection' if item.module else 'section'}")
        else:
            lines.append(f"type: {item.modname}")
        lines.append(f"course: {yaml_str(self.title)}")
        crumbs = item.breadcrumb()
        if crumbs:
            lines.append(f"section: {yaml_str(' › '.join(crumbs))}")
        if item.url:
            lines.append(f"moodle_url: {yaml_str(item.url)}")
        ident = item.raw.get("id")
        if ident is not None:
            lines.append(f"moodle_id: {ident}")
        if item.restricted:
            lines.append(f"restricted: {yaml_str(item.restricted)}")
        lines.append("---")
        return "\n".join(lines)

    def _meta_line(self, item: Item) -> str:
        crumbs = item.breadcrumb()
        parts = [f"*{item.type_label}*"]
        if crumbs:
            parts.append(" › ".join(escape_md(c) for c in crumbs))
        if item.url:
            parts.append(f"[Apri in Moodle]({md_url(item.url)})")
        return " · ".join(parts)

    def _files_of(self, item: Item, kinds: tuple = ("material", "video")) -> list:
        return [s for s in self.specs if s.owner == item.key and s.kind in kinds]

    def _file_line(self, spec: FileSpec, indent: str = "") -> str:
        size = f" ({human_size(spec.size)})" if spec.size else ""
        note = f" — *non scaricato: {spec.message}*" if spec.status in ("skipped", "error") else ""
        shown = spec.rel_path.split("/")
        label = spec.label or shown[-1]
        return f"{indent}- [{escape_md(label)}](@@FILE:{spec.index}@@){size}{note}"

    def _activity_body(self, item: Item, heading_level: int = 2) -> list:
        """Blocks for an activity: facts, description, content, files, links, missing."""
        blocks = []
        if item.restricted:
            blocks.append(f"> **Accesso limitato:** {escape_md(item.restricted)}")
        if item.facts:
            blocks.append("\n".join(f"- **{escape_md(k)}:** {escape_md(v)}" for k, v in item.facts))
        h = "#" * heading_level
        if item.description:
            blocks.append(f"{h} Descrizione\n\n{normalize_heading_levels(item.description, heading_level + 1)}")
        files = self._files_of(item)
        if files:
            blocks.append(f"{h} File\n\n" + "\n".join(self._file_line(s) for s in files))
        if item.body:
            blocks.append(shift_headings(item.body, heading_level - 2))
        if item.manual_files:
            blocks.append(f"{h} File aggiunti da te\n\n" + "\n".join(
                f"- [{escape_md(posixpath.basename(r))}](@@PATH:{quote(r, safe='/')}@@)" for r in item.manual_files))
        if item.links:
            blocks.append(f"{h} Video e link esterni\n\n" + "\n".join(
                f"- [{escape_md(t or u)}]({md_url(u)})" + (f" ({p})" if p and p not in ("link", "embed") else "")
                for t, u, p in item.links))
        if item.missing:
            blocks.append(f"{h} Non esportato\n\n" + "\n".join(
                f"- {escape_md(r)}" + (f" — [Moodle]({md_url(u)})" if u else "") for r, u in item.missing))
        return blocks

    def _activity_md(self, item: Item) -> str:
        blocks = [self._frontmatter(item), f"# {escape_md(item.title)}", self._meta_line(item)]
        blocks += self._activity_body(item, 2)
        return self._resolve("\n\n".join(blocks).strip() + "\n", item.md_path)

    def _child_line(self, child: Item, indent: str = "") -> str:
        extra = first_line(child.description) if child.description else ""
        bits = [child.type_label]
        if child.restricted:
            bits.append(f"accesso limitato: {child.restricted}")
        if extra:
            bits.append(extra)
        return f"{indent}- [{escape_md(child.title)}](@@ITEM:{child.key}@@) — " + " · ".join(escape_md(b) for b in bits)

    def _section_md(self, sec: Item) -> str:
        blocks = [self._frontmatter(sec), f"# {escape_md(sec.title)}", self._meta_line(sec)]
        if sec.restricted:
            blocks.append(f"> **Accesso limitato:** {escape_md(sec.restricted)}")
        if sec.facts:
            blocks.append("\n".join(f"- **{escape_md(k)}:** {escape_md(v)}" for k, v in sec.facts))
        if sec.description:
            blocks.append(normalize_heading_levels(sec.description, 2))
        run: list = []
        for child in sec.children:
            if child.kind == "label":
                if run:
                    blocks.append("\n".join(run))
                    run = []
                if child.description:
                    blocks.append(normalize_heading_levels(child.description, 2, exact=True))
                if child.restricted:
                    blocks.append(f"> *Testo con accesso limitato: {escape_md(child.restricted)}*")
            else:
                run.append(self._child_line(child))
        if run:
            blocks.append("\n".join(run))
        if sec.missing:
            blocks.append("## Non esportato\n\n" + "\n".join(f"- {escape_md(r)}" for r, _ in sec.missing))
        return self._resolve("\n\n".join(blocks).strip() + "\n", sec.md_path)

    def _overview_tree(self, sec: Item, level: int) -> list:
        h = "#" * min(6, level)
        blocks = [f"{h} {escape_md(sec.title)}"]
        meta = [f"[Note della sezione](@@ITEM:{sec.key}@@)"]
        if sec.url:
            meta.append(f"[Moodle]({md_url(sec.url)})")
        blocks.append(" · ".join(meta))
        if sec.restricted:
            blocks.append(f"> **Accesso limitato:** {escape_md(sec.restricted)}")
        if sec.description:
            blocks.append(normalize_heading_levels(sec.description, min(6, level + 1)))
        run: list = []

        def flush() -> None:
            if run:
                blocks.append("\n".join(run))
                run.clear()

        for child in sec.children:
            if child.kind == "label":
                flush()
                if child.description:
                    blocks.append(normalize_heading_levels(child.description, min(6, level + 1), exact=True))
            elif child.kind == "section":
                flush()
                blocks.extend(self._overview_tree(child, level + 1))
            else:
                run.append(self._child_line(child))
                for spec in self._files_of(child, ("material", "video", "submission", "feedback", "attachment")):
                    run.append(self._file_line(spec, "  "))
                for rel in child.manual_files:
                    run.append(f"  - [{escape_md(posixpath.basename(rel))}](@@PATH:{quote(rel, safe='/')}@@)"
                               " *(aggiunto da te)*")
                for t, u, p in child.links:
                    if p and p not in ("link", "embed"):
                        run.append(f"  - {p}: [{escape_md(t or u)}]({md_url(u)})")
        flush()
        return blocks

    def _course_header(self) -> list:
        d = self.details
        contacts = ", ".join(c.get("fullname", "") for c in d.get("contacts") or [])
        start = fmt_d(d.get("startdate") or self.course.get("startdate"))
        end = fmt_d(d.get("enddate") or self.course.get("enddate"))
        facts = [f"- **Codice:** {escape_md(self.shortname)}"]
        if start or end:
            facts.append(f"- **Periodo:** {start}{' – ' + end if end else ''}")
        if contacts:
            facts.append(f"- **Docenti:** {escape_md(contacts)}")
        if d.get("categoryname"):
            facts.append(f"- **Categoria:** {escape_md(html_to_text(d['categoryname']))}")
        facts.append(f"- **Moodle:** [Apri il corso]({md_url(self.course_url)})")
        return facts

    def _course_summary_md(self) -> str:
        summary = self.details.get("summary") or self.course.get("summary") or ""
        return self._converter(self.root_item, base_url=self.course_url).convert(summary)

    def _overview_md(self) -> str:
        blocks = ["---", f"title: {yaml_str(self.title)}", "type: course", f"moodle_url: {yaml_str(self.course_url)}",
                  f"moodle_id: {self.cid}", "---"]
        blocks = ["\n".join(blocks), f"# {escape_md(self.title)}", "\n".join(self._course_header() + [
            "- **Nell'archivio:** [testo completo](course_fulltext.md) · [informazioni](course_info.md) · "
            "[elementi non esportati](elementi_mancanti.md) · [verifica con Moodle](verifica_corso.md) · "
            "[video e media](video_e_media.md) · "
            "[struttura JSON](course_structure.json)"])]
        if self.summary_md:
            blocks.append("## Descrizione del corso\n\n" + normalize_heading_levels(self.summary_md, 3))
        blocks.append("## Struttura del corso")
        for sec in self.root_item.children:
            blocks.extend(self._overview_tree(sec, 3))
        videos = [(it, t, u, p) for it in self._walk() for t, u, p in it.links if p not in ("link", "embed")]
        if videos:
            blocks.append("## Video e contenuti online\n\n" + "\n".join(
                f"- [{escape_md(t or u)}]({md_url(u)}) ({p}) — in [{escape_md(it.title)}](@@ITEM:{it.key}@@)"
                for it, t, u, p in videos))
        gone = [e for e in self.state.items.values() if e.get("removed") and e.get("type") != "label"]
        if gone:
            lines = []
            for e in sorted(gone, key=lambda e: e.get("removed") or 0):
                title = escape_md(e.get("title", ""))
                md = e.get("md")
                link = f"[{title}]({rel_link('course_overview.md', md)})" if md and \
                    fsutil.is_file(self.root / md) else title
                lines.append(f"- {link} — non più in Moodle dal {fmt_d(e.get('removed'))}")
            blocks.append("## Non più disponibili in Moodle\n\nCopie locali di elementi che non sono più visibili "
                          "in Moodle (rimossi o nascosti dal docente):\n\n" + "\n".join(lines))
        return self._resolve("\n\n".join(blocks).strip() + "\n", "course_overview.md")

    def _fulltext_md(self) -> str:
        blocks = [f"# {escape_md(self.title)} — testo completo",
                  "> Tutti i testi del corso Moodle nell'ordine originale: sezioni, obiettivi, pagine, libri, "
                  "istruzioni dei compiti, quiz rivisti e forum. I documenti (PDF, PowerPoint, …) sono elencati "
                  "con il percorso nell'archivio; il loro contenuto è nei file stessi.",
                  "\n".join(self._course_header())]
        if self.summary_md:
            blocks.append("## Descrizione del corso\n\n" + normalize_heading_levels(self.summary_md, 3))

        def section_blocks(sec: Item, level: int) -> None:
            h = "#" * min(6, level)
            blocks.append(f"{h} {escape_md(sec.title)}")
            if sec.restricted:
                blocks.append(f"> **Accesso limitato:** {escape_md(sec.restricted)}")
            if sec.description:
                blocks.append(normalize_heading_levels(sec.description, min(6, level + 1)))
            for child in sec.children:
                if child.kind == "label":
                    if child.description:
                        blocks.append(normalize_heading_levels(child.description, min(6, level + 1), exact=True))
                elif child.kind == "section":
                    section_blocks(child, level + 1)
                else:
                    alevel = min(6, level + 1)
                    blocks.append(f"{'#' * alevel} {escape_md(child.title)} ({escape_md(child.type_label)})")
                    blocks.append(f"[Nota nell'archivio](@@ITEM:{child.key}@@) · [Moodle]({md_url(child.url)})")
                    body = self._activity_body(child, min(6, alevel + 1))
                    blocks.extend(body)

        for sec in self.root_item.children:
            section_blocks(sec, 2)
        return self._resolve("\n\n".join(blocks).strip() + "\n", "course_fulltext.md")

    def _info_md(self) -> str:
        c = Counter(s.status for s in self.specs)
        blocks = [f"# {escape_md(self.title)} — informazioni", "\n".join(self._course_header())]
        if self.summary_md:
            blocks.append("## Descrizione\n\n" + normalize_heading_levels(self.summary_md, 3))
        blocks.append("## Ultima sincronizzazione\n\n" + "\n".join([
            f"- **Data:** {fmt_dt(self.now)}",
            f"- **File nell'archivio:** {c['new'] + c['updated'] + c['unchanged']} "
            f"(nuovi {c['new']}, aggiornati {c['updated']}, invariati {c['unchanged']})",
            f"- **File non scaricati:** {c['error'] + c['skipped']}",
            f"- **Sito:** {self.client.site_url} (Moodle {self.client.site_info_data.get('release', '?')})",
            f"- **Programma:** FFHS Moodle Archiver {__version__}"]))
        return self._resolve("\n\n".join(blocks).strip() + "\n", "course_info.md")

    @staticmethod
    def _missing_kind(item: Item, reason: str) -> str:
        low = reason.lower()
        if "accesso limitato" in low or "nascost" in low:
            return "limitato"
        if "download non riuscito" in low:
            return "errore"
        if "escluse dalle impostazioni" in low or "escluso dalle impostazioni" in low or "più grande di" in low:
            return "impostazioni"
        if low.startswith(("video", "audio")):
            return "video"
        if "h5p" in low:
            return "interattivo"
        if "conservato solo come link" in low:
            return "collegamento"
        if item.modname == "quiz":
            return "quiz"
        if item.modname in VIDEO_MODS:
            return "video"
        if item.modname in LINK_ONLY_NOTES:
            return "interattivo"
        return "altro"

    def _missing_md(self) -> str:
        lines = [f"# Elementi non esportati — {escape_md(self.title)}", "",
                 "Questi elementi esistono in Moodle ma non sono (completamente) nell'archivio. "
                 "Il link porta alla pagina in Moodle; per ogni gruppo trovi cosa puoi fare.", ""]
        groups: dict = {}
        for it in [self.root_item, *self._walk()]:
            for reason, url in it.missing:
                groups.setdefault(self._missing_kind(it, reason), []).append((it, reason, url))
        for kind, title, tip in MISSING_GROUPS:
            entries = groups.get(kind)
            if not entries:
                continue
            lines += [f"## {title} ({len(entries)})", "", f"*Cosa puoi fare:* {tip}", ""]
            for it, reason, url in entries:
                crumbs = " › ".join(escape_md(c) for c in it.breadcrumb())
                where = f" ({crumbs})" if crumbs else ""
                link = f"[{escape_md(it.title)}]({md_url(url)})" if url else escape_md(it.title)
                local = f" · [nota]({md_url(it.md_path)})" if it.md_path and it.kind == "activity" else ""
                if it.manual_files:
                    local += f" · aggiunti da te: {len(it.manual_files)} file"
                lines.append(f"- **{link}**{where} — {escape_md(reason)}{local}")
            lines.append("")
        if not groups:
            lines.append("Nessun elemento mancante rilevato nei dati elaborati. Questo controllo non garantisce "
                         "che le API espongano tutti i contenuti visibili nel browser: confronta "
                         "`verifica_corso.md` con Moodle. Libri e materiali citati nei documenti "
                         "non vengono individuati automaticamente.")
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------------ comparison checklist
    def _media_inventory_md(self) -> str:
        lines = [f"# Video e media — {escape_md(self.title)}", "",
                 "[Panoramica del corso](course_overview.md) · [Contenuti mancanti](elementi_mancanti.md)", "",
                 "File video/audio rilevati e contenuti rimasti online. Un player o un manifest "
                 "non equivale a un video scaricato. I file aggiunti manualmente restano nelle note "
                 "dell'attività e richiedono una verifica del contenuto.", "",
                 "| Attività | Media | Stato | Copia locale | Origine | Dettaglio |",
                 "| --- | --- | --- | --- | --- | --- |"]
        count = 0

        def cell(value: str) -> str:
            return escape_md(value).replace("|", "\\|").replace("\n", " ")

        for spec in self.specs:
            if spec.kind != "video" and not (is_video_filename(spec.label) or is_audio_filename(spec.label)):
                continue
            owner = self.items.get(spec.owner)
            activity = (f"[{cell(owner.title)}]({md_url(owner.md_path)})" if owner and owner.md_path
                        else cell(owner.title if owner else self.title))
            status = ("Scaricato" if spec.local_ok else "Download fallito" if spec.status == "error"
                      else "Escluso" if spec.status == "skipped" else "Non scaricato")
            local = f"[Apri file]({md_url(spec.rel_path)})" if spec.local_ok else "—"
            lines.append(f"| {activity} | {cell(spec.label)} | {status} | {local} | "
                         f"[Originale]({md_url(spec.url)}) | {cell(spec.message)} |")
            count += 1
        for item in [self.root_item, *self._walk()]:
            for reason, url in item.missing:
                if self._missing_kind(item, reason) != "video":
                    continue
                if any(spec.url == url and spec.kind == "video" for spec in self.specs):
                    continue
                note_path = item.md_path if item.kind == "activity" else "course_overview.md"
                activity = f"[{cell(item.title)}]({md_url(note_path)})"
                lines.append(f"| {activity} | Video/audio online | Solo link | — | "
                             f"[Originale]({md_url(url)}) | {cell(reason)} |")
                count += 1
        if not count:
            lines += ["", "Nessun video/audio rilevato nei dati elaborati. "
                      "Questo non esclude la presenza di player o contenuti non esposti dalle API."]
        return "\n".join(lines).rstrip() + "\n"

    _TIME_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s?(?:h|Std\.?|Stunden?|min|Minuten|ore|ora)(?![\w])", re.I)

    def _verification_md(self) -> str:
        rows = []

        def visit(sec: Item, depth: int) -> None:
            acts = [c for c in sec.children if c.kind == "activity"]
            labels = [c for c in sec.children if c.kind == "label"]
            owned = {sec.key, *(c.key for c in acts), *(c.key for c in labels)}
            specs = [sp for sp in self.specs if sp.owner in owned]
            notes = sum(1 for a in acts if a.md_path in self.texts_now)
            missing = sum(1 for c in [sec, *acts, *labels] if c.missing)
            restricted = sum(1 for c in [*acts, *labels] if c.restricted)
            name = ("↳ " * depth) + sec.title
            rows.append(f"| {escape_md(name).replace('|', '/')} | {len(acts)} | {len(labels)} | {notes} | "
                        f"{sum(1 for sp in specs if sp.local_ok)} / {len(specs)} | {missing} | {restricted} |")
            for child in sec.children:
                if child.kind == "section":
                    visit(child, depth + 1)

        for sec in self.root_item.children:
            visit(sec, 0)
        texts = [(it, f"{it.description}\n{it.body}") for it in self._walk() if it.kind != "course"]
        times = [(it, m.group(0)) for it, t in texts for m in [self._TIME_RE.search(t)] if m]
        goals = [it for it, t in texts if re.search(r"lernziel|lernergebnis|nach diesem thema|obiettiv",
                                                 f"{it.title}\n{t}", re.I)]
        total_acts = sum(1 for it in self._walk() if it.kind == "activity")
        lines = [f"# Verifica dell'esportazione — {escape_md(self.title)}", "",
                 f"Confronta questa tabella con il corso in Moodle ([apri il corso]({md_url(self.course_url)})). "
                 "I numeri contano solo ciò che Moodle mostra al tuo account.", "",
                 "| Sezione | Attività | Testi (etichette) | Note create | File salvati / previsti | "
                 "Con elementi non esportati | Accesso limitato |",
                 "| --- | --- | --- | --- | --- | --- | --- |", *rows, "",
                 "## Indizi trovati automaticamente", "",
                 f"- Attività in totale: {total_acts}",
                 f"- Testi con obiettivi di apprendimento (Lernziele): {len(goals)}"
                 + (" — es. " + ", ".join(escape_md(g.title) for g in goals[:4]) if goals else ""),
                 f"- Testi con tempi di studio (es. «3 h»): {len(times)}"
                 + (" — es. " + ", ".join(f"«{escape_md(t)}» in {escape_md(it.title)}" for it, t in times[:4])
                    if times else ""),
                 f"- File aggiunti da te: {sum(len(it.manual_files) for it in self._walk())}", "",
                 "## Controlli consigliati (spunta mentre confronti con Moodle)", "",
                 "- [ ] Titoli e ordine delle sezioni uguali a Moodle",
                 "- [ ] Sottosezioni (es. LERNZIELE, VORBEREITUNG, TRANSFER) annidate nella sezione giusta",
                 "- [ ] Obiettivi di apprendimento e tempi di studio presenti in `course_fulltext.md`",
                 "- [ ] Numero di attività per sezione uguale a quello visibile in Moodle",
                 "- [ ] Due o tre documenti (PDF, Word, PowerPoint) si aprono correttamente",
                 "- [ ] Ogni voce di `elementi_mancanti.md` ha un motivo plausibile", ""]
        return "\n".join(lines)

    def _structure_json(self) -> str:
        def node(it: Item) -> dict:
            d = {"key": it.key, "type": "subsection" if (it.kind == "section" and it.module) else
                 (it.kind if it.kind != "activity" else it.modname), "title": it.title, "moodle_url": it.url,
                 "md": it.md_path if it.kind != "label" else None}
            if it.kind == "label":
                d["text"] = it.description
            if it.restricted:
                d["restricted"] = it.restricted
            if it.facts:
                d["facts"] = {k: v for k, v in it.facts}
            if it.kind in ("activity", "section") and it.description:
                d["description_text"] = first_line(it.description, 500)
            files = [s for s in self.specs if s.owner == it.key]
            if files:
                d["files"] = [{"name": s.label, "path": s.rel_path, "size": s.size, "kind": s.kind,
                               "moodle_modified": fmt_dt(s.mtime), "status": s.status} for s in files]
            if it.links:
                d["links"] = [{"title": t, "url": u, "provider": p} for t, u, p in it.links]
            if it.missing:
                d["not_exported"] = [{"reason": r, "url": u} for r, u in it.missing]
            if it.manual_files:
                d["manual_files"] = it.manual_files
            if it.children:
                d["children"] = [node(c) for c in it.children]
            return d

        data = {"generator": f"FFHS Moodle Archiver {__version__}", "exported_at": fmt_dt(self.now),
                "course": {"id": self.cid, "fullname": self.title, "shortname": self.shortname,
                           "moodle_url": self.course_url, "folder": self.folder,
                           "startdate": fmt_d(self.details.get("startdate") or self.course.get("startdate")),
                           "enddate": fmt_d(self.details.get("enddate") or self.course.get("enddate")),
                           "teachers": [c.get("fullname") for c in self.details.get("contacts") or []]},
                "sections": [node(s) for s in self.root_item.children]}
        return json.dumps(data, ensure_ascii=False, indent=1) + "\n"

    def _write_all(self) -> None:
        for item in self._walk():
            self.client.check_cancel()
            if item.kind == "section":
                self._write_text(item.md_path, self._section_md(item), item.key)
            elif item.kind == "activity":
                self._write_text(item.md_path, self._activity_md(item), item.key)
        self._write_text("course_overview.md", self._overview_md(), "course", version=False)
        self._write_text("course_fulltext.md", self._fulltext_md(), "course", version=False)
        self._write_text("course_info.md", self._info_md(), "course", version=False)
        self._write_text("elementi_mancanti.md", self._missing_md(), "course", version=False)
        self._write_text("video_e_media.md", self._media_inventory_md(), "course", version=False)
        self._write_text("verifica_corso.md", self._verification_md(), "course", version=False)
        self._write_text("course_structure.json", self._structure_json(), "course", version=False)
        raw_dir = self.root / STATE_DIR / "raw"
        if self.opts.save_raw_json:
            for name, data in self.raw.items():
                fsutil.write_text_atomic(raw_dir / f"{name}.json",
                                         scrub_secrets(json.dumps(data, ensure_ascii=False, indent=1)))
        elif fsutil.exists(raw_dir):  # option switched off: remove copies made earlier
            for old in list(raw_dir.glob("*.json")):
                fsutil.remove(old)
            fsutil.remove_empty_dirs(self.root, raw_dir)

    def _track_items(self) -> None:
        items_state = self.state.items
        current = {k: it for k, it in self.items.items() if it.kind in ("activity", "section", "label")}
        first_run = not items_state
        for key, it in current.items():
            entry = items_state.get(key)
            if entry is None:
                items_state[key] = {"title": it.title, "type": it.modname or it.kind, "md": it.md_path,
                                    "first_seen": self.now, "last_seen": self.now}
                if not first_run and it.kind != "label":
                    self.new_items.append(it.title)
            else:
                entry.update({"title": it.title, "md": it.md_path, "last_seen": self.now, "removed": None})
        for key, entry in items_state.items():
            if key in current or entry.get("removed"):
                continue
            entry["removed"] = self.now
            self.removed_items.append(entry.get("title", key))
        self.state.touch()

    def _annotate_removed(self) -> None:
        """Mark kept notes of elements that disappeared from Moodle (once)."""
        for key, entry in self.state.items.items():
            if not entry.get("removed") or entry.get("type") == "label":
                continue
            md = entry.get("md")
            if md and md not in self.texts_now and fsutil.is_file(self.root / md):
                text = fsutil.read_text(self.root / md)
                note = (f"> **Non più disponibile in Moodle** (rilevato il {fmt_d(entry['removed'])}). "
                        "Questa è la copia conservata nell'archivio.\n\n")
                if "Non più disponibile in Moodle" not in text:
                    if text.startswith("---"):
                        end = text.find("\n---", 3)
                        head, rest = (text[: end + 4], text[end + 4:].lstrip("\n")) if end > 0 else ("", text)
                        text = f"{head}\n\n{note}{rest}" if head else note + text
                    else:
                        text = note + text
                    fsutil.write_text_atomic(self.root / md, text)
                    self.state.texts[md] = {"sha": fsutil.sha256_bytes(text.encode("utf-8")), "owner": key}
        self.state.touch()

    def _cleanup_stale_texts(self) -> None:
        for rel, info in list(self.state.texts.items()):
            if rel in self.texts_now:
                continue
            owner = info.get("owner", "")
            owner_alive = owner == "course" or owner in self.items
            if not owner_alive:
                continue  # the element disappeared from Moodle: keep its notes
            path = self.root / rel
            if fsutil.is_file(path) and fsutil.sha256_file(path) == info.get("sha"):
                fsutil.remove(path)
                fsutil.remove_empty_dirs(self.root, path.parent)
            del self.state.texts[rel]

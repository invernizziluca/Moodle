"""Command line interface (tests, automation, diagnostics). Without arguments the GUI starts."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path

from . import APP_NAME, __version__
from .appdata import Settings, app_dir
from .exporter import ExportOptions, Exporter
from .moodle import MoodleError, RedactingFilter, TokenInvalidError
from .session import Session
from .sso import SSOError, handle_callback


def _client_or_exit(session: Session):
    creds = session.saved_credentials()
    if not creds:
        print("Non connesso: esegui prima  login", file=sys.stderr)
        raise SystemExit(2)
    return session.client_for(creds), creds


def cmd_login(args, settings: Settings) -> int:
    """Login by copy & paste (the command line never touches the Windows registry)."""
    session = Session()
    keep = bool(getattr(args, "videos", False))
    if args.paste:
        creds, _ = session.finish_login(args.paste, require_signature=False, keep_private_token=keep)
    else:
        attempt = session.start_login(method="other" if args.other else "ffhs-id",
                                      open_browser=not args.no_browser, use_handler=False)
        print("Apri questo indirizzo nel browser e accedi:\n" + attempt.url)
        print("\nPoi incolla qui il link «moodlemobile://token=…» della pagina finale e premi Invio:")
        text = sys.stdin.readline().strip()
        creds, _ = session.finish_login(text, require_signature=False, keep_private_token=keep)
    print(f"Connesso come {creds.fullname} ({creds.sitename}, Moodle {creds.release}). "
          f"Token salvato ({creds.protection}).")
    return 0


def cmd_verify(args, settings: Settings) -> int:
    from .verify import access_report

    session = Session()
    creds = session.saved_credentials()
    client = session.client_for(creds) if creds else None
    report = access_report(app_dir(), client, creds)
    print(report)
    return 1 if "problemi da risolvere" in report else 0


def cmd_preview(args, settings: Settings) -> int:
    from .verify import course_preview

    client, _ = _client_or_exit(Session())
    courses = Exporter(client, ExportOptions(dest=settings.archive_dir)).list_courses()
    course = next((c for c in courses if int(c["id"]) == args.course), None)
    if course is None:
        print("Corso non trovato tra i tuoi corsi: usa  courses  per vedere gli ID.", file=sys.stderr)
        return 2
    print(course_preview(client, course))
    return 0


def cmd_courses(args, settings: Settings) -> int:
    client, _ = _client_or_exit(Session())
    exporter = Exporter(client, ExportOptions(dest=settings.archive_dir))
    for c in exporter.list_courses():
        print(f"{c['id']:>7}  {c.get('shortname', ''):<28}  {c.get('fullname', '')}")
    return 0


def cmd_export(args, settings: Settings) -> int:
    client, creds = _client_or_exit(Session())
    want_videos = bool(getattr(args, "videos", False))
    want_capture = bool(getattr(args, "capture_pages", False)) and not bool(getattr(args, "no_capture", False))
    need_token = want_videos or want_capture
    opts = ExportOptions(dest=args.dest or settings.archive_dir, include_forums=not args.no_forums,
                         include_quiz_reviews=not args.no_quiz, include_submissions=not args.no_submissions,
                         include_videos=not args.no_videos, download_videos=want_videos,
                         capture_pages=want_capture,
                         private_token=(creds.privatetoken if need_token else ""), max_file_mb=args.max_mb,
                         keep_versions=not args.no_versions, save_raw_json=args.raw,
                         anonymize_others=not args.show_names,
                         obsidian_ready=not getattr(args, "no_obsidian", False))
    cancel = threading.Event()

    def emit(event: dict) -> None:
        t = event["type"]
        if t == "log":
            print(f"[{event['level']}] {event['message']}", flush=True)
        elif t == "phase":
            print(f"  … {event['message']}", flush=True)
        elif t == "progress" and event.get("scope") == "files" and args.verbose:
            print(f"    {event['done']}/{event['total']} {event['message']}", flush=True)

    exporter = Exporter(client, opts, emit=emit, cancel_event=cancel)
    courses = exporter.list_courses()
    if not args.all:
        wanted = set(args.course or [])
        courses = [c for c in courses if int(c["id"]) in wanted]
        if not courses:
            print("Nessun corso corrispondente: usa  courses  per vedere gli ID.", file=sys.stderr)
            return 2
    if args.stop_after_files:
        count = {"n": 0}
        original = client.download

        def limited(*a, **kw):
            count["n"] += 1
            if count["n"] > args.stop_after_files:
                cancel.set()
            return original(*a, **kw)

        client.download = limited
    summary = exporter.export(courses)
    print(json.dumps({"stopped": summary["stopped"], "report": summary["report"],
                      "results": [{k: r.get(k) for k in ("id", "name", "status", "counts", "missing")}
                                  for r in summary["results"]]}, ensure_ascii=False, indent=1))
    return 0 if not summary["stopped"] else 1


def cmd_sync(args, settings: Settings) -> int:
    """Export the courses selected in the GUI, with the GUI options (for scheduled runs)."""
    if not settings.selected_courses:
        print("Nessun corso selezionato nell'interfaccia: apri il programma e spunta i corsi.", file=sys.stderr)
        return 2
    args.all = False
    args.course = [int(c) for c in settings.selected_courses]
    args.dest = settings.archive_dir
    args.no_forums = not settings.include_forums
    args.no_quiz = not settings.include_quiz_reviews
    args.no_submissions = not settings.include_submissions
    args.no_videos = not settings.include_videos
    args.videos = settings.download_videos
    args.no_capture = not settings.capture_pages
    args.capture_pages = settings.capture_pages
    args.no_versions = not settings.keep_versions
    args.raw = settings.save_raw_json
    args.show_names = not settings.anonymize_others
    args.max_mb = settings.max_file_mb
    args.verbose = False
    args.stop_after_files = 0
    return cmd_export(args, settings)


def cmd_diagnose(args, settings: Settings) -> int:
    client, _ = _client_or_exit(Session())
    data = Exporter(client, ExportOptions(dest=settings.archive_dir)).diagnose(args.course)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Diagnostica salvata in {args.out}")
    else:
        print(text)
    return 0


def cmd_logout(args, settings: Settings) -> int:
    Session().logout()
    print("Disconnesso: token rimosso da questo computer. Per revocarlo anche in Moodle: "
          "Profil → Einstellungen → Sicherheitsschlüssel → zurücksetzen.")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--sso-callback" in argv:
        # Started by the browser at the end of the login: strict, minimal handling, no GUI.
        return handle_callback(argv)
    parser = argparse.ArgumentParser(prog="ffhs_archiver", description=f"{APP_NAME} {__version__}")
    parser.add_argument("--debug", action="store_true", help="log dettagliato")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("login", help="accesso tramite browser (FFHS-ID), con link incollato")
    p.add_argument("--paste", help="link moodlemobile://token=… o chiave")
    p.add_argument("--other", action="store_true", help="pagina di login normale invece di FFHS-ID diretto")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--videos", action="store_true",
                   help="conserva il token privato per scaricare i video delle lezioni e catturare le pagine "
                        "del corso (Modulplan)")
    sub.add_parser("verify", help="verifica accesso e sistema, senza scaricare nulla")
    p = sub.add_parser("preview", help="anteprima della struttura di un corso, senza scaricare nulla")
    p.add_argument("--course", type=int, required=True)
    sub.add_parser("courses", help="elenca i corsi")
    p = sub.add_parser("export", help="esporta corsi")
    p.add_argument("--course", type=int, action="append")
    p.add_argument("--all", action="store_true")
    p.add_argument("--dest")
    p.add_argument("--no-forums", action="store_true")
    p.add_argument("--no-quiz", action="store_true")
    p.add_argument("--no-submissions", action="store_true")
    p.add_argument("--no-videos", action="store_true", help="non scaricare i file video caricati in Moodle")
    p.add_argument("--no-capture", action="store_true",
                   help="non catturare le pagine che richiedono login (es. Modulplan)")
    p.add_argument("--capture-pages", action="store_true",
                   help="cattura avanzata delle pagine tramite auto-login, disattivata normalmente")
    p.add_argument("--videos", action="store_true",
                   help="scarica anche i video in streaming delle lezioni FFHS (Kaltura/SWITCHcast), uso personale")
    p.add_argument("--no-versions", action="store_true")
    p.add_argument("--no-obsidian", action="store_true", help="non creare la configurazione .obsidian")
    p.add_argument("--raw", action="store_true", help="salva anche i dati tecnici grezzi (diagnosi)")
    p.add_argument("--show-names", action="store_true", help="non nascondere i nomi degli altri studenti")
    p.add_argument("--max-mb", type=int, default=0)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--stop-after-files", type=int, default=0, help=argparse.SUPPRESS)
    p = sub.add_parser("diagnose", help="struttura di un corso senza testi (per assistenza)")
    p.add_argument("--course", type=int, required=True)
    p.add_argument("--out")
    sub.add_parser("sync", help="esporta i corsi selezionati nell'interfaccia (per esecuzioni pianificate)")
    sub.add_parser("logout")
    sub.add_parser("gui")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter())
    if args.cmd in (None, "gui"):
        from .gui import run_gui

        return run_gui()
    settings = Settings.load()
    commands = {"login": cmd_login, "verify": cmd_verify, "preview": cmd_preview, "courses": cmd_courses,
                "export": cmd_export, "diagnose": cmd_diagnose, "sync": cmd_sync, "logout": cmd_logout}
    try:
        return commands[args.cmd](args, settings)
    except (SSOError, TokenInvalidError, MoodleError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1

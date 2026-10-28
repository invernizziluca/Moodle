"""Import files produced by the real Moodle Buddy and FetchV extensions.

The extensions themselves perform downloads in the user's browser. This module
only copies chosen local files, expands folder ZIPs, and records their context.
It never controls an extension, reads a browser profile, or receives credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from .browser_import import (_archive_root, _inside, _is_link, _prepare_activity_folder,
                             _read_json, _regular_source, list_activities)
from .html2md import escape_md, is_audio_filename, is_video_filename
from .naming import UniqueNames, rel_link, safe_filename, slug

LEDGER = ".archiver/moodle_buddy_imports.json"
MAX_RELATIVE_PATH = 150


def is_fetchv_filename(name: str) -> bool:
    """Final video/audio formats offered by the FetchV collection workflow."""
    return is_video_filename(name) or is_audio_filename(name)


def _relative_parts(value: str) -> tuple[str, ...]:
    if not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("Nome o percorso non valido nel download Moodle Buddy")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("..", ".") for part in path.parts):
        raise ValueError("Il download contiene un percorso esterno alla cartella")
    if not path.parts or len(path.parts) > 40:
        raise ValueError("Il download contiene un percorso troppo profondo")
    return path.parts


def _directory(root: Path, relative: str) -> tuple[Path, list[Path]]:
    current, created = root, []
    for part in _relative_parts(relative):
        current = current / part
        if _is_link(current):
            raise ValueError("I collegamenti simbolici non sono ammessi nell'archivio")
        if not current.exists():
            current.mkdir()
            created.append(current)
        elif not current.is_dir():
            raise ValueError("Una cartella dell'archivio è occupata da un file")
    return current, created


@contextmanager
def _source(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Il download selezionato non è un file normale")
        yield handle
        after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Un download sta ancora cambiando: attendi che sia terminato")


def _course_data(root: Path, course_id: int):
    rows = _read_json(root, "courses.json").get("courses", [])
    selected = [row for row in rows if str(row.get("id")) == str(course_id)]
    if len(selected) != 1:
        raise ValueError("Esporta prima il corso da usare come destinazione")
    course = _inside(root, selected[0].get("folder", ""))
    activities = {a["key"]: a for a in list_activities(root, course_id)}
    structure = _read_json(course, "course_structure.json")
    matches = {}

    def visit(nodes):
        for node in nodes:
            key = node.get("key")
            if key in activities:
                for entry in node.get("files") or []:
                    name = entry.get("name")
                    if isinstance(name, str) and name:
                        matches.setdefault(name, set()).add(key)
            visit(node.get("children") or [])
    visit(structure.get("sections") or [])
    return course, activities, matches


class _Paths:
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.names = {}
        self.parents = {}

    def take(self, original: str) -> Path:
        parts = _relative_parts(original)
        parent = ""
        for i, part in enumerate(parts[:-1]):
            key = parts[:i + 1]
            if key not in self.parents:
                name = self.names.setdefault(parent, UniqueNames()).take(slug(part, 22))
                self.parents[key] = "/".join(p for p in (parent, name) if p)
            parent = self.parents[key]
        name = safe_filename(parts[-1], 56)
        # Keep full source context in the report; flatten only the local path if
        # its combined depth would exceed a conservative Windows budget.
        if len(f"{self.prefix}/{parent}/{name}") > MAX_RELATIVE_PATH - 8:
            parent = "p_" + hashlib.sha256("/".join(parts[:-1]).encode()).hexdigest()[:12]
        budget = MAX_RELATIVE_PATH - len(self.prefix) - len(parent) - 10
        if budget < 12:
            raise ValueError("Il percorso del corso è troppo lungo: scegli una cartella archivio più breve")
        name = safe_filename(name, min(56, budget))
        if len(name.encode("utf-16-le")) // 2 > min(56, budget):
            suffix = Path(name).suffix
            name = slug(Path(name).stem, max(8, min(40, budget - len(suffix)))) + suffix
        name = self.names.setdefault(parent, UniqueNames()).take(name)
        return Path(*PurePosixPath(parent).parts) / name if parent else Path(name)


def import_buddy_downloads(sources, archive_root, course_id, download_root=None, *,
                          activity_key=None,
                          max_entries: int = 10000, max_bytes: int = 20 * 1024 ** 3) -> dict:
    """Copy selected completed downloads without overwrites or inferred matches.

    File association requires one unique activity with exactly the original
    filename in its exported API metadata. Every other file is "Da associare".
    An explicit ``activity_key`` assigns every selected file to that activity
    based on the user's choice, rather than inferring an association.
    Folder ZIPs are expanded, but the source ZIP and files remain untouched.
    Limits cover every expanded member, including duplicates, before publication.
    """
    return _import_downloads(sources, archive_root, course_id, download_root,
                             max_entries=max_entries, max_bytes=max_bytes,
                             activity_key=activity_key)


def import_fetchv_downloads(sources, archive_root, course_id, activity_key, download_root=None, *,
                           max_entries: int = 10000, max_bytes: int = 20 * 1024 ** 3) -> dict:
    """Copy final FetchV video files into the activity explicitly chosen by the user.

    No title/filename matching is inferred. ZIPs and individual streaming
    segments are not accepted as completed videos. Sources are preserved.
    """
    return _import_downloads(sources, archive_root, course_id, download_root,
                             max_entries=max_entries, max_bytes=max_bytes,
                             tool="fetchv", activity_key=activity_key)


def _import_downloads(sources, archive_root, course_id, download_root=None, *,
                      max_entries: int, max_bytes: int,
                      tool: str = "moodle_buddy", activity_key=None) -> dict:
    if max_entries < 1 or max_bytes < 1:
        raise ValueError("I limiti di importazione devono essere positivi")
    root = _archive_root(archive_root)
    course, activities, exact = _course_data(root, course_id)
    source_paths = sorted({_regular_source(source) for source in sources})
    if not source_paths:
        raise ValueError("Seleziona almeno un download completato")
    if activity_key is not None and activity_key not in activities:
        raise ValueError("Scegli un'attività esportata come destinazione dei file selezionati")
    if tool == "fetchv":
        if activity_key not in activities:
            raise ValueError("Scegli un'attività esportata come destinazione del video FetchV")
        if any(not is_fetchv_filename(source.name) for source in source_paths):
            raise ValueError("Seleziona i video finali di FetchV (MP4, WebM, …), non ZIP o segmenti streaming")
    download = _archive_root(download_root) if download_root is not None else None
    if any(path.is_relative_to(root) for path in source_paths):
        raise ValueError("Seleziona download esterni all'archivio")
    if download is not None and any(not path.is_relative_to(download) for path in source_paths):
        raise ValueError("Un file selezionato è esterno alla cartella download")
    ledger_name = ".archiver/fetchv_imports.json" if tool == "fetchv" else LEDGER
    label = "FetchV" if tool == "fetchv" else "Moodle Buddy"
    report_name = "video_fetchv.md" if tool == "fetchv" else "materiali_moodle_buddy.md"
    ledger_path = course / ledger_name
    if _is_link(ledger_path) or _is_link(ledger_path.parent):
        raise ValueError("Il registro di importazione non può essere un collegamento simbolico")
    if ledger_path.exists():
        ledger = _read_json(course, ledger_name)
        if ledger.get("version") != 1 or not isinstance(ledger.get("files"), dict):
            raise ValueError("Registro importazioni non valido: non è stato sovrascritto")
    else:
        ledger = {"version": 1, "tool": label, "files": {}, "reports": {}}
    created_dirs, created_notes = [], []
    published = None
    try:
        imports_folder = (activities[activity_key]["directory"].relative_to(course).as_posix() + "/fetchv"
                          if tool == "fetchv" else "materiali_moodle_buddy")
        imports, made = _directory(course, imports_folder)
        created_dirs.extend(made)
        _, made = _directory(course, ".archiver")
        created_dirs.extend(made)
        number = 1
        while (imports / f"b{number:06d}").exists():
            number += 1
        batch = imports / f"b{number:06d}"
        prefix = batch.relative_to(root).as_posix()
        mapper = _Paths(prefix)
        with tempfile.TemporaryDirectory(prefix=".buddy-", dir=course) as temp:
            staging = Path(temp)
            payload = staging / "batch"
            payload.mkdir()
            rows, new_digests = [], set()
            total, count = 0, 0

            def copy(stream, original, declared_size=None):
                nonlocal total, count
                parts = _relative_parts(original)
                count += 1
                if count > max_entries or declared_size is not None and total + declared_size > max_bytes:
                    raise ValueError("Il download supera i limiti di importazione")
                temporary = staging / f"file_{count}"
                digest = hashlib.sha256()
                with temporary.open("xb") as output:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("Il download supera il limite di dimensione")
                        digest.update(chunk)
                        output.write(chunk)
                sha = digest.hexdigest()
                record_key = f"{activity_key}|{sha}" if tool == "fetchv" else sha
                previous = ledger["files"].get(record_key)
                target = None
                if previous:
                    try:
                        target = _inside(course, previous["path"])
                        if not target.is_file() or _hash_file(target) != sha:
                            target = None
                    except (OSError, ValueError, KeyError):
                        target = None
                if record_key in new_digests:
                    target = course / ledger["files"][record_key]["path"]
                if target is None:
                    rel = mapper.take(original)
                    target = batch / rel
                    destination = payload / rel
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(temporary, destination)
                    ledger["files"][record_key] = {"path": target.relative_to(course).as_posix(),
                                                  "sha256": sha, "origins": []}
                    new_digests.add(record_key)
                else:
                    temporary.unlink()
                previous = ledger["files"][record_key]
                if original not in previous["origins"]:
                    previous["origins"].append(original)
                owners = exact.get(parts[-1], set())
                saved_key = previous.get("associations", {}).get(original)
                key = (activity_key if activity_key is not None else saved_key if saved_key in activities
                       else next(iter(owners)) if len(owners) == 1 else None)
                previous.setdefault("associations", {})[original] = key
                rows.append({"path": target, "original": original, "sha256": sha,
                             "activity_key": key, "activity_title": activities[key]["title"] if key else ""})

            for source in source_paths:
                relative = source.relative_to(download).as_posix() if download else source.name
                if source.suffix.lower() != ".zip":
                    with _source(source) as handle:
                        copy(handle, relative, source.stat().st_size)
                    continue
                with _source(source) as handle, ZipFile(handle) as archive:
                    members = archive.infolist()
                    if len(members) > max_entries:
                        raise ValueError("Lo ZIP contiene troppi elementi")
                    for entry in members:
                        mode = (entry.external_attr >> 16) & 0o170000
                        if mode == stat.S_IFLNK or mode not in (0, stat.S_IFDIR, stat.S_IFREG):
                            raise ValueError("Lo ZIP contiene collegamenti o file speciali")
                        _relative_parts(entry.filename.rstrip("/"))
                        if entry.is_dir():
                            continue
                        if entry.flag_bits & 1:
                            raise ValueError("Lo ZIP è cifrato: estrailo prima con il browser o il sistema")
                        original = str(PurePosixPath(relative).with_suffix("")) + "/" + entry.filename
                        with archive.open(entry) as stream:
                            copy(stream, original, entry.file_size)
            if not rows:
                raise ValueError("Nessun file trovato nei download selezionati")
            fingerprint = hashlib.sha256(json.dumps(sorted(
                (r["original"], r["sha256"], r["activity_key"] or "",
                 str(activities[r["activity_key"]]["md"]) if r["activity_key"] else "") for r in rows)).encode()).hexdigest()
            old_report = ledger.get("reports", {}).get(fingerprint)
            if not new_digests and old_report:
                try:
                    report = _inside(course, old_report)
                    if report.is_file():
                        return _result(rows, report, 0)
                except (OSError, ValueError):
                    pass
            report = batch / report_name
            association = ("L'attività di destinazione è stata scelta esplicitamente dall'utente; "
                           "il nome del video non è stato usato per indovinarla."
                           if activity_key is not None else
                           "L'associazione automatica usa soltanto nomi di file esatti con una singola attività candidata.")
            lines = ["---", f"title: Materiali importati da {label}", "type: manual", "---", "",
                     f"# Materiali importati da {label}", "",
                     "Copia di download locali scelti dall'utente. Gli originali sono stati conservati.",
                     association + " La copia non certifica la completezza del corso.", ""]
            matched_title = ("Attività scelta dall'utente" if tool == "fetchv" else
                             "Associati dall'utente" if activity_key is not None else "Associati per nome esatto")
            for matched, title in ((True, matched_title),
                                   (False, "Da associare")):
                selected = [r for r in rows if bool(r["activity_key"]) is matched]
                if not selected:
                    continue
                lines += ["## " + title, "", "| Percorso originale | Copia locale | Attività |",
                          "| --- | --- | --- |"]
                for row in selected:
                    activity = activities.get(row["activity_key"])
                    link = (f"[{_cell(activity['title'])}]({rel_link(report.relative_to(course).as_posix(), activity['md'].relative_to(course).as_posix())})"
                            if activity else "Da associare: nessuna corrispondenza unica")
                    file_link = rel_link(report.relative_to(course).as_posix(), row["path"].relative_to(course).as_posix())
                    lines.append(f"| {_cell(row['original'])} | [Apri file]({file_link}) | {link} |")
                lines.append("")
            (payload / report.name).write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.rename(payload, batch)
            published = batch
            for key, activity in activities.items():
                selected = [r for r in rows if r["activity_key"] == key]
                if not selected:
                    continue
                if _prepare_activity_folder(activity):
                    created_dirs.append(activity["directory"])
                names = UniqueNames()
                for file in activity["directory"].iterdir():
                    names.reserve(file.name)
                note = activity["directory"] / names.take(report_name)
                relative_note = note.relative_to(course).as_posix()
                content = ["---", f"title: Materiali {label}", "type: manual", "---", "",
                           f"# Materiali {label}", "", association,
                           "Verifica il contenuto prima di considerarlo completo.", ""]
                content += [f"- [{escape_md(PurePosixPath(r['original']).name)}]({rel_link(relative_note, r['path'].relative_to(course).as_posix())})" for r in selected]
                with note.open("x", encoding="utf-8") as handle:
                    created_notes.append(note)
                    handle.write("\n".join(content) + "\n")
            ledger.setdefault("reports", {})[fingerprint] = report.relative_to(course).as_posix()
            staged_ledger = staging / "ledger.json"
            staged_ledger.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(staged_ledger, ledger_path)
            return _result(rows, report, len(new_digests))
    except BaseException:
        for note in created_notes:
            note.unlink(missing_ok=True)
        if published is not None:
            shutil.rmtree(published)
        raise
    finally:
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass


def _cell(text):
    return escape_md(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result(rows, report, new_count):
    files = list(dict.fromkeys(row["path"] for row in rows))
    return {"files": files, "report_path": report, "note": report,
            "matched": [row for row in rows if row["activity_key"]],
            "unmatched": [row for row in rows if not row["activity_key"]],
            "matched_files": len({row["path"] for row in rows if row["activity_key"]}),
            "unmatched_files": len({row["path"] for row in rows if not row["activity_key"]}),
            "new_files": new_count, "reused_files": len(files) - new_count}


def _buddy_records(course, activities, exact, ledger):
    """Read persisted origin associations, including ledgers from version 0.5."""
    if ledger.get("version") != 1 or not isinstance(ledger.get("files"), dict):
        raise ValueError("Registro importazioni non valido: non è stato sovrascritto")
    records = []
    for sha, entry in ledger["files"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("origins"), list):
            raise ValueError("Registro importazioni non valido")
        path = _inside(course, entry.get("path", ""))
        if not path.is_file():
            raise ValueError("Una copia importata non è più disponibile nell'archivio")
        associations = entry.get("associations", {})
        if not isinstance(associations, dict):
            raise ValueError("Associazioni nel registro non valide")
        for original in dict.fromkeys(entry["origins"]):
            parts = _relative_parts(original)
            owners = exact.get(parts[-1], set())
            key = associations.get(original)
            if original not in associations:
                key = next(iter(owners)) if len(owners) == 1 else None
            if key not in activities:
                key = None
            item_id = hashlib.sha256((sha + "\0" + original).encode("utf-8")).hexdigest()
            records.append({"id": item_id, "path": path, "original": original,
                            "name": parts[-1], "sha256": sha, "activity_key": key})
    return records


def list_unmatched_buddy_files(archive_root, course_id) -> list[dict]:
    """List already archived origins awaiting an activity; no downloads required.

    The stable id identifies both content and original context. Multiple origins
    can share one deduplicated local file and be assigned independently.
    """
    root = _archive_root(archive_root)
    course, activities, exact = _course_data(root, course_id)
    if not (course / LEDGER).exists():
        return []
    records = _buddy_records(course, activities, exact, _read_json(course, LEDGER))
    return [record for record in records if not record["activity_key"]]


def assign_buddy_files(archive_root, course_id, item_ids, activity_key) -> dict:
    """Link already imported Moodle Buddy files to a chosen activity in place.

    Original downloads and local payloads are never copied, renamed or removed.
    New notes/report are published before the atomic ledger commit and rolled
    back if it fails. Existing reports remain historical snapshots.
    """
    root = _archive_root(archive_root)
    course, activities, exact = _course_data(root, course_id)
    if activity_key not in activities:
        raise ValueError("Scegli un'attività del corso come destinazione")
    ledger = _read_json(course, LEDGER)
    records = _buddy_records(course, activities, exact, ledger)
    ids = set(item_ids)
    if not ids:
        raise ValueError("Seleziona almeno un file da associare")
    available = {record["id"]: record for record in records}
    if not ids.issubset(available):
        raise ValueError("L'elenco dei file è cambiato: aggiornalo prima di associare")
    selected = [available[item_id] for item_id in sorted(ids)]
    # Verify selected archived bytes, rather than trusting stale preview data.
    hashes = {}
    for record in selected:
        path = record["path"]
        if path not in hashes:
            hashes[path] = _hash_file(path)
        if hashes[path] != record["sha256"]:
            raise ValueError("Una copia importata è stata modificata: non è stata associata")
    changed = [record for record in selected if record["activity_key"] != activity_key]
    for record in selected:
        record["activity_key"] = activity_key
        ledger["files"][record["sha256"]].setdefault("associations", {})[record["original"]] = activity_key
    fingerprint = hashlib.sha256(json.dumps(sorted(
        (record["id"], record["activity_key"] or "") for record in records)).encode()).hexdigest()
    report_key = "associations:" + fingerprint
    previous_report = ledger.get("reports", {}).get(report_key)
    if not changed and previous_report:
        report = _inside(course, previous_report)
        if report.is_file():
            return {"assigned": 0, "remaining": sum(not r["activity_key"] for r in records),
                    "report_path": report, "note": report,
                    "files": list(dict.fromkeys(r["path"] for r in selected))}

    created_dirs, created_files = [], []
    try:
        folder, made = _directory(course, "materiali_moodle_buddy")
        created_dirs.extend(made)
        names = UniqueNames()
        for file in folder.iterdir():
            names.reserve(file.name)
        report = folder / names.take("associazioni_moodle_buddy.md")
        report_relative = report.relative_to(course).as_posix()
        lines = ["---", "title: Associazioni Moodle Buddy", "type: manual", "---", "",
                 "# Associazioni Moodle Buddy", "",
                 "Stato degli abbinamenti al momento della conferma. Le copie locali e gli originali sono conservati.", "",
                 "| Percorso originale | Copia locale | Attività |", "| --- | --- | --- |"]
        for record in records:
            activity = activities.get(record["activity_key"])
            activity_link = (f"[{_cell(activity['title'])}]({rel_link(report_relative, activity['md'].relative_to(course).as_posix())})"
                             if activity else "Da associare")
            file_link = rel_link(report_relative, record["path"].relative_to(course).as_posix())
            lines.append(f"| {_cell(record['original'])} | [Apri file]({file_link}) | {activity_link} |")
        with report.open("x", encoding="utf-8") as handle:
            created_files.append(report)
            handle.write("\n".join(lines) + "\n")
        activity = activities[activity_key]
        if _prepare_activity_folder(activity):
            created_dirs.append(activity["directory"])
        note_names = UniqueNames()
        for file in activity["directory"].iterdir():
            note_names.reserve(file.name)
        note = activity["directory"] / note_names.take("materiali_moodle_buddy_assegnati.md")
        relative_note = note.relative_to(course).as_posix()
        content = ["---", "title: Materiali Moodle Buddy assegnati", "type: manual", "---", "",
                   "# Materiali Moodle Buddy", "", "Attività scelta esplicitamente dall'utente.", ""]
        content += [f"- [{escape_md(record['name'])}]({rel_link(relative_note, record['path'].relative_to(course).as_posix())})"
                    for record in selected]
        with note.open("x", encoding="utf-8") as handle:
            created_files.append(note)
            handle.write("\n".join(content) + "\n")
        ledger.setdefault("reports", {})[report_key] = report_relative
        with tempfile.TemporaryDirectory(prefix=".buddy-assign-", dir=course) as temporary:
            staged = Path(temporary) / "ledger.json"
            staged.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(staged, _inside(course, LEDGER))
        return {"assigned": len(changed), "remaining": sum(not r["activity_key"] for r in records),
                "report_path": report, "note": note,
                "files": list(dict.fromkeys(r["path"] for r in selected))}
    except BaseException:
        for file in reversed(created_files):
            file.unlink(missing_ok=True)
        raise
    finally:
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass

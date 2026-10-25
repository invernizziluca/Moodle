"""Place browser-downloaded materials into an existing exported activity.

This never receives browser cookies or passwords, never deletes source downloads,
and does not infer that manually imported files make the course complete.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import unquote, urlsplit

from .html2md import escape_md, md_url
from .naming import UniqueNames, safe_filename


def _archive_root(directory: str | Path) -> Path:
    root = Path(directory).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("La cartella dell'archivio deve essere una directory reale esistente")
    return root.resolve(strict=True)


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Percorso non valido nei dati dell'archivio")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or any(part in ("..", ".") for part in rel.parts):
        raise ValueError("Percorso esterno all'archivio")
    result = root
    for part in rel.parts:
        result = result / part
        if result.is_symlink():
            raise ValueError("I collegamenti simbolici non sono ammessi nell'archivio")
    resolved = result.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Percorso esterno all'archivio")
    return resolved


def _read_json(root: Path, name: str) -> dict:
    path = _inside(root, name)
    if not path.is_file():
        raise ValueError("Metadati dell'archivio non disponibili")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Metadati dell'archivio non validi")
    return data


def list_activities(archive_root: str | Path, course_id: int) -> list[dict]:
    """Return safe existing activity directories for one exported course."""
    root = _archive_root(archive_root)
    index = _read_json(root, "courses.json")
    rows = index.get("courses", [])
    matches = [row for row in rows if str(row.get("id")) == str(course_id)]
    if len(matches) != 1:
        raise ValueError("Corso non presente nell'archivio: esportalo prima di importare materiali")
    course_root = _inside(root, matches[0].get("folder", ""))
    if not course_root.is_dir():
        raise ValueError("Cartella del corso non disponibile")
    structure = _read_json(course_root, "course_structure.json")
    if str((structure.get("course") or {}).get("id")) != str(course_id):
        raise ValueError("La struttura non appartiene al corso selezionato")
    activities = []
    keys = set()

    def visit(nodes: list) -> None:
        for node in nodes:
            key, kind, md = node.get("key", ""), node.get("type", ""), node.get("md")
            if key.startswith("cm:") and kind not in ("label", "section", "subsection") and md:
                if key in keys:
                    raise ValueError("Identificativo attività duplicato nell'archivio")
                note = _inside(course_root, md)
                if not note.is_file() or note.parent == course_root:
                    raise ValueError("Nota dell'attività non valida")
                activities.append({"key": key, "title": str(node.get("title") or key),
                                   "directory": note.parent, "md": note})
                keys.add(key)
            visit(node.get("children") or [])

    visit(structure.get("sections") or [])
    return activities


def _regular_source(source: str | Path) -> Path:
    path = Path(source).absolute()
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Seleziona file normali, non collegamenti simbolici")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("È possibile importare soltanto file normali")
    return path


def import_files(sources: Iterable[str | Path], archive_root: str | Path,
                 course_id: int, activity_key: str) -> dict:
    """Copy downloads without overwrites; return {'files': [Path], 'note': Path}.

    The destination is resolved from current exported metadata rather than
    trusting a path previously returned by the GUI.
    """
    matches = [activity for activity in list_activities(archive_root, course_id)
               if activity["key"] == activity_key]
    if len(matches) != 1:
        raise ValueError("Attività non presente nel corso esportato")
    activity = matches[0]
    folder = activity["directory"]
    files = [_regular_source(source) for source in sources]
    if not files:
        raise ValueError("Seleziona almeno un file da importare")
    names = UniqueNames()
    for existing in folder.iterdir():
        names.reserve(existing.name)
    note = folder / names.take("materiali_browser.md")
    copied = []
    note_created = False
    try:
        for source in files:
            target = folder / names.take(safe_filename(source.name, 80))
            # O_EXCL on destination is provided by 'xb', O_NOFOLLOW prevents
            # replacing a selected source with a symlink on supporting systems.
            descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as src:
                if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                    raise ValueError("Il file selezionato non è più un file normale")
                with target.open("xb") as dest:
                    copied.append(target)
                    shutil.copyfileobj(src, dest)
        lines = ["---", "title: Materiali aggiunti dal browser", "type: manual", "---", "",
                 "# Materiali aggiunti dal browser", "",
                 f"Attività: [{escape_md(activity['title'])}]({md_url(activity['md'].name)})", "",
                 "Importazione manuale di file scelti dall'utente. La presenza di questi file non "
                 "certifica la completezza del corso né la corrispondenza con il contenuto Moodle.", ""]
        lines.extend(f"- [{escape_md(file.name)}]({md_url(file.name)})" for file in copied)
        with note.open("x", encoding="utf-8") as handle:
            note_created = True
            handle.write("\n".join(lines) + "\n")
        return {"files": copied, "note": note}
    except Exception:
        for target in copied:
            target.unlink(missing_ok=True)
        if note_created:
            note.unlink(missing_ok=True)
        raise


def _validate_playlist_references(playlist: Path, source_root: Path) -> None:
    """An imported offline bundle must not link to files outside itself."""
    text = playlist.read_text(encoding="utf-8-sig")
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("La playlist selezionata non è un file HLS valido")
    references = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            references.extend(match[0] or match[1] for match in
                              re.findall(r'\bURI=(?:"([^"]+)"|([^,\s]+))', line))
        else:
            references.append(line)
    for reference in references:
        parsed = urlsplit(reference)
        path = unquote(parsed.path)
        if parsed.scheme or parsed.netloc or path.startswith("/") or "\\" in path or ":" in path:
            raise ValueError("La playlist deve riferirsi soltanto ai file locali della cartella HLS")
        target = (playlist.parent / path).resolve(strict=True)
        if not target.is_relative_to(source_root) or not target.is_file():
            raise ValueError("La playlist contiene riferimenti esterni alla cartella selezionata")


def import_directory(source_dir: str | Path, archive_root: str | Path,
                     course_id: int, activity_key: str) -> dict:
    """Copy an offline HLS directory preserving all relative names and bytes.

    Only the enclosing folder gets a short unique name. Internal files must
    already have Windows-valid names, otherwise renaming would break playlists.
    """
    matches = [activity for activity in list_activities(archive_root, course_id)
               if activity["key"] == activity_key]
    if len(matches) != 1:
        raise ValueError("Attività non presente nel corso esportato")
    activity = matches[0]
    source = Path(source_dir).absolute()
    if source.is_symlink() or any(parent.is_symlink() for parent in source.parents) or not source.is_dir():
        raise ValueError("Seleziona una cartella HLS reale, non un collegamento simbolico")
    source = source.resolve(strict=True)
    # Importing the archive into one of its own activities would recursively
    # duplicate user work. A downloaded bundle should live outside the archive.
    archive = _archive_root(archive_root)
    if source.is_relative_to(archive) or archive.is_relative_to(source):
        raise ValueError("Scegli una cartella HLS esterna all'archivio")
    files, directories, playlists = [], [], []
    windows_paths = set()
    for current, dirnames, filenames in os.walk(source, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError("La cartella HLS non può contenere collegamenti simbolici")
            rel = path.relative_to(source)
            if len(rel.parts) > 12 or len(rel.as_posix()) > 180:
                raise ValueError("La cartella HLS contiene percorsi troppo lunghi o profondi")
            if any(safe_filename(part, 255) != part for part in rel.parts):
                raise ValueError("Un nome nella cartella HLS non è valido su Windows; non è stato rinominato")
            folded = rel.as_posix().casefold()
            if folded in windows_paths:
                raise ValueError("La cartella HLS contiene nomi duplicati su Windows")
            windows_paths.add(folded)
            if path.is_dir():
                directories.append(rel)
            elif stat.S_ISREG(path.stat().st_mode):
                files.append(rel)
                if path.suffix.lower() == ".m3u8":
                    playlists.append(path)
            else:
                raise ValueError("La cartella HLS deve contenere soltanto file e cartelle normali")
    if not playlists:
        raise ValueError("La cartella non contiene una playlist HLS .m3u8")
    for playlist in playlists:
        _validate_playlist_references(playlist, source)
    preferred = min(playlists, key=lambda p: (
        p.relative_to(source).as_posix() not in ("video.m3u8", "master.m3u8"),
        len(p.relative_to(source).parts), p.relative_to(source).as_posix()))
    folder = activity["directory"]
    names = UniqueNames()
    for entry in folder.iterdir():
        names.reserve(entry.name)
    destination = folder / names.take("video_browser")
    note = folder / names.take("materiali_browser.md")
    created = False
    note_created = False
    try:
        destination.mkdir()
        created = True
        for directory in sorted(directories, key=lambda path: len(path.parts)):
            (destination / directory).mkdir()
        for relative in files:
            source_file = source / relative
            descriptor = os.open(source_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as src:
                if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                    raise ValueError("Uno dei file HLS non è più un file normale")
                with (destination / relative).open("xb") as dest:
                    shutil.copyfileobj(src, dest)
        local_playlist = destination / preferred.relative_to(source)
        local_url = local_playlist.relative_to(folder).as_posix()
        lines = ["---", "title: Materiali aggiunti dal browser", "type: manual", "---", "",
                 "# Materiali aggiunti dal browser", "",
                 f"Attività: [{escape_md(activity['title'])}]({md_url(activity['md'].name)})", "",
                 f"- [Apri playlist HLS]({md_url(local_url)})", "",
                 f"Copiati {len(files)} file mantenendo i nomi e i percorsi relativi della cartella HLS.", "",
                 "Importazione manuale: apri la playlist con un lettore compatibile (per esempio VLC). "
                 "La presenza di questi file non certifica la completezza del corso né la corrispondenza "
                 "con il contenuto Moodle."]
        with note.open("x", encoding="utf-8") as handle:
            note_created = True
            handle.write("\n".join(lines) + "\n")
        return {"files": [destination / relative for relative in files], "note": note,
                "directory": destination, "playlist": local_playlist}
    except Exception:
        if created:
            shutil.rmtree(destination)
        if note_created:
            note.unlink(missing_ok=True)
        raise

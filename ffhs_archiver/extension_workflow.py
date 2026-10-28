"""A reviewable download session using the user's actual browser extensions."""

from pathlib import Path

from .browser_import import _archive_root, _inside, _read_json, list_activities
from .extension_bridge import DownloadSession, EXTENSIONS, is_youtube_url, open_moodle_page, safe_moodle_url
from .extension_import import import_buddy_downloads, import_fetchv_downloads, is_fetchv_filename
from .extension_import import (assign_buddy_files as _assign_buddy_files,
                               list_unmatched_buddy_files)
from . import fsutil
from .html2md import escape_md
from .naming import rel_link

def course_targets(archive_root, course_id):
    """Read current local metadata; no network or browser credentials are used."""
    root = _archive_root(archive_root)
    courses = _read_json(root, "courses.json").get("courses", [])
    rows = [c for c in courses if str(c.get("id")) == str(course_id)]
    if len(rows) != 1:
        raise ValueError("Prepara prima la struttura del corso nell'archivio")
    course = _inside(root, rows[0].get("folder", ""))
    structure = _read_json(course, "course_structure.json")
    activity_keys = {a["key"] for a in list_activities(root, course_id)}
    result = []

    def visit(nodes, parents=()):
        for node in nodes:
            if node.get("key") in activity_keys:
                url = node.get("moodle_url") or ""
                if not url:
                    url = f"https://moodle.ffhs.ch/mod/{node.get('type')}/view.php?id={node['key'][3:]}"
                try:
                    url = safe_moodle_url(url)
                except ValueError:
                    continue
                links = [entry.get("url", "") for entry in node.get("links") or []]
                youtube_only = node.get("type") == "url" and bool(links) and all(is_youtube_url(u) for u in links)
                result.append({"key": node["key"], "title": node.get("title") or node["key"],
                               "label": " › ".join((*parents, node.get("title") or node["key"])),
                               "url": url, "youtube_only": youtube_only})
            context = parents if node.get("key") in activity_keys else (*parents, node.get("title") or "Sezione")
            visit(node.get("children") or [], context)

    visit(structure.get("sections") or [])
    return result


def assign_buddy_files(archive_root, course_id, item_ids, activity_key):
    """Resolve existing copies in place and refresh the course entry point."""
    result = _assign_buddy_files(archive_root, course_id, item_ids, activity_key)
    try:
        refresh_extension_index(archive_root, course_id)
    except (OSError, ValueError) as exc:
        result["index_warning"] = str(exc)
    return result


class ExtensionWorkflow:
    """Snapshot before downloads, preview completed files, then import the selection.

    The real extensions remain in the normal browser. This class does not click
    their controls, read their private settings, or claim that a file is complete.
    The user confirms completion and reviews the selected local files.
    """

    def __init__(self, archive_root, course_id, tool, download_dir, activity_key=None):
        if tool not in EXTENSIONS:
            raise ValueError("Scegli Moodle Buddy oppure FetchV")
        self.root = _archive_root(archive_root)
        self.course_id, self.tool = int(course_id), tool
        targets = course_targets(self.root, self.course_id)
        self.activity_key = activity_key
        if tool == "fetchv":
            chosen = [a for a in targets if a["key"] == activity_key]
            if len(chosen) != 1:
                raise ValueError("Scegli l'attività a cui appartiene il video")
            if chosen[0]["youtube_only"]:
                raise ValueError("Questa attività rimanda a YouTube: nell'archivio rimane soltanto il link")
            self.url = chosen[0]["url"]
        else:
            if activity_key is not None and not any(a["key"] == activity_key for a in targets):
                raise ValueError("L'attività scelta non appartiene al corso")
            self.url = safe_moodle_url(f"https://moodle.ffhs.ch/course/view.php?id={self.course_id}")
        self.session = DownloadSession(download_dir)
        self.imported = False

    def open(self, browser="edge"):
        if not open_moodle_page(self.url, browser):
            raise OSError("Il browser non si è aperto: verifica Edge o scegli il browser predefinito")

    def preview(self):
        candidates = self.session.candidates()
        if self.tool == "fetchv":
            candidates = [c for c in candidates if is_fetchv_filename(c.path.name)]
        return candidates

    def review_existing(self, paths):
        candidates = self.session.preview_existing(paths)
        if self.tool == "fetchv":
            candidates = [c for c in candidates if is_fetchv_filename(c.path.name)]
        return candidates

    def collect(self, candidates):
        candidates = list(candidates)
        sources = self.session.validate_candidates(candidates)
        if self.tool == "fetchv":
            if any(not is_fetchv_filename(p.name) for p in sources):
                raise ValueError("Per FetchV scegli i file video/audio completati, non i file temporanei")
            result = import_fetchv_downloads(sources, self.root, self.course_id, self.activity_key,
                                            download_root=self.session.directory)
        else:
            result = import_buddy_downloads(sources, self.root, self.course_id,
                                           download_root=self.session.directory, activity_key=self.activity_key)
        self.session.mark_imported(candidates)
        try:
            refresh_extension_index(self.root, self.course_id)
        except (OSError, ValueError) as exc:
            result["index_warning"] = str(exc)  # the committed import/report is still available
        self.imported = True
        return result


def refresh_extension_index(archive_root, course_id):
    """Maintain one human-readable entry point for real extension imports."""
    root = _archive_root(archive_root)
    rows = _read_json(root, "courses.json").get("courses", [])
    course_rows = [r for r in rows if str(r.get("id")) == str(course_id)]
    if len(course_rows) != 1:
        raise ValueError("Corso non presente nell'archivio")
    course = _inside(root, course_rows[0]["folder"])
    lines = ["---", "title: Download dalle estensioni", "type: manual", "---", "",
             "# Download dalle estensioni", "",
             "[Panoramica del corso](course_overview.md)", "",
             "Copie importate dai download scelti dall'utente. Gli originali sono conservati. "
             "L'associazione a un'attività non certifica la completezza del corso.", ""]
    initial_length = len(lines)
    for key, label in (("moodle_buddy", "Moodle Buddy"), ("fetchv", "FetchV")):
        relative = f".archiver/{key}_imports.json"
        if not (course / relative).exists():
            continue
        ledger = _read_json(course, relative)
        lines.extend(["## " + label, ""])
        if key == "moodle_buddy":
            remaining = len(list_unmatched_buddy_files(root, course_id))
            lines.extend([f"Collegamenti da scegliere: **{remaining}**. "
                          "Puoi completarli nella scheda File da collegare dell'app.", ""])
            current = [path for name, path in ledger.get("reports", {}).items()
                       if name.startswith("associations:")]
            if current:
                _inside(course, current[-1])
                lines.extend([f"[Ultimo stato degli abbinamenti]({rel_link('download_estensioni.md', current[-1])})",
                              "", "I rapporti precedenti qui sotto sono storici: descrivono lo stato al momento dell'operazione.", ""])
        for report in dict.fromkeys(ledger.get("reports", {}).values()):
            path = _inside(course, report)
            lines.append(f"- [Rapporto: {escape_md(path.parent.name)}]({rel_link('download_estensioni.md', report)})")
        for entry in ledger.get("files", {}).values():
            relative_file = entry.get("path", "")
            path = _inside(course, relative_file)
            lines.append(f"- [{escape_md(path.name)}]({rel_link('download_estensioni.md', relative_file)})")
        lines.append("")
    if len(lines) == initial_length:
        lines.append("Nessun download importato finora. Usa i pulsanti Moodle Buddy e FetchV nell'app.")
    fsutil.write_text_atomic(course / "download_estensioni.md", "\n".join(lines) + "\n")
    return course / "download_estensioni.md"

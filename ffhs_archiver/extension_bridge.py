"""Coordinate downloads from the real FetchV and Moodle Buddy extensions.

Neither extension exposes an application-control API. This module opens the
actual Moodle page and lists files created during an explicit download session.
The user confirms that browser downloads have finished before collecting them;
a stable file signature alone is not proof that a download is complete.
No browser profile, cookies, password, or extension data is read.
"""

from __future__ import annotations

import os
import stat
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


EXTENSIONS = {
    "fetchv": {
        "name": "FetchV",
        "website": "https://fetchv.net/",
        "chrome_id": "nfmmmhanepmpifddlkkmihkalkoekpfd",
        "store": "https://chromewebstore.google.com/detail/nfmmmhanepmpifddlkkmihkalkoekpfd",
        "edge_store": "https://microsoftedge.microsoft.com/addons/detail/dbepbhhcmhodojepbagfppgpieeplpik",
    },
    "moodle_buddy": {
        "name": "Moodle Buddy",
        "website": "https://moodlebuddy.com/",
        "chrome_id": "nomahjpllnbcpbggnpiehiecfbjmcaeo",
        "store": "https://chromewebstore.google.com/detail/nomahjpllnbcpbggnpiehiecfbjmcaeo",
    },
}

_MODULES = frozenset({
    "assign", "attendance", "bigbluebuttonbn", "book", "chat", "checklist", "choice", "data",
    "feedback", "folder", "forum", "glossary", "h5pactivity", "hvp", "imscp", "journal",
    "kalvidassign", "kalvidres", "label", "lesson", "lti", "mumie", "page", "pdfannotator",
    "questionnaire", "quiz", "resource", "scorm", "subsection", "survey", "turnitintooltwo",
    "url", "wiki", "workshop", "zoom",
})
_PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload")


def safe_moodle_url(raw: str) -> str:
    """Canonical FFHS course/activity URL; authentication parameters are omitted."""
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme != "https" or parsed.hostname != "moodle.ffhs.ch" or
                parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError
        parts = parsed.path.split("/")
        valid_path = parsed.path == "/course/view.php" or (
            len(parts) == 4 and parts[1] == "mod" and parts[2] in _MODULES and parts[3] == "view.php")
        ids = parse_qs(parsed.query).get("id", [])
        if not valid_path or len(ids) != 1 or not ids[0].isascii() or not ids[0].isdigit() or int(ids[0]) <= 0:
            raise ValueError
        return urlunsplit(("https", "moodle.ffhs.ch", parsed.path, urlencode({"id": int(ids[0])}), ""))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Apri una pagina di corso o attività su https://moodle.ffhs.ch") from None


def is_youtube_url(raw: str) -> bool:
    """Recognize YouTube pages and media CDN hosts without substring matching."""
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except (ValueError, TypeError):
        return False
    return any(host == domain or host.endswith("." + domain) for domain in
               ("youtube.com", "youtube-nocookie.com", "youtu.be", "googlevideo.com", "ytimg.com"))


def open_moodle_page(url: str, browser: str = "edge") -> bool:
    """Open the normal installed browser/profile, without command shell or login export."""
    target = safe_moodle_url(url)
    if browser not in ("edge", "default"):
        raise ValueError("Browser non supportato: scegli Edge oppure il browser predefinito")
    if browser == "edge" and os.name == "nt":
        os.startfile("microsoft-edge:" + target)
        return True
    return bool(webbrowser.open(target, new=2))


@dataclass(frozen=True)
class DownloadCandidate:
    path: Path
    relative_path: str
    size: int
    mtime_ns: int

    @property
    def signature(self) -> tuple[int, int]:
        return self.size, self.mtime_ns


def _reparse_point(info: os.stat_result) -> bool:
    # Windows directory junctions must not turn a bounded walk into a loop.
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class DownloadSession:
    """Snapshot a chosen download folder before opening the real extension workflow.

    ``candidates()`` is called only after the user's "download finished" action.
    It provides a review list, not a claim about which extension produced a file.
    The selected candidates must be validated again immediately before import.
    """

    def __init__(self, download_dir: str | Path, *, max_files: int = 10000,
                 max_depth: int = 12, max_entries: int | None = None):
        if max_files <= 0 or max_depth < 0 or (max_entries is not None and max_entries <= 0):
            raise ValueError("I limiti della cartella download devono essere positivi")
        root = Path(download_dir).absolute()
        if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
            raise ValueError("Scegli una cartella download reale, non un collegamento simbolico")
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or _reparse_point(info):
            raise ValueError("Scegli una cartella download reale esistente")
        self.directory = root.resolve(strict=True)
        self.max_files = max_files
        self.max_depth = max_depth
        self.max_entries = max_entries if max_entries is not None else max_files * 4 + 100
        self._baseline = self._snapshot()
        self._previewed: dict[str, DownloadCandidate] = {}

    def _snapshot(self) -> dict[str, DownloadCandidate]:
        result: dict[str, DownloadCandidate] = {}
        visited = 0

        def visit(directory: Path, depth: int) -> None:
            nonlocal visited
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > self.max_entries:
                        raise ValueError("Troppi elementi nella cartella download: scegli una cartella più piccola")
                    name = entry.name
                    if name.startswith(".") or name.lower().endswith(_PARTIAL_SUFFIXES) or entry.is_symlink():
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # browser may atomically rename a temporary download
                    if _reparse_point(info):
                        continue
                    path = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= self.max_depth:
                            raise ValueError("Cartella download troppo profonda: scegli una sottocartella")
                        visit(path, depth + 1)
                    elif stat.S_ISREG(info.st_mode):
                        relative = path.relative_to(self.directory).as_posix()
                        result[relative] = DownloadCandidate(path, relative, info.st_size, info.st_mtime_ns)
                        if len(result) > self.max_files:
                            raise ValueError("Troppi file nella cartella download: scegli una cartella più piccola")
        visit(self.directory, 0)
        return result

    def candidates(self) -> list[DownloadCandidate]:
        current = self._snapshot()
        candidates = [item for key, item in current.items()
                      if key not in self._baseline or item.signature != self._baseline[key].signature]
        candidates.sort(key=lambda item: item.relative_path.casefold())
        self._previewed = {item.relative_path: item for item in candidates}
        return candidates

    def preview_existing(self, paths: Iterable[str | Path]) -> list[DownloadCandidate]:
        """Review existing downloads explicitly picked by the user, within the chosen folder."""
        current = self._snapshot()
        selected = []
        for raw in paths:
            path = Path(raw).absolute()
            try:
                relative = path.relative_to(self.directory).as_posix()
            except ValueError:
                raise ValueError("Scegli file all'interno della cartella Download selezionata") from None
            candidate = current.get(relative)
            if candidate is None or candidate.path != path:
                raise ValueError("Il file non è disponibile o è ancora un download temporaneo")
            if candidate not in selected:
                selected.append(candidate)
        self._previewed = {c.relative_path: c for c in selected}
        return selected

    def mark_imported(self, candidates: Iterable[DownloadCandidate]) -> None:
        """Exclude only committed selections from later scans in this session.

        Keep their reviewed signatures: if a browser later changes the file,
        it must appear again. Unselected downloads remain visible for review.
        """
        for candidate in candidates:
            self._baseline[candidate.relative_path] = candidate

    def validate_candidates(self, selected: Iterable[DownloadCandidate]) -> list[Path]:
        """Reject replaced/deleted/changed files and anything outside the reviewed session."""
        selected = list(selected)
        if not selected:
            raise ValueError("Seleziona almeno un download da importare")
        current = self._snapshot()
        seen = set()
        for candidate in selected:
            if (not isinstance(candidate, DownloadCandidate) or candidate.relative_path in seen or
                    self._previewed.get(candidate.relative_path) != candidate or
                    current.get(candidate.relative_path) != candidate):
                raise ValueError("Un download è cambiato dopo l'anteprima: aggiorna l'elenco prima di importare")
            seen.add(candidate.relative_path)
        return [candidate.path for candidate in selected]

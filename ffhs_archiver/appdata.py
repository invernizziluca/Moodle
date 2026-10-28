"""Application folders and user settings."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import APP_NAME

SETTINGS_VERSION = 3


def app_dir() -> Path:
    """Per-user data folder (%LOCALAPPDATA%\\FFHS Moodle Archiver on Windows)."""
    override = os.environ.get("FFHS_ARCHIVER_HOME")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "ffhs-moodle-archiver"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _windows_documents() -> Path | None:
    """Real 'Documents' folder on Windows (it may be redirected to OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        data4 = (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)
        folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF, data4)  # FOLDERID_Documents
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value)
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:
        return None


def default_archive_dir() -> Path:
    docs = _windows_documents() if os.name == "nt" else None
    if docs is None:
        docs = Path.home() / "Documents"
        if not docs.exists():
            docs = Path.home()
    return docs / "FFHS_ARCHIVE"


@dataclass
class Settings:
    archive_dir: str = ""
    include_forums: bool = True
    include_quiz_reviews: bool = True
    include_submissions: bool = True
    include_videos: bool = True           # download video files Moodle serves (e.g. an MP4 in a folder)
    download_videos: bool = False         # also fetch streamed FFHS lecture videos (Kaltura/SWITCHcast)
    capture_pages: bool = False           # optional advanced API capture; browser workflow needs no autologin
    extension_download_dir: str = ""
    extension_browser: str = "edge"
    max_file_mb: int = 0
    keep_versions: bool = True
    obsidian_ready: bool = True  # write a minimal .obsidian config so the archive opens as a vault
    save_raw_json: bool = False  # technical copies of the API answers: only for diagnosis
    anonymize_others: bool = True  # other students' names in forums -> "Partecipante N"
    use_protocol_handler: bool = True  # let the browser hand the login link to the program
    selected_courses: list = field(default_factory=list)
    window_geometry: str = ""
    verified_at: float = 0.0
    settings_version: int = SETTINGS_VERSION

    @classmethod
    def path(cls) -> Path:
        return app_dir() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        s = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        names = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in names:
                setattr(s, key, value)
        if int(data.get("settings_version") or 1) < 2:
            s.save_raw_json = False  # version 0.1 saved the raw API data by default
        if int(data.get("settings_version") or 1) < 3:
            s.download_videos = False
            s.capture_pages = False
        s.settings_version = SETTINGS_VERSION
        if not s.archive_dir:
            s.archive_dir = str(default_archive_dir())
        return s

    def save(self) -> None:
        path = self.path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

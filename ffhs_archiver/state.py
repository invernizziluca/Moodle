"""Persistent export state: what was downloaded where, so syncs are incremental and resumable."""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import fsutil

STATE_DIR = ".archiver"
SCHEMA = 1


class CourseState:
    """<course>/.archiver/state.json

    files:  key -> {path, size, mtime, last_modified, downloaded}
    texts:  rel path -> {sha, owner}
    items:  item key -> {title, modname, md, last_seen, first_seen, removed}
    """

    def __init__(self, course_root: Path) -> None:
        self.root = Path(course_root)
        self.path = self.root / STATE_DIR / "state.json"
        self.data = {"schema": SCHEMA, "files": {}, "texts": {}, "items": {}, "runs": []}
        self._dirty = False
        self._last_save = 0.0

    @classmethod
    def load(cls, course_root: Path) -> CourseState:
        st = cls(course_root)
        if fsutil.exists(st.path):
            try:
                loaded = json.loads(fsutil.read_text(st.path))
                if isinstance(loaded, dict):
                    st.data.update(loaded)
            except (OSError, ValueError):
                backup = st.path.with_name(f"state.broken-{int(time.time())}.json")
                fsutil.move(st.path, backup)
        for key in ("files", "texts", "items"):
            st.data.setdefault(key, {})
        st.data.setdefault("runs", [])
        return st

    @property
    def files(self) -> dict:
        return self.data["files"]

    @property
    def texts(self) -> dict:
        return self.data["texts"]

    @property
    def items(self) -> dict:
        return self.data["items"]

    def touch(self) -> None:
        self._dirty = True

    def save(self, force: bool = True) -> None:
        if not force and (not self._dirty or time.monotonic() - self._last_save < 5):
            return
        fsutil.write_text_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=1))
        self._dirty = False
        self._last_save = time.monotonic()


class ArchiveIndex:
    """<archive>/.archiver/archive.json: course id -> folder and last export summary."""

    def __init__(self, archive_root: Path) -> None:
        self.root = Path(archive_root)
        self.path = self.root / STATE_DIR / "archive.json"
        self.data = {"schema": SCHEMA, "courses": {}}
        if fsutil.exists(self.path):
            try:
                self.data.update(json.loads(fsutil.read_text(self.path)))
            except (OSError, ValueError):
                pass
        self.data.setdefault("courses", {})

    @property
    def courses(self) -> dict:
        return self.data["courses"]

    def folder_for(self, course_id: int) -> str:
        entry = self.courses.get(str(course_id))
        return entry.get("folder", "") if entry else ""

    def used_folders(self) -> set:
        return {c.get("folder", "").lower() for c in self.courses.values()}

    def update(self, course_id: int, **values) -> None:
        entry = self.courses.setdefault(str(course_id), {})
        entry.update(values)

    def save(self) -> None:
        fsutil.write_text_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=1))

"""Windows-safe, readable file and folder names."""

from __future__ import annotations

import os
import posixpath
import re
import unicodedata
from urllib.parse import quote

_TRANSLIT = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
    "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ø": "o", "Ø": "O", "å": "a", "Å": "A",
    "&": " und ", "+": " plus ", "@": " at ", "€": "EUR", "%": "Prozent",
}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def slug(text: str, max_len: int = 40, fallback: str = "ohne_Titel") -> str:
    """ASCII folder name: 'Thema 1: Video & Test' -> 'Thema_1_Video_und_Test'."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in (text or ""))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_.-")
    if len(text) > max_len:
        cut = text[:max_len]
        if "_" in cut[max_len // 2:]:
            cut = cut[: cut.rfind("_")]
        text = cut.strip("_.-")
    if not text:
        text = fallback
    if text.upper().split(".")[0] in _RESERVED:
        text += "_"
    return text


def safe_filename(name: str, max_len: int = 90) -> str:
    """Keep the original file name (umlauts included) but make it valid on Windows."""
    name = unicodedata.normalize("NFC", name or "")
    name = _INVALID_FILE_CHARS.sub("_", name).strip()
    name = name.rstrip(". ")
    stem, ext = os.path.splitext(name)
    if len(ext) > 16 or " " in ext:
        stem, ext = name, ""
    if not stem.strip(" ._"):
        stem = "Datei"
    if len(stem) + len(ext) > max_len:
        stem = stem[: max(8, max_len - len(ext))].rstrip(" ._")
    if stem.upper() in _RESERVED:
        stem += "_"
    return stem + ext


def numbered(index: int, title: str, max_len: int = 40) -> str:
    return f"{index:02d}_{slug(title, max_len)}"


class UniqueNames:
    """Hands out unique names within one folder (case-insensitive, like Windows)."""

    def __init__(self) -> None:
        self._used: set = set()

    def take(self, name: str) -> str:
        candidate = name
        stem, ext = os.path.splitext(name)
        n = 2
        while candidate.lower() in self._used:
            candidate = f"{stem} ({n}){ext}"
            n += 1
        self._used.add(candidate.lower())
        return candidate

    def reserve(self, name: str) -> None:
        self._used.add(name.lower())


def rel_link(from_file: str, to_path: str) -> str:
    """Relative, URL-encoded Markdown link from one archive file to another (posix paths)."""
    start = posixpath.dirname(from_file) or "."
    rel = posixpath.relpath(to_path, start)
    return quote(rel, safe="/")


def human_size(num: int | None) -> str:
    if num is None or num < 0:
        return ""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return f"{num} B"

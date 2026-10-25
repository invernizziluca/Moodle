"""File-system helpers: long-path safe on Windows, atomic writes, safe moves."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path


def lp(path) -> str:
    """Absolute path string; on Windows with the \\\\?\\ prefix so paths > 260 chars work."""
    p = os.path.abspath(str(path))
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        if p.startswith("\\\\"):
            return "\\\\?\\UNC\\" + p[2:]
        return "\\\\?\\" + p
    return p


def exists(path) -> bool:
    return os.path.exists(lp(path))


def is_file(path) -> bool:
    return os.path.isfile(lp(path))


def getsize(path) -> int:
    return os.path.getsize(lp(path))


def ensure_dir(path) -> None:
    os.makedirs(lp(path), exist_ok=True)


def read_bytes(path) -> bytes:
    with open(lp(path), "rb") as fh:
        return fh.read()


def read_text(path) -> str:
    return read_bytes(path).decode("utf-8")


def _replace(src: str, dst: str) -> None:
    """os.replace with a few retries: on Windows antivirus/indexer may hold a new file for a moment."""
    for attempt in range(4):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 3:
                raise
            time.sleep(0.3 * (attempt + 1))


def write_bytes_atomic(path, data: bytes) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(lp(tmp), "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    _replace(lp(tmp), lp(path))


def write_text_atomic(path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def move(src, dst) -> None:
    ensure_dir(Path(dst).parent)
    try:
        _replace(lp(src), lp(dst))
    except OSError:
        shutil.move(lp(src), lp(dst))


def remove(path) -> None:
    try:
        os.remove(lp(path))
    except FileNotFoundError:
        pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(lp(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remove_empty_dirs(root, start) -> None:
    """Delete empty folders from ``start`` upwards, never above ``root``."""
    root = os.path.abspath(str(root))
    cur = os.path.abspath(str(start))
    while cur.startswith(root) and cur != root:
        try:
            if os.listdir(lp(cur)):
                return
            os.rmdir(lp(cur))
        except OSError:
            return
        cur = os.path.dirname(cur)

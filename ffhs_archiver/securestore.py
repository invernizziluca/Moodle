"""Stores the Moodle access token encrypted for the current Windows user (DPAPI).

The university password is never seen by this program: login happens in the normal browser.
What is stored is the Moodle app token that Moodle issues after that login. On Windows the whole
record (token, name, user name) is encrypted with DPAPI (CryptProtectData, current-user scope, plus an
application-specific secondary key), so only the same Windows user on the same computer can read it.
On other systems (development only) the file is readable by its owner only.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_ENTROPY = b"FFHS-Moodle-Archiver/credentials/v2"
_LEGACY_ENTROPY_PREFIX = b"FFHS-Moodle-Archiver/token/v1|"
FORMAT = 2


# --------------------------------------------------------------------------- DPAPI (Windows)

def _dpapi():
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
                                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
                                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, DATA_BLOB, crypt32, kernel32


_UI_FORBIDDEN = 0x01


def dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    ctypes, DATA_BLOB, crypt32, kernel32 = _dpapi()
    in_buf = ctypes.create_string_buffer(data, len(data))
    ent_buf = ctypes.create_string_buffer(entropy, len(entropy))
    blob_in = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    blob_ent = DATA_BLOB(len(entropy), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), "FFHS Moodle Archiver", ctypes.byref(blob_ent),
                                    None, None, _UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData non riuscito")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    ctypes, DATA_BLOB, crypt32, kernel32 = _dpapi()
    in_buf = ctypes.create_string_buffer(data, len(data))
    ent_buf = ctypes.create_string_buffer(entropy, len(entropy))
    blob_in = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    blob_ent = DATA_BLOB(len(entropy), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None,
                                      _UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData non riuscito")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


# --------------------------------------------------------------------------- store

@dataclass
class Credentials:
    site: str
    token: str
    userid: int = 0
    fullname: str = ""
    username: str = ""
    sitename: str = ""
    release: str = ""
    saved_at: float = 0.0
    protection: str = ""
    privatetoken: str = ""  # kept (encrypted with the rest) only if video download is on; else empty


class TokenStore:
    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / "credentials.json"

    def save(self, creds: Credentials) -> str:
        record = asdict(creds)
        record["saved_at"] = time.time()
        record.pop("protection", None)
        raw = json.dumps(record).encode("utf-8")
        if os.name == "nt":
            blob, protection = dpapi_protect(raw, _ENTROPY), "dpapi"
        else:
            blob, protection = raw, "file-permissions"
        payload = {"format": FORMAT, "protection": protection, "data": base64.b64encode(blob).decode("ascii")}
        tmp = self.path.with_name(self.path.name + ".tmp")
        if os.name != "nt":
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload))
        else:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.path)
        return protection

    def load(self) -> Credentials | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            if data.get("format") == FORMAT:
                blob = base64.b64decode(data["data"])
                if data.get("protection") == "dpapi":
                    if os.name != "nt":
                        return None
                    blob = dpapi_unprotect(blob, _ENTROPY)
                record = json.loads(blob.decode("utf-8"))
                creds = Credentials(**{k: v for k, v in record.items() if k in Credentials.__dataclass_fields__})
                creds.protection = data.get("protection", "")
                return creds
            return self._load_legacy(data)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _load_legacy(self, data: dict) -> Credentials | None:
        """Version 0.1 stored only the token encrypted: read it once and rewrite in the new format."""
        blob = base64.b64decode(data["token"])
        if data.get("protection") == "dpapi":
            if os.name != "nt":
                return None
            token = dpapi_unprotect(blob, _LEGACY_ENTROPY_PREFIX + data["site"].encode("utf-8")).decode("utf-8")
        else:
            token = blob.decode("utf-8")
        creds = Credentials(site=data["site"], token=token, userid=int(data.get("userid") or 0),
                            fullname=data.get("fullname", ""), username=data.get("username", ""),
                            sitename=data.get("sitename", ""), release=data.get("release", ""))
        creds.protection = self.save(creds)
        return creds

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def self_test() -> str:
    """DPAPI round trip (used by the 'Verifica accesso' report)."""
    if os.name != "nt":
        return "non Windows: file leggibile solo dal proprietario"
    sample = "prova-è-€".encode()
    enc = dpapi_protect(sample, b"self-test")
    if enc == sample or dpapi_unprotect(enc, b"self-test") != sample:
        raise OSError("cifratura DPAPI non coerente")
    try:
        dpapi_unprotect(enc, b"wrong-entropy")
    except OSError:
        return "cifratura DPAPI di Windows funzionante"
    raise OSError("DPAPI ha accettato una chiave secondaria errata")

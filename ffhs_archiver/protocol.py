"""Temporary registration of the ``moodlemobile://`` link handler for the current Windows user.

Why: at the end of the browser login Moodle hands the access token to the app through a
``moodlemobile://token=…`` link. Registering a handler lets the browser pass that link straight to
this program ("Open …?" prompt) instead of asking the user to copy it.

Safety properties (see docs/REVISIONE_SICUREZZA.md):
- only ``HKEY_CURRENT_USER\\Software\\Classes\\moodlemobile`` is touched: no admin rights, no other key;
- before the first change the complete existing key (all values and subkeys, with their types) is saved
  to a backup file and restored exactly afterwards; if nothing existed the key is deleted again;
- our registration carries a marker (login channel id + expiry), so a leftover after a crash is
  recognised and removed: at the next program start, by the link helper once expired, or from the menu;
- a registration that is not ours is never deleted and never overwritten without a backup;
- the registration lives only while a login is in progress (at most 15 minutes).

The registry module is injectable so the logic can be tested without Windows (tests/fake_winreg.py).
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

KEY_PATH = r"Software\Classes\moodlemobile"
COMMAND_PATH = KEY_PATH + r"\shell\open\command"
MARKER = "FFHSMoodleArchiver"
BACKUP_NAME = "protocol_backup.json"
LEGACY_STATE = "sso_handler_previous.json"  # written by version 0.1
SELFTEST_PATH = r"Software\FFHS Moodle Archiver\SelfTest"
SELFTEST_PARENT = r"Software\FFHS Moodle Archiver"

_DEFAULT = object()


def default_registry():
    if os.name != "nt":
        return None
    import winreg

    return winreg


def _encode(data):
    if isinstance(data, (bytes, bytearray)):
        return {"b64": base64.b64encode(bytes(data)).decode("ascii")}
    return data


def _decode(data):
    if isinstance(data, dict) and "b64" in data:
        return base64.b64decode(data["b64"])
    return data


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class ProtocolRegistrar:
    def __init__(self, state_dir: Path, reg=_DEFAULT) -> None:
        self.state_dir = Path(state_dir)
        self.reg = default_registry() if reg is _DEFAULT else reg

    @property
    def supported(self) -> bool:
        return self.reg is not None

    @property
    def backup_path(self) -> Path:
        return self.state_dir / BACKUP_NAME

    # ------------------------------------------------------------------ registry primitives
    def _hkcu(self):
        return self.reg.HKEY_CURRENT_USER

    def _subkeys(self, key) -> list:
        names = []
        i = 0
        while True:
            try:
                names.append(self.reg.EnumKey(key, i))
            except OSError:
                return names
            i += 1

    def _snapshot(self, path: str) -> dict | None:
        """All values (name, data, type) and subkeys of ``path``; None if the key does not exist."""
        try:
            key = self.reg.OpenKey(self._hkcu(), path, 0, self.reg.KEY_READ)
        except FileNotFoundError:
            return None
        with key:
            values = []
            i = 0
            while True:
                try:
                    name, data, typ = self.reg.EnumValue(key, i)
                except OSError:
                    break
                values.append([name, _encode(data), typ])
                i += 1
            subs = self._subkeys(key)
        children = {}
        for sub in subs:
            child = self._snapshot(path + "\\" + sub)
            if child is not None:
                children[sub] = child
        return {"values": values, "subkeys": children}

    def _delete_tree(self, path: str) -> None:
        try:
            key = self.reg.OpenKey(self._hkcu(), path, 0, self.reg.KEY_READ)
        except FileNotFoundError:
            return
        with key:
            subs = self._subkeys(key)
        for sub in subs:
            self._delete_tree(path + "\\" + sub)
        try:
            self.reg.DeleteKey(self._hkcu(), path)
        except FileNotFoundError:
            pass

    def _create_tree(self, path: str, snap: dict) -> None:
        key = self.reg.CreateKeyEx(self._hkcu(), path, 0, self.reg.KEY_WRITE)
        with key:
            for name, data, typ in snap.get("values") or []:
                self.reg.SetValueEx(key, name, 0, typ, _decode(data))
        for sub, child in (snap.get("subkeys") or {}).items():
            if child is not None:
                self._create_tree(path + "\\" + sub, child)

    def _query(self, path: str, name: str):
        try:
            key = self.reg.OpenKey(self._hkcu(), path, 0, self.reg.KEY_READ)
        except FileNotFoundError:
            return None
        with key:
            try:
                return self.reg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                return None

    # ------------------------------------------------------------------ ownership
    def owner(self) -> dict | None:
        """Our marker (dict) if the current moodlemobile registration is ours, else None."""
        if not self.supported:
            return None
        raw = self._query(KEY_PATH, MARKER)
        if raw is not None:
            try:
                info = json.loads(raw)
            except (TypeError, ValueError):
                info = {}
            return info if isinstance(info, dict) else {}
        command = self._query(COMMAND_PATH, "") or ""
        if isinstance(command, str) and "--sso-callback" in command and (
                "ffhs_archiver" in command or "FFHS Moodle Archiver" in command):
            return {"legacy": True}  # registered by version 0.1 (no marker)
        return None

    def status(self) -> str:
        if not self.supported:
            return "non applicabile (non Windows)"
        info = self.owner()
        if info is None:
            return "assente" if self._snapshot(KEY_PATH) is None else "appartiene a un'altra applicazione (non toccato)"
        if info.get("legacy"):
            return "registrazione residua della versione 0.1"
        expires = info.get("expires") or 0
        state = "scaduta" if time.time() > expires else "attiva"
        return f"registrazione temporanea {state} (fino alle {time.strftime('%H:%M', time.localtime(expires))})"

    # ------------------------------------------------------------------ register / restore
    def _legacy_backup(self) -> dict:
        old = _read_json(self.state_dir / LEGACY_STATE) or {}
        tree = None
        if old.get("existed") and old.get("command"):
            reg_sz = self.reg.REG_SZ
            tree = {"values": [["", "URL:moodlemobile", reg_sz], ["URL Protocol", "", reg_sz]],
                    "subkeys": {"shell": {"values": [], "subkeys": {"open": {"values": [], "subkeys": {
                        "command": {"values": [["", old["command"], reg_sz]], "subkeys": {}}}}}}}}
        return {"version": 1, "saved_at": time.time(), "tree": tree, "source": "v0.1"}

    def register(self, command: str, channel_id: str, expires: float) -> None:
        if not self.supported:
            raise OSError("Registrazione dei link disponibile solo su Windows")
        current = self.owner()
        if current is None:
            # Not ours (or absent): save everything first; abort if the backup cannot be written.
            _write_json_atomic(self.backup_path, {"version": 1, "saved_at": time.time(),
                                                  "tree": self._snapshot(KEY_PATH)})
        elif not self.backup_path.exists():
            # Our own leftover: never treat it as "the previous state".
            _write_json_atomic(self.backup_path, self._legacy_backup() if current.get("legacy") else
                               {"version": 1, "saved_at": time.time(), "tree": None})
        try:
            self._delete_tree(KEY_PATH)
            key = self.reg.CreateKeyEx(self._hkcu(), KEY_PATH, 0, self.reg.KEY_WRITE)
            with key:
                # The marker is written first: any partial state is then recognisable as ours.
                self.reg.SetValueEx(key, MARKER, 0, self.reg.REG_SZ, json.dumps(
                    {"channel": channel_id, "expires": int(expires), "created": int(time.time())}))
                self.reg.SetValueEx(key, "", 0, self.reg.REG_SZ,
                                    "URL:moodlemobile (FFHS Moodle Archiver, temporaneo)")
                self.reg.SetValueEx(key, "URL Protocol", 0, self.reg.REG_SZ, "")
            key = self.reg.CreateKeyEx(self._hkcu(), COMMAND_PATH, 0, self.reg.KEY_WRITE)
            with key:
                self.reg.SetValueEx(key, "", 0, self.reg.REG_SZ, command)
        except OSError:
            self._apply_backup()
            raise

    def _drop_backup_files(self) -> None:
        for name in (BACKUP_NAME, LEGACY_STATE):
            try:
                (self.state_dir / name).unlink()
            except FileNotFoundError:
                pass

    def _apply_backup(self, legacy: bool = False) -> str:
        """Put back exactly what was saved before our registration (or nothing)."""
        backup = _read_json(self.backup_path)
        if backup is None and legacy:
            backup = self._legacy_backup()
        self._delete_tree(KEY_PATH)
        tree = backup.get("tree") if isinstance(backup, dict) else None
        if tree:
            self._create_tree(KEY_PATH, tree)
        self._drop_backup_files()
        return "restored" if tree else "removed"

    def _not_ours(self) -> str:
        """The key carries no marker of ours. If it is missing while a backup exists, our registration was
        interrupted half-way (e.g. power cut): put the saved key back. Otherwise the backup is stale."""
        backup = _read_json(self.backup_path)
        if isinstance(backup, dict) and backup.get("tree") and self._snapshot(KEY_PATH) is None:
            return self._apply_backup()
        self._drop_backup_files()
        return "not-ours"

    def restore(self, only_channel: str | None = None) -> str:
        """Undo our registration (restoring what was there before). Never touches a foreign registration."""
        if not self.supported:
            return "unsupported"
        current = self.owner()
        if current is None:
            return self._not_ours()
        if only_channel is not None and current.get("channel") != only_channel:
            return "other-attempt"
        return self._apply_backup(legacy=bool(current.get("legacy")))

    def cleanup_leftover(self) -> str:
        """At program start no login can be in progress: remove any registration of ours."""
        if not self.supported:
            return "unsupported"
        if self.owner() is None:
            result = self._not_ours()
            return "clean" if result == "not-ours" else result
        return self.restore()

    # ------------------------------------------------------------------ self test (real registry, test key)
    def self_test(self) -> str:
        if not self.supported:
            return "non applicabile (non Windows)"
        r = self.reg
        tree = {"values": [["", "valore predefinito", r.REG_SZ], ["Numero", 7, r.REG_DWORD],
                           ["Binario", _encode(b"\x00\x01\xff"), r.REG_BINARY],
                           ["Multi", ["a", "b"], r.REG_MULTI_SZ], ["URL Protocol", "", r.REG_SZ]],
                "subkeys": {"shell": {"values": [], "subkeys": {"open": {"values": [], "subkeys": {
                    "command": {"values": [["", '"C:\\Test\\app.exe" "%1"', r.REG_SZ]], "subkeys": {}}}}}}}}
        self._delete_tree(SELFTEST_PATH)
        try:
            self._create_tree(SELFTEST_PATH, tree)
            snap = self._snapshot(SELFTEST_PATH)
            self._delete_tree(SELFTEST_PATH)
            if self._snapshot(SELFTEST_PATH) is not None:
                raise OSError("la chiave di prova non è stata cancellata")
            self._create_tree(SELFTEST_PATH, snap)
            again = self._snapshot(SELFTEST_PATH)
        finally:
            self._delete_tree(SELFTEST_PATH)
            parent = self._snapshot(SELFTEST_PARENT)
            if parent is not None and not parent["values"] and not parent["subkeys"]:
                self._delete_tree(SELFTEST_PARENT)

        def norm(node):
            return {"values": sorted((n, json.dumps(d, sort_keys=True), t) for n, d, t in node["values"]),
                    "subkeys": {k: norm(v) for k, v in node["subkeys"].items()}}

        if norm(snap) != norm(tree) or norm(again) != norm(tree):
            raise OSError("salvataggio/ripristino della chiave di prova non identico")
        return "salvataggio e ripristino completo di una chiave di prova riusciti"

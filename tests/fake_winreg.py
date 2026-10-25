"""In-memory stand-in for Python's ``winreg`` module (only what protocol.py uses), following the real
module's behaviour: case-insensitive key names, FileNotFoundError for missing keys/values, OSError at the
end of enumerations, DeleteKey refusing keys that still have subkeys, type checks on SetValueEx."""

from __future__ import annotations

HKEY_CURRENT_USER = "HKCU"
KEY_READ = 0x20019
KEY_WRITE = 0x20006
KEY_ALL_ACCESS = 0xF003F
REG_NONE, REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD, REG_MULTI_SZ, REG_QWORD = 0, 1, 2, 3, 4, 7, 11


class _Node:
    def __init__(self, name: str) -> None:
        self.name = name
        self.values: dict = {}  # lower name -> (name, data, type)
        self.subkeys: dict = {}  # lower name -> _Node


class _Key:
    def __init__(self, reg: FakeWinreg, node: _Node, writable: bool) -> None:
        self.reg = reg
        self.node = node
        self.writable = writable
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def Close(self):
        self.closed = True


class FakeWinreg:
    HKEY_CURRENT_USER = HKEY_CURRENT_USER
    KEY_READ = KEY_READ
    KEY_WRITE = KEY_WRITE
    KEY_ALL_ACCESS = KEY_ALL_ACCESS
    REG_NONE, REG_SZ, REG_EXPAND_SZ, REG_BINARY = REG_NONE, REG_SZ, REG_EXPAND_SZ, REG_BINARY
    REG_DWORD, REG_MULTI_SZ, REG_QWORD = REG_DWORD, REG_MULTI_SZ, REG_QWORD

    def __init__(self) -> None:
        self.root = _Node("HKCU")
        self.fail_on_set = None  # callable(name, data) -> bool: raise PermissionError when True
        self.calls = 0

    # ---- helpers
    def _walk(self, path: str, create: bool = False) -> _Node:
        node = self.root
        for part in [p for p in path.split("\\") if p]:
            child = node.subkeys.get(part.lower())
            if child is None:
                if not create:
                    raise FileNotFoundError(2, "Impossibile trovare il file specificato")
                child = _Node(part)
                node.subkeys[part.lower()] = child
            node = child
        return node

    # ---- winreg API
    def OpenKey(self, root, path, reserved=0, access=KEY_READ):
        self.calls += 1
        return _Key(self, self._walk(path), bool(access & KEY_WRITE & ~KEY_READ))

    def CreateKeyEx(self, root, path, reserved=0, access=KEY_WRITE):
        self.calls += 1
        return _Key(self, self._walk(path, create=True), True)

    def CreateKey(self, root, path):
        return self.CreateKeyEx(root, path)

    def SetValueEx(self, key, name, reserved, typ, data):
        self.calls += 1
        if key.closed:
            raise OSError(6, "handle non valido")
        name = name or ""
        if self.fail_on_set and self.fail_on_set(name, data):
            raise PermissionError(5, "Accesso negato")
        expected = {REG_SZ: str, REG_EXPAND_SZ: str, REG_DWORD: int, REG_QWORD: int, REG_BINARY: (bytes, type(None)),
                    REG_MULTI_SZ: list}.get(typ)
        if expected and not isinstance(data, expected):
            raise ValueError(f"dati di tipo errato per il tipo {typ}")
        key.node.values[name.lower()] = (name, data, typ)

    def QueryValueEx(self, key, name):
        entry = key.node.values.get((name or "").lower())
        if entry is None:
            raise FileNotFoundError(2, "Impossibile trovare il file specificato")
        return entry[1], entry[2]

    def EnumValue(self, key, index):
        values = list(key.node.values.values())
        if index >= len(values):
            raise OSError(259, "Non sono disponibili altri dati")
        return values[index]

    def EnumKey(self, key, index):
        subs = list(key.node.subkeys.values())
        if index >= len(subs):
            raise OSError(259, "Non sono disponibili altri dati")
        return subs[index].name

    def DeleteKey(self, root, path):
        self.calls += 1
        parts = [p for p in path.split("\\") if p]
        parent = self._walk("\\".join(parts[:-1]))
        node = parent.subkeys.get(parts[-1].lower())
        if node is None:
            raise FileNotFoundError(2, "Impossibile trovare il file specificato")
        if node.subkeys:
            raise PermissionError(5, "Accesso negato")
        del parent.subkeys[parts[-1].lower()]

    # ---- test helpers
    def dump(self, path: str):
        try:
            node = self._walk(path)
        except FileNotFoundError:
            return None

        def rec(n):
            return {"values": sorted((v[0], repr(v[1]), v[2]) for v in n.values.values()),
                    "subkeys": {s.name: rec(s) for s in n.subkeys.values()}}

        return rec(node)

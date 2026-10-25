"""Privacy helpers for what is written into the archive.

- ``scrub_secrets``: no access keys end up in the archive (and therefore in files you may later give
  to an AI service): ``token=`` parameters are removed, personal file keys of ``tokenpluginfile.php``
  links are replaced by normal ``pluginfile.php`` links (they open in the browser after the normal
  Moodle login), ``webservice/pluginfile.php`` links become ``pluginfile.php`` links, session keys are
  neutralised.
- ``Pseudonymizer``: names of other students in forums become "Partecipante 1, 2, …". Your own name, the
  course teachers (Moodle "contacts") and the authors of announcement forums stay visible.
"""

from __future__ import annotations

import re

_TOKEN_FILE = re.compile(r"tokenpluginfile\.php/[0-9a-fA-F]{32}/")
_WS_FILE = re.compile(r"/webservice/pluginfile\.php/")
_PARAM_FIRST = re.compile(r"\?(?:ws|private)?token=[^&\s\"'<>)\]#]*&", re.I)
_PARAM_ANY = re.compile(r"[?&](?:ws|private)?token=[^&\s\"'<>)\]#]*", re.I)
_ESCAPED_PARAM = re.compile(r"(?:&amp;|\\u0026)(?:ws|private)?token=[^&\s\"'<>)\]#\\]*", re.I)
_SESSKEY = re.compile(r"(sesskey=)[A-Za-z0-9]+", re.I)


def scrub_secrets(text: str) -> str:
    if not text:
        return text
    text = _TOKEN_FILE.sub("pluginfile.php/", text)
    text = _WS_FILE.sub("/pluginfile.php/", text)
    text = _PARAM_FIRST.sub("?", text)
    text = _PARAM_ANY.sub("", text)
    text = _ESCAPED_PARAM.sub("", text)
    return _SESSKEY.sub(r"\1rimosso", text)


class Pseudonymizer:
    def __init__(self, enabled: bool, keep_ids=()) -> None:
        self.enabled = enabled
        self.keep = {int(i) for i in keep_ids if i}
        self.names: dict = {}

    def name(self, userid, fullname: str, staff: bool = False) -> str:
        if not self.enabled or staff:
            return fullname
        try:
            uid = int(userid or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid and uid in self.keep:
            return fullname
        key = uid or fullname
        if key not in self.names:
            self.names[key] = f"Partecipante {len(self.names) + 1}"
        return self.names[key]

    @property
    def used(self) -> bool:
        return bool(self.names)

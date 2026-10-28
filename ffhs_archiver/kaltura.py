"""Download FFHS lecture videos (Kaltura / SWITCHcast) for personal, offline study.

This module is used only when the user turns on video download. It does not bypass any
access control: it opens the video platform **as the already-authenticated user**, using
Moodle's own official autologin mechanism (the same the Moodle app uses to open web
content), and then fetches the very stream the user can already watch in the browser.

A video the account cannot access, or that the platform does not let an authenticated
browser download, is *flagged* with its Moodle link instead of being fetched — nothing is
ever silently dropped, and no DRM or restriction is circumvented.

The network side (cookies, host allow-list, autologin) lives in ``moodle.VideoSession``.
Everything that is pure parsing (embed URLs, the LTI auto-submit form, the player config,
HLS playlists) lives here as small functions so it can be tested without the network.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

log = logging.getLogger(__name__)

# Default Kaltura API host (used only if the player config does not name its own service).
DEFAULT_SERVICE = "https://cdnapisec.kaltura.com"
ENTRY_RE = re.compile(r"""(?:entry_?id[/=\"':]+|/entryid/|["']id["']\s*:\s*["'])([01]_[A-Za-z0-9]{6,12})""", re.I)
# partner id in config (partner_id: 123 / "partnerId":123), in a path (/p/123/ or /partner_id/123)
# or in a widget id (wid:'_123', "widgetId":"_123").
PARTNER_RE = re.compile(r"(?:partner_?id[\"'=:/\s]+|/p/|/partner_id/|w(?:idget)?id[\"'=:\s]+_?)(\d{3,10})", re.I)
# a Kaltura session: ks:'...', "ks":"...", ks=... (url/flashvars), flashvars[ks]=...
KS_RE = re.compile(r"""(?:["']?ks["']?\s*[:=]\s*["']|[?&]ks=|flashvars\[ks\]\s*=\s*["']?)"""
                   r"""([A-Za-z0-9+/=_\-]{24,})""")
SERVICE_RE = re.compile(r"""["']?(?:serviceUrl|service_url|serverName|service_base)["']?\s*[:=]\s*"""
                        r"""["'](https?://[^"']+?|[A-Za-z0-9.\-]+\.(?:kaltura\.com|switch\.ch))["']""")
# the <iframe> that a KAF launch/browse page embeds the real player in (same KAF host).
IFRAME_RE = re.compile(r"""<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I)
# the Playkit/embed script often carries /p/<pid>/ ... /uiconf_id/<id> in its src.
UICONF_RE = re.compile(r"(?:uiconf_?id[\"'=:/\s]+)(\d{3,10})", re.I)


@dataclass
class KalturaRef:
    """A Kaltura/SWITCHcast video identified from Moodle content."""
    entry_id: str
    kaf_host: str = ""
    launch_url: str = ""          # Moodle /filter/kaltura/lti_launch.php URL (opens the player as the user)
    uiconf_id: str = ""
    title: str = ""

    @property
    def ident(self) -> str:
        return f"kaltura:{self.entry_id}"


@dataclass
class MediaSource:
    """A resolved, fetchable media location for one entry."""
    kind: str                     # "progressive" (single MP4) | "hls"
    url: str
    suffix: str = ".mp4"
    headers: dict = field(default_factory=dict)


@dataclass
class VideoOutcome:
    status: str                   # "downloaded" | "flagged"
    suffix: str = ".mp4"
    size: int = 0
    note: str = ""


# --------------------------------------------------------------------------- parsing (pure)
def parse_kaltura(url: str, title: str = "") -> KalturaRef | None:
    """Recognise a Kaltura/SWITCHcast embed or launch URL and pull out the entry id and host."""
    if not url:
        return None
    unquoted = urllib.parse.unquote(url)
    if not re.search(r"kaltura|switchcast|/kaf|kaf\.", unquoted, re.I) and "lti_launch" not in unquoted:
        return None
    m = ENTRY_RE.search(unquoted)
    if not m:
        return None
    entry_id = m.group(1)
    launch_url = ""
    kaf_host = ""
    uiconf = ""
    parts = urllib.parse.urlsplit(url)
    if "lti_launch.php" in parts.path:
        launch_url = url
        q = urllib.parse.parse_qs(parts.query)
        source = (q.get("source") or [""])[0]
        if source:
            kaf_host = urllib.parse.urlsplit(source).netloc
        ps = q.get("playerSkin") or q.get("uiconf_id")
        if ps:
            uiconf = ps[0]
    else:
        kaf_host = parts.netloc
    return KalturaRef(entry_id=entry_id, kaf_host=kaf_host, launch_url=launch_url, uiconf_id=uiconf, title=title)


def parse_autosubmit_form(html: str, base_url: str = "") -> tuple[str, dict] | None:
    """The LTI launch page is a self-submitting <form>: return (action_url, {field: value})."""
    form = re.search(r"<form\b[^>]*>(.*?)</form>", html, re.I | re.S)
    if not form:
        return None
    attrs = html[form.start():form.start() + form.group(0).find(">") + 1]
    action_m = re.search(r"""\baction\s*=\s*["']([^"']*)["']""", attrs, re.I)
    action = action_m.group(1) if action_m else ""
    if base_url:
        action = urllib.parse.urljoin(base_url, action) if action else base_url
    fields: dict = {}
    for inp in re.finditer(r"<input\b[^>]*>", form.group(1), re.I):
        tag = inp.group(0)
        name = re.search(r"""\bname\s*=\s*["']([^"']*)["']""", tag, re.I)
        value = re.search(r"""\bvalue\s*=\s*["']([^"']*)["']""", tag, re.I)
        if name:
            fields[_html_unescape(name.group(1))] = _html_unescape(value.group(1)) if value else ""
    return action, fields


def extract_player_config(html: str) -> dict:
    """Pull partnerId / ks / serviceUrl / entryId out of a Kaltura embed or player page.

    Handles the common KAF/Playkit shapes: an inline ``kConf``/``kWidget`` config, a
    ``kalturaIframePackageData`` JSON blob, flashvars, a ks in a URL parameter, and the partner id
    taken from a ``/p/<id>/`` path or a widget id. Missing pieces are simply left out.
    """
    cfg: dict = {}
    pm = PARTNER_RE.search(html)
    if pm:
        cfg["partner_id"] = pm.group(1)
    km = KS_RE.search(html)
    if km:
        cfg["ks"] = km.group(1)
    sm = SERVICE_RE.search(html)
    if sm:
        service = sm.group(1).rstrip("/")
        cfg["service_url"] = service if service.startswith("http") else "https://" + service
    em = ENTRY_RE.search(html)
    if em:
        cfg["entry_id"] = em.group(1)
    um = UICONF_RE.search(html)
    if um:
        cfg["uiconf_id"] = um.group(1)
    return cfg


def extract_iframe_src(html: str, base_url: str = "") -> str:
    """The player <iframe> src on a KAF browse/launch page, absolutised against ``base_url``."""
    m = IFRAME_RE.search(html)
    if not m:
        return ""
    src = _html_unescape(m.group(1))
    return urllib.parse.urljoin(base_url, src) if base_url else src


def playmanifest_url(service_url: str, partner_id: str, entry_id: str, fmt: str, ks: str = "") -> str:
    """Build a Kaltura playManifest URL (``fmt`` is 'url' for a single MP4 or 'applehttp' for HLS)."""
    base = (service_url or DEFAULT_SERVICE).rstrip("/")
    path = (f"/p/{partner_id}/sp/{partner_id}00/playManifest/entryId/{entry_id}"
            f"/protocol/https/format/{fmt}")
    if fmt == "url":
        path += "/flavorParamId/0"
    url = base + path + "/video" + (".mp4" if fmt == "url" else ".m3u8")
    if ks:
        url += "?ks=" + urllib.parse.quote(ks)
    return url


def parse_master_playlist(text: str, base_url: str) -> list[tuple[int, str]]:
    """Return [(bandwidth, variant_url)] from an HLS master playlist, best last."""
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            bw = re.search(r"BANDWIDTH=(\d+)", line)
            band = int(bw.group(1)) if bw else 0
            for nxt in lines[i + 1:]:
                nxt = nxt.strip()
                if nxt and not nxt.startswith("#"):
                    out.append((band, urllib.parse.urljoin(base_url, nxt)))
                    break
    out.sort(key=lambda x: x[0])
    return out


def parse_media_playlist(text: str, base_url: str) -> tuple[str | None, list[str]]:
    """Return (init_segment_url | None, [segment_urls]) from an HLS media playlist."""
    init = None
    segs: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MAP"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init = urllib.parse.urljoin(base_url, m.group(1))
        elif line and not line.startswith("#"):
            segs.append(urllib.parse.urljoin(base_url, line))
    return init, segs


def is_master_playlist(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


def _html_unescape(text: str) -> str:
    import html as _html
    return _html.unescape(text)


# --------------------------------------------------------------------------- orchestration (network)
class KalturaClient:
    """Resolves and downloads one entry using an injected ``session`` (``moodle.VideoSession``).

    The session provides: ``ensure_session() -> bool``, ``get_text(url)``, ``post_text(url, fields)``,
    ``get_bytes(url)``, ``stream(url, dest, progress) -> int``, and ``available`` / ``reason``.
    """

    def __init__(self, session: Any) -> None:
        self.session = session
        self._config: dict | None = None  # cached partner_id / service_url / ks from the first launch
        self.last_diagnostic = ""         # redacted note on the last resolve (for Verifica/diagnose)

    # -- resolution -------------------------------------------------------
    def _launch_config(self, ref: KalturaRef) -> dict:
        """Open the player as the user (autologin + LTI auto-submit) and read its config once.

        FFHS wraps the player in a Kaltura (KAF) page reached by a self-submitting LTI form; some
        skins then embed the real player in an <iframe>. We follow the form (up to 3 hops) and, if the
        partner id is still not found, one same-host iframe, merging whatever each page reveals.
        """
        if self._config is not None and self._config.get("partner_id"):
            cfg = dict(self._config)
            cfg["entry_id"] = ref.entry_id
            return cfg
        if not ref.launch_url:
            return {}
        if not self.session.ensure_session():
            return {}
        merged: dict = {}
        html = self.session.get_text(ref.launch_url)
        current_url = ref.launch_url
        for _ in range(3):
            self._merge_config(merged, html)
            if merged.get("partner_id") and merged.get("ks"):
                break
            form = parse_autosubmit_form(html, current_url)
            if not form or not form[0]:
                break
            current_url = form[0]
            html = self.session.post_text(form[0], form[1])
        self._merge_config(merged, html)
        if not merged.get("partner_id"):
            # Some KAF skins load the player in an iframe on the same host; follow it once.
            iframe = extract_iframe_src(html, current_url)
            if iframe:
                try:
                    inner = self.session.get_text(iframe)
                    self._merge_config(merged, inner)
                except Exception as exc:  # the iframe is a best-effort extra hop
                    log.info("Kaltura iframe follow failed: %s", exc)
        if merged.get("partner_id"):
            self._config = {k: merged[k] for k in ("partner_id", "service_url", "ks", "uiconf_id")
                            if merged.get(k)}
        merged.setdefault("entry_id", ref.entry_id)
        self.last_diagnostic = (f"partner_id={'si' if merged.get('partner_id') else 'NO'}, "
                                f"ks={'si' if merged.get('ks') else 'no'}, "
                                f"service={'propria' if merged.get('service_url') else 'default'}")
        return merged

    @staticmethod
    def _merge_config(into: dict, html: str) -> None:
        for key, value in extract_player_config(html).items():
            into.setdefault(key, value)

    def probe(self, ref: KalturaRef) -> VideoOutcome:
        """Resolve one entry **without downloading** it, for the pre-export check. Reports whether the
        FFHS video platform can be opened and a playable source found, with a short redacted reason."""
        if not getattr(self.session, "available", True):
            return VideoOutcome("flagged", note=self.session.reason or "accesso ai video non disponibile")
        try:
            source = self.resolve(ref)
        except Exception as exc:
            return VideoOutcome("flagged", note=f"{exc} [{self.last_diagnostic}]")
        if source is None:
            return VideoOutcome("flagged", note=f"sorgente video non trovata [{self.last_diagnostic}]")
        return VideoOutcome("downloaded", note=f"sorgente {source.kind} pronta [{self.last_diagnostic}]")

    def resolve(self, ref: KalturaRef) -> MediaSource | None:
        cfg = self._launch_config(ref)
        partner = cfg.get("partner_id")
        if not partner:
            return None
        service = cfg.get("service_url", "")
        ks = cfg.get("ks", "")
        # 1) a single progressive MP4 (best when the entry allows it)
        prog = playmanifest_url(service, partner, ref.entry_id, "url", ks)
        if self.session.looks_like_media(prog):
            return MediaSource("progressive", prog, ".mp4")
        # 2) otherwise HLS
        hls = playmanifest_url(service, partner, ref.entry_id, "applehttp", ks)
        return MediaSource("hls", hls, ".mp4")

    # -- download ---------------------------------------------------------
    def download(self, ref: KalturaRef, dest: Path, progress: Callable[[int, int | None], None] | None = None
                 ) -> VideoOutcome:
        if not getattr(self.session, "available", True):
            return VideoOutcome("flagged", note=self.session.reason or "accesso ai video non disponibile")
        try:
            source = self.resolve(ref)
        except Exception as exc:  # resolution is best-effort; never break the export
            log.info("Kaltura resolve failed for %s: %s", ref.entry_id, exc)
            return VideoOutcome("flagged", note=f"risoluzione del video non riuscita ({exc})")
        if source is None:
            return VideoOutcome("flagged", note="video non risolvibile (accesso o piattaforma)")
        try:
            if source.kind == "progressive":
                size = self.session.stream(source.url, dest, progress)
                return VideoOutcome("downloaded", ".mp4", size)
            return self._download_hls(source.url, dest, progress)
        except Exception as exc:
            log.info("Kaltura download failed for %s: %s", ref.entry_id, exc)
            return VideoOutcome("flagged", note=f"download del video non riuscito ({exc})")

    def _download_hls(self, master_url: str, dest: Path, progress) -> VideoOutcome:
        text = self.session.get_text(master_url)
        media_url = master_url
        if is_master_playlist(text):
            variants = parse_master_playlist(text, master_url)
            if not variants:
                return VideoOutcome("flagged", note="playlist del video vuota")
            media_url = variants[-1][1]  # best quality
            text = self.session.get_text(media_url)
        init, segs = parse_media_playlist(text, media_url)
        if not segs:
            return VideoOutcome("flagged", note="nessun segmento video trovato")
        suffix = ".mp4" if init or segs[0].lower().split("?")[0].endswith((".mp4", ".m4s")) else ".ts"
        target = dest.with_suffix(suffix)
        written = self.session.concat(target, ([init] if init else []) + segs, progress)
        return VideoOutcome("downloaded", suffix, written)

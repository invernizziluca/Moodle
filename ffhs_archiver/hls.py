"""Download an unencrypted, finite Moodle HLS recording for offline playback.

Only the supplied Moodle client performs network operations. This does not
extract players, transfer browser cookies, decrypt DRM, or mux files into MP4.
The result is a local playlist plus all of its media and initialization files.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from . import fsutil
from .moodle import MoodleError
from .privacy import scrub_secrets


@dataclass(frozen=True)
class HLSResult:
    entrypoint: Path
    total_bytes: int
    segment_count: int


def _attributes(text: str) -> dict[str, str]:
    return {m.group(1).upper(): (m.group(2)[1:-1] if m.group(2).startswith('"') else m.group(2))
            for m in re.finditer(r'([A-Za-z0-9-]+)=("[^"\r\n]*"|[^,\r\n]*)', text)}


def _relative_url(base: str, reference: str) -> str:
    """Resolve relative media paths on both Moodle slash-argument configurations."""
    reference = reference.strip()
    ref = urlsplit(reference)
    parts = urlsplit(base)
    query = parse_qsl(parts.query, keep_blank_values=True)
    file_args = [v for k, v in query if k == "file"]
    if len(file_args) == 1 and not ref.scheme and not ref.netloc and not ref.path.startswith("/"):
        file_path = posixpath.normpath(posixpath.join(posixpath.dirname(file_args[0]), unquote(ref.path)))
        preserved = [(k, v) for k, v in query if k.lower() not in ("file", "token")]
        preserved += parse_qsl(ref.query, keep_blank_values=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode([("file", file_path), *preserved]), ""))
    return urljoin(base, reference)


def _error(message: str, code: str = "hlsunsupported") -> MoodleError:
    return MoodleError(message, code)


def download_hls(client, url: str, dest_dir: Path, *, max_bytes: int = 0,
                 progress=None) -> HLSResult:
    """Atomically publish one offline HLS VOD directory, or leave no new output.

    ``dest_dir`` must be absent; an existing recording is never overwritten.
    ``max_bytes`` limits the complete recording (manifests, init files, segments),
    and progress receives aggregate bytes plus the currently known lower bound.
    """
    dest_dir = Path(dest_dir)
    if dest_dir.exists():
        raise FileExistsError(f"La cartella video esiste già: {dest_dir.name}")
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    manifests_bytes = 0
    visited = set()

    def check_url(target: str) -> None:
        client.check_cancel()
        if not client.is_file_url(target):
            raise _error("Streaming HLS: una risorsa è esterna ai file Moodle autorizzati", "hlsexternal")

    def check_size(size: int) -> None:
        if max_bytes and size > max_bytes:
            raise _error("Streaming HLS più grande del limite impostato", "filetoolarge")

    # Resolve a master playlist before creating any local output.
    for _ in range(6):
        check_url(url)
        if url in visited:
            raise _error("Streaming HLS: ciclo tra le playlist")
        visited.add(url)
        text = client.fetch_text(url).lstrip("\ufeff")
        if len(text) > 2 * 1024 * 1024:
            raise _error("Playlist HLS eccessivamente grande")
        manifests_bytes += len(text.encode("utf-8"))
        check_size(manifests_bytes)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or lines[0] != "#EXTM3U":
            raise _error("Il server non ha restituito una playlist HLS valida")
        for line in lines:
            if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")) and \
                    _attributes(line.split(":", 1)[1]).get("METHOD") != "NONE":
                raise _error("Video HLS cifrato/DRM: questa versione non lo scarica", "hlsencrypted")
        variants = []
        pending = None
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF:"):
                pending = _attributes(line.split(":", 1)[1])
            elif not line.startswith("#") and pending is not None:
                try:
                    bandwidth = int(pending.get("BANDWIDTH", "0"))
                except ValueError:
                    bandwidth = 0
                variants.append((bandwidth, line, pending))
                pending = None
        if not variants:
            if pending is not None:
                raise _error("Playlist HLS master incompleta")
            break
        _, child, attrs = max(variants, key=lambda variant: variant[0])
        audio_group = attrs.get("AUDIO")
        if audio_group and any(line.startswith("#EXT-X-MEDIA:") and
                               (a := _attributes(line.split(":", 1)[1])).get("TYPE") == "AUDIO" and
                               a.get("GROUP-ID") == audio_group and a.get("URI") for line in lines):
            raise _error("Streaming HLS con traccia audio separata: download completo non supportato",
                         "hlsaudio")
        url = _relative_url(url, child)
    else:
        raise _error("Streaming HLS: troppe playlist annidate")

    if "#EXT-X-ENDLIST" not in lines:
        raise _error("Streaming HLS live o incompleto: manca la fine della registrazione", "hlslive")
    # Plan every URI first, so unsupported formats fail before downloading media.
    output = []
    assets = []
    by_url = {}
    segments = 0

    def asset(reference: str, initialization: bool = False) -> str:
        target = _relative_url(url, reference)
        check_url(target)
        if target in by_url:
            return by_url[target]
        suffix = Path(client.file_key(target)).suffix.lower()
        if suffix not in (".ts", ".m4s", ".mp4", ".aac", ".m4a", ".vtt", ".webvtt"):
            suffix = ".mp4" if initialization else ".ts"
        name = f"{'i' if initialization else 's'}{len(assets):05d}{suffix}"
        assets.append((target, name))
        by_url[target] = name
        return name

    for line in lines:
        if line.startswith("#EXT-X-BYTERANGE:"):
            raise _error("Streaming HLS con byte range: download completo non supportato", "hlsbyterange")
        if line.startswith("#EXT-X-MAP:"):
            attrs = _attributes(line.split(":", 1)[1])
            if attrs.get("BYTERANGE") or not attrs.get("URI"):
                raise _error("Inizializzazione HLS con byte range o senza URI non supportata")
            output.append(f'#EXT-X-MAP:URI="{asset(attrs["URI"], initialization=True)}"')
        elif line.startswith("#EXT-X-KEY:"):
            # METHOD=NONE was checked above. No remote key references are kept.
            continue
        elif not line.startswith("#"):
            segments += 1
            if segments > 10000:
                raise _error("Streaming HLS: numero di segmenti eccessivo")
            output.append(asset(line))
        elif "URI=" in line or line.startswith(("#EXT-X-PART", "#EXT-X-PRELOAD-HINT")):
            raise _error("Playlist HLS con risorse aggiuntive non supportate")
        else:
            output.append(line)
    if not segments:
        raise _error("La playlist HLS non contiene segmenti video")

    fsutil.ensure_dir(dest_dir.parent)
    staging = Path(tempfile.mkdtemp(prefix=".hls-", dir=fsutil.lp(dest_dir.parent)))
    completed = manifests_bytes
    try:
        for target, name in assets:
            check_url(target)

            def report(done, total):
                client.check_cancel()
                check_size(completed + max(done, total or 0))
                if progress:
                    progress(completed + done, completed + total if total else None)

            result = client.download(target, staging / name, progress=report)
            if result.status != "downloaded" or not fsutil.is_file(staging / name):
                raise _error("Segmento HLS non scaricato completamente")
            completed += fsutil.getsize(staging / name)
            check_size(completed)
        client.check_cancel()
        playlist = scrub_secrets("\n".join(output) + "\n")
        fsutil.write_text_atomic(staging / "video.m3u8", playlist)
        if dest_dir.exists():
            raise FileExistsError(f"La cartella video esiste già: {dest_dir.name}")
        os.rename(fsutil.lp(staging), fsutil.lp(dest_dir))
        return HLSResult(dest_dir / "video.m3u8", completed, segments)
    finally:
        if fsutil.exists(staging):
            shutil.rmtree(fsutil.lp(staging))

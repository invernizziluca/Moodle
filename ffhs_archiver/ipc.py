"""Local, authenticated, in-memory channel for the end of the browser login.

When the browser opens ``moodlemobile://token=…`` Windows starts a short-lived helper process
(this program with ``--sso-callback``). The helper hands the link to the program window that is
waiting for it, through a Windows *named pipe* (a Unix socket on other systems). Nothing is written
to disk.

Protection:
- the pipe name and a 256-bit key are random and new for every login attempt; both are known only to
  the waiting program and to the helper (via the temporary registry entry of the current user);
- before any link is sent, both sides prove they know the key (HMAC-SHA256 challenge/response in both
  directions): a process that does not know the key can neither receive nor inject a link;
- messages are length-limited bytes (no pickle), every read has a timeout, the channel closes as soon
  as the login ends or after 15 minutes.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Client, Listener
from pathlib import Path

PROTO = b"FMA1"
MAX_URL = 4096
TIMEOUT = 5.0
PIPE_PREFIX = r"\\.\pipe\FFHSMoodleArchiver-"


def _mac(key: bytes, label: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, label + b"".join(parts), "sha256").digest()


@dataclass(frozen=True)
class ChannelSpec:
    id: str
    key: bytes
    expires: int

    def encode(self) -> str:
        return f"{self.id}.{self.key.hex()}.{self.expires}"

    @classmethod
    def parse(cls, text: str) -> ChannelSpec:
        m = re.fullmatch(r"([0-9a-f]{16})\.([0-9a-f]{64})\.(\d{9,11})", (text or "").strip())
        if not m:
            raise ValueError("canale non valido")
        return cls(m.group(1), bytes.fromhex(m.group(2)), int(m.group(3)))

    @classmethod
    def new(cls, lifetime: float) -> ChannelSpec:
        return cls(secrets.token_hex(8), secrets.token_bytes(32), int(time.time() + lifetime))


def channel_address(channel_id: str, state_dir) -> tuple:
    if os.name == "nt":
        return PIPE_PREFIX + channel_id, "AF_PIPE"
    base = Path(state_dir) / "ipc"
    if len(str(base)) > 80:  # Unix socket paths are limited to ~100 characters
        base = Path(tempfile.gettempdir()) / f"fma-{os.getuid()}"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    return str(base / f"{channel_id}.sock"), "AF_UNIX"


class LoginChannel:
    """Server side, owned by the program window during one login attempt."""

    def __init__(self, state_dir, lifetime: float = 900) -> None:
        self.spec = ChannelSpec.new(lifetime)
        self.address, self.family = channel_address(self.spec.id, state_dir)
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.rejected = 0

    def start(self, on_message: Callable[[str], bool]) -> None:
        self._listener = Listener(self.address, self.family, backlog=8)
        self._thread = threading.Thread(target=self._serve, args=(on_message,), daemon=True,
                                        name="sso-channel")
        self._thread.start()

    def _serve(self, on_message) -> None:
        while not self._stop.is_set():
            try:
                conn = self._listener.accept()
            except (OSError, EOFError):
                if self._stop.is_set():
                    return
                time.sleep(0.05)
                continue
            with conn:
                if self._stop.is_set() or time.time() > self.spec.expires:
                    return
                try:
                    message = self._handshake(conn)
                except (OSError, EOFError, ValueError, UnicodeDecodeError):
                    message = None
                if message is None:
                    self.rejected += 1
                    continue
                try:
                    accepted = on_message(message) is not False
                except Exception:  # never let a UI error kill the channel
                    accepted = False
                try:
                    conn.send_bytes(b"OK" if accepted else b"NO")
                except OSError:
                    pass

    def _handshake(self, conn) -> str | None:
        key = self.spec.key
        server_nonce = secrets.token_bytes(32)
        conn.send_bytes(PROTO + server_nonce)
        if not conn.poll(TIMEOUT):
            return None
        data = conn.recv_bytes(128)
        if len(data) != 64:
            return None
        client_nonce, client_mac = data[:32], data[32:]
        if not hmac.compare_digest(client_mac, _mac(key, b"C", server_nonce, client_nonce)):
            return None
        conn.send_bytes(_mac(key, b"S", client_nonce, server_nonce))
        if not conn.poll(TIMEOUT):
            return None
        payload = conn.recv_bytes(MAX_URL + 8)
        if not payload.startswith(b"URL "):
            return None
        return payload[4:].decode("utf-8")

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._listener is not None:
            try:  # unblock accept()
                Client(self.address, self.family).close()
            except Exception:
                pass
            try:
                self._listener.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(2)
        if self.family == "AF_UNIX":
            try:
                os.unlink(self.address)
            except OSError:
                pass


def deliver(spec_text: str, url: str, state_dir, timeout: float = TIMEOUT) -> str:
    """Client side (the helper started by the browser). Returns ok / no-app / rejected / bad-peer / no-answer."""
    spec = ChannelSpec.parse(spec_text)
    if len(url.encode("utf-8")) > MAX_URL:
        return "rejected"
    address, family = channel_address(spec.id, state_dir)
    try:
        conn = Client(address, family)
    except (FileNotFoundError, ConnectionRefusedError):
        return "no-app"
    except OSError:
        return "no-app"
    with conn:
        try:
            if not conn.poll(timeout):
                return "no-answer"
            hello = conn.recv_bytes(64)
            if len(hello) != len(PROTO) + 32 or not hello.startswith(PROTO):
                return "bad-peer"
            server_nonce = hello[len(PROTO):]
            client_nonce = secrets.token_bytes(32)
            conn.send_bytes(client_nonce + _mac(spec.key, b"C", server_nonce, client_nonce))
            if not conn.poll(timeout):
                return "rejected"
            server_mac = conn.recv_bytes(64)
            if not hmac.compare_digest(server_mac, _mac(spec.key, b"S", client_nonce, server_nonce)):
                return "bad-peer"  # whoever answers does not know the key: the link is NOT sent
            conn.send_bytes(b"URL " + url.encode("utf-8"))
            if not conn.poll(timeout * 3):
                return "no-answer"
            return "ok" if conn.recv_bytes(16) == b"OK" else "rejected"
        except (EOFError, OSError):
            return "rejected"


def self_test(state_dir) -> str:
    """Round trip through a real pipe/socket, plus a wrong-key attempt that must be refused."""
    received = []
    channel = LoginChannel(state_dir, lifetime=60)
    channel.start(lambda msg: received.append(msg) or True)
    try:
        sample = "moodlemobile://token=c2VsZnRlc3Q="
        result = deliver(channel.spec.encode(), sample, state_dir)
        if result != "ok" or received != [sample]:
            raise OSError(f"consegna di prova non riuscita ({result})")
        forged = ChannelSpec(channel.spec.id, secrets.token_bytes(32), channel.spec.expires).encode()
        if deliver(forged, sample, state_dir) == "ok" or len(received) != 1:
            raise OSError("un messaggio con chiave errata è stato accettato")
    finally:
        channel.stop()
    kind = "named pipe di Windows" if os.name == "nt" else "socket locale"
    return f"{kind}: consegna autenticata riuscita, chiave errata rifiutata"

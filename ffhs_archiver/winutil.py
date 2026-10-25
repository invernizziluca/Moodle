"""Small OS helpers (mostly Windows): open folders, desktop shortcut, DPI awareness, single instance."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
ASSETS = Path(__file__).resolve().parent / "assets"


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def open_path(path) -> None:
    path = str(path)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - opens Explorer / default app
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        try:
            subprocess.Popen(["xdg-open", path])
        except OSError:
            webbrowser.open(Path(path).as_uri())


def launcher_command() -> tuple:
    """(target, arguments, workdir) to start the GUI without a console window."""
    root = Path(__file__).resolve().parent.parent
    if getattr(sys, "frozen", False):
        return sys.executable, "", str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    launcher = root / "FFHS Moodle Archiver.pyw"
    return str(pythonw if pythonw.exists() else exe), f'"{launcher}"', str(root)


def create_desktop_shortcut() -> str:
    if os.name != "nt":
        raise OSError("Il collegamento sul Desktop si può creare solo su Windows.")
    target, args, workdir = launcher_command()
    icon = ASSETS / "app.ico"

    def q(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$p=Join-Path $d 'FFHS Moodle Archiver.lnk';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
        f"$s.TargetPath={q(target)};$s.Arguments={q(args)};$s.WorkingDirectory={q(workdir)};"
        f"$s.IconLocation={q(str(icon) + ',0')};$s.Description='Esporta i corsi Moodle FFHS';"
        "$s.Save();Write-Output $p"
    )
    # A single inline command (no script file): Windows execution policies do not apply, nothing is bypassed.
    res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0:
        raise OSError(res.stderr.strip() or "PowerShell non ha creato il collegamento")
    return res.stdout.strip()


# --------------------------------------------------------------------------- single instance

HEARTBEAT_FILE = "instance_heartbeat.txt"


def heartbeat(state_dir) -> None:
    Path(state_dir, HEARTBEAT_FILE).write_text(str(time.time()), encoding="utf-8")


def app_running(state_dir, max_age: float = 12.0) -> bool:
    try:
        ts = float(Path(state_dir, HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return time.time() - ts < max_age


def stop_heartbeat(state_dir) -> None:
    try:
        Path(state_dir, HEARTBEAT_FILE).unlink()
    except OSError:
        pass

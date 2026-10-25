"""Double-click this file to start FFHS Moodle Archiver (no console window)."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _report_startup_error(text: str) -> None:
    """pythonw has no console: write the error to a file and show a native message box."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "FFHS Moodle Archiver")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "errore_avvio.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass
    if "tkinter" in text or "_tkinter" in text:
        message = ("Manca il componente grafico Tk di Python.\n\nApri «Installa applicazioni» di Windows → Python → "
                   "Modifica, e attiva «tcl/tk and IDLE». Poi riavvia il programma.")
    elif sys.version_info < (3, 10):
        message = "Serve Python 3.10 o più recente: https://www.python.org/downloads/"
    else:
        message = "Il programma non è riuscito ad avviarsi.\n\n" + text[-1200:]
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "FFHS Moodle Archiver", 0x10)
    except Exception:
        print(message, file=sys.stderr)


try:
    if sys.version_info < (3, 10):
        raise RuntimeError("Python version too old")
    from ffhs_archiver.cli import main

    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    import traceback

    _report_startup_error(traceback.format_exc())
    sys.exit(1)

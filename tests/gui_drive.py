"""Drive the real GUI end to end under Xvfb, the way it is used on Windows:
paste check -> «Accedi con FFHS-ID» -> (simulated) browser login -> helper process started with the
moodlemobile:// link -> login channel -> automatic «Verifica accesso» -> «Anteprima» -> export of one course.
Screenshots are taken at every step.

Usage: DISPLAY=:99 FFHS_ARCHIVER_HOME=<fresh dir> FFHS_ARCHIVER_DEV_SITE=http://127.0.0.1:8085 \\
       python tests/gui_drive.py <archive> <shots_dir>
"""

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from browser_sim import browser_login  # noqa: E402
from ffhs_archiver import gui  # noqa: E402
from ffhs_archiver.appdata import Settings  # noqa: E402

archive, shots = sys.argv[1], Path(sys.argv[2])
shots.mkdir(parents=True, exist_ok=True)
settings = Settings.load()
settings.archive_dir = archive
settings.save()

opened = []
webbrowser.open = lambda url, *a, **k: opened.append(url) or True
dialogs = []
messagebox.askyesno = lambda title, text, **k: dialogs.append(("askyesno", title, text)) or "comunque" in text
messagebox.showinfo = lambda title, text, **k: dialogs.append(("showinfo", title, text))
messagebox.showwarning = lambda title, text, **k: dialogs.append(("showwarning", title, text))
messagebox.showerror = lambda title, text, **k: dialogs.append(("showerror", title, text))


def shot(name: str, full: bool = False) -> None:
    root.update()
    time.sleep(0.4)
    root.update()
    crop = [] if full else ["-crop", f"{root.winfo_width()}x{root.winfo_height()}+0+0"]
    subprocess.run(["import", "-window", "root", *crop, str(shots / f"{name}.png")], check=False)
    print("screenshot", name, flush=True)


def wait_until(cond, timeout=60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.05)
    return False


def toplevels():
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]


root = tk.Tk()
root.geometry("1180x830+0+0")
app = gui.App(root)
assert wait_until(lambda: app.login_view.winfo_ismapped(), 10), "login view not shown"
shot("01_login")

# A pasted link without an ongoing login is refused.
app.paste_var.set("moodlemobile://token=" + "QUJD" * 30)
app._use_paste()
assert wait_until(lambda: "Prima clicca" in str(app.login_status.cget("text")) or
                  "valido" in str(app.login_status.cget("text")), 20)
print("paste without login:", app.login_status.cget("text"))
app.paste_var.set("")

app.btn_login.invoke()
assert wait_until(lambda: bool(opened) and app.session.attempt is not None, 20), "browser was not opened"
assert wait_until(lambda: app.wait_frame.winfo_ismapped(), 5)
shot("02_waiting_for_browser")
attempt = app.session.attempt
helper_exit = {}


def browser() -> None:
    link = browser_login(opened[-1], "luca", "Luca#2026test")
    # What Windows does when the browser opens moodlemobile://…: start the helper with the link.
    res = subprocess.run([sys.executable, "-I", str(ROOT / "FFHS Moodle Archiver.pyw"), "--sso-callback", link,
                          "--sso-channel", attempt.channel.spec.encode()], env=os.environ, timeout=60)
    helper_exit["code"] = res.returncode


t = threading.Thread(target=browser)
t.start()
assert wait_until(lambda: app.main_view.winfo_ismapped(), 60), "login did not complete"
t.join()
print("helper exit code:", helper_exit.get("code"), "| attempt cleared:", app.session.attempt is None)
assert wait_until(lambda: bool(toplevels()), 60), "access report not shown"
shot("03_verifica_accesso", full=True)
report_text = toplevels()[0].winfo_children()[0].winfo_children()[0].get("1.0", "end")
marks = [line.strip() for line in report_text.splitlines() if line.strip().startswith(("[OK]", "[!!]", "[NO]"))]
print("report:", " | ".join(marks)[:600])
for w in toplevels():
    w.destroy()
assert wait_until(lambda: bool(app.courses), 30)
shot("04_courses")

hrm = next(c for c in app.courses if "HRM4" in c["fullname"])
app.tree.focus(str(hrm["id"]))
app.tree.selection_set(str(hrm["id"]))
app._preview_course()
assert wait_until(lambda: bool(toplevels()), 60), "preview not shown"
shot("05_anteprima", full=True)
for w in toplevels():
    w.destroy()

app._toggle(str(hrm["id"]))
app.btn_export.invoke()
assert wait_until(lambda: app.busy, 5)
wait_until(lambda: app.item_prog["value"] > 0, 20)
shot("06_exporting")
assert wait_until(lambda: not app.busy, 180), "export did not finish"
shot("07_done")
print("dialogs:")
for d in dialogs:
    print("  ", d[0], "|", d[2].replace("\n", " / ")[:240])
app._on_close()
root.update()

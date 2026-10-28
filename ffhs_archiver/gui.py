"""Graphical interface (tkinter, Italian)."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, verify, winutil
from .appdata import Settings, app_dir
from .exporter import ExportOptions, Exporter, fmt_d, fmt_dt
from .moodle import MoodleError, RedactingFilter, TokenInvalidError, allowed_site
from .session import Session
from .sso import SSOError
from .state import ArchiveIndex
from .extension_dialog import open_extension_dialog

log = logging.getLogger(__name__)

RED = "#D50006"
RED_DARK = "#A80005"
INK = "#1f2328"
MUTED = "#5b6470"
OK_GREEN = "#1b7f3b"
WARN = "#a15c00"
ERR = "#b42318"


def setup_logging() -> Path:
    logdir = app_dir() / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(logdir / "app.log", maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return logdir


class ReportWindow:
    """Read-only text window for the access check and the course preview (copy / save on request)."""

    def __init__(self, app: App, title: str, text: str, filename: str) -> None:
        self.app = app
        self.text = text
        self.filename = filename
        win = tk.Toplevel(app.root)
        self.win = win
        win.title(f"{title} — {APP_NAME}")
        win.geometry("980x640")
        win.transient(app.root)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        frame = ttk.Frame(win, padding=(12, 12, 12, 6))
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        box = tk.Text(frame, wrap="word", font=app.font_mono, relief="flat", borderwidth=1, highlightthickness=1,
                      highlightbackground="#d0d4d9", background="white", foreground=INK, padx=10, pady=8)
        sb = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=sb.set)
        box.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        for tag, color in (("ok", OK_GREEN), ("warn", WARN), ("fail", ERR), ("head", INK)):
            box.tag_configure(tag, foreground=color)
        box.tag_configure("head", font=(app.font[0], 10, "bold"))
        plain = ("Esito", "Prossimo", "Solo ", "Moodle:", "Riepilogo", "Tipi di", "Confronta", "  ")
        for i, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if stripped.startswith("[OK]"):
                tag = "ok"
            elif stripped.startswith("[!!]"):
                tag = "warn"
            elif stripped.startswith("[NO]"):
                tag = "fail"
            elif i == 0 or (stripped and len(line) < 80 and not line.startswith(plain)):
                tag = "head"
            else:
                tag = ""
            box.insert("end", line + "\n", tag)
        box.configure(state="disabled")
        buttons = ttk.Frame(win, padding=(12, 0, 12, 12))
        buttons.grid(row=1, column=0, sticky="ew")
        ttk.Button(buttons, text="Copia negli appunti", command=self._copy).grid(row=0, column=0)
        ttk.Button(buttons, text="Salva come file…", command=self._save).grid(row=0, column=1, padx=8)
        ttk.Button(buttons, text="Chiudi", command=win.destroy).grid(row=0, column=2)
        win.bind("<Escape>", lambda e: win.destroy())

    def _copy(self) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(self.text)
        self.app.status_var.set("Rapporto copiato negli appunti.")

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".txt", initialfile=self.filename,
                                            filetypes=[("Testo", "*.txt"), ("Markdown", "*.md")])
        if path:
            Path(path).write_text(self.text, encoding="utf-8")
            self.app.status_var.set(f"Rapporto salvato: {path}")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = Settings.load()
        self.session = Session()
        self.events: queue.Queue = queue.Queue()
        self.client = None
        self.creds = None
        self.courses: list = []
        self.checked = {int(x) for x in self.settings.selected_courses}
        self.busy = False
        self.finishing_login = False
        self.cancel_event = threading.Event()
        self.closing = False
        self._last_bytes_update = 0.0

        root.title(APP_NAME)
        root.geometry(self.settings.window_geometry or "1160x830")
        root.minsize(980, 700)
        self._set_icon()
        self._setup_style()
        self.raw_var = tk.BooleanVar(value=bool(self.settings.save_raw_json))
        self.handler_var = tk.BooleanVar(value=bool(self.settings.use_protocol_handler))
        # The chosen extensions workflow needs only the ordinary mobile-app token.
        self.advanced_var = tk.BooleanVar(value=False)
        self._build_menu()
        self._build_header()
        self.body = ttk.Frame(root, padding=(16, 10, 16, 6))
        self.body.grid(row=1, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        self._build_statusbar()
        self.login_view = self._build_login_view(self.body)
        self.main_view = self._build_main_view(self.body)
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll_events)
        root.after(150, self._startup)
        root.after(300, self._heartbeat)

    # ------------------------------------------------------------------ look & feel
    def _set_icon(self) -> None:
        try:
            if os.name == "nt":
                self.root.iconbitmap(default=str(winutil.ASSETS / "app.ico"))
            else:
                self._icon = tk.PhotoImage(file=str(winutil.ASSETS / "app.png"))
                self.root.iconphoto(True, self._icon)
        except Exception:
            pass

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names() and os.name == "nt":
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        family = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
        self.font = (family, 10)
        self.font_small = (family, 9)
        self.font_bold = (family, 10, "bold")
        self.font_title = (family, 16, "bold")
        self.font_h2 = (family, 12, "bold")
        self.font_mono = ("Consolas", 10) if os.name == "nt" else ("DejaVu Sans Mono", 9)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                from tkinter import font as tkfont

                f = tkfont.nametofont(name)
                f.configure(family=family, size=10)
            except tk.TclError:
                pass
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=self.font_title, foreground=INK)
        style.configure("H2.TLabel", font=self.font_h2, foreground=INK)
        style.configure("Step.TLabel", font=self.font, foreground=INK)
        style.configure("Treeview", rowheight=26, font=self.font)
        style.configure("Treeview.Heading", font=self.font_bold)
        if style.theme_use() == "clam":
            style.configure("Horizontal.TProgressbar", background=RED, troughcolor="#e6e7e9", bordercolor="#d0d4d9",
                            lightcolor=RED, darkcolor=RED_DARK)

    def _make_check_images(self) -> None:
        """Checkbox images for the course list (fonts do not always contain ☐/☑)."""
        try:
            scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            scale = 1.0
        n = int(round(16 * scale))
        self._img_size = n

        def build(checked: bool) -> tk.PhotoImage:
            img = tk.PhotoImage(width=n, height=n)
            border, fill = ("#B5000A", RED) if checked else ("#8a929b", "#ffffff")
            rows = []
            for y in range(n):
                row = []
                for x in range(n):
                    edge = x in (1, n - 2) or y in (1, n - 2)
                    inside = 1 <= x <= n - 2 and 1 <= y <= n - 2
                    color = border if edge and inside else fill if inside else "#ffffff"
                    if checked and inside:
                        u, v = x / n, y / n
                        if (abs(v - (u + 0.18)) < 0.09 and 0.22 <= u <= 0.45) or \
                                (abs(v - (-1.25 * u + 1.25)) < 0.10 and 0.40 <= u <= 0.80):
                            color = "#ffffff"
                    row.append(color)
                rows.append("{" + " ".join(row) + "}")
            img.put(" ".join(rows), to=(0, 0))
            for x in range(n):
                for y in range(n):
                    if not (1 <= x <= n - 2 and 1 <= y <= n - 2):
                        try:
                            img.tk.call(img, "transparency", "set", x, y, 1)
                        except tk.TclError:
                            pass
            return img

        self._img_on = build(True)
        self._img_off = build(False)

    def _accent_button(self, parent, text: str, command, big: bool = False) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=RED, fg="white", activebackground=RED_DARK,
                         activeforeground="white", relief="flat", bd=0, cursor="hand2",
                         font=(self.font[0], 11 if big else 10, "bold"), padx=18 if big else 14,
                         pady=9 if big else 6, disabledforeground="#f4c7c9", highlightthickness=0)

    # ------------------------------------------------------------------ frame: menu, header, status
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar, tearoff=False)
        m_file.add_command(label="Apri cartella dell'archivio", command=self._open_archive)
        m_file.add_command(label="Crea collegamento sul Desktop", command=self._create_shortcut)
        m_file.add_separator()
        m_file.add_command(label="Esci", command=self._on_close)
        menubar.add_cascade(label="File", menu=m_file)
        m_tools = tk.Menu(menubar, tearoff=False)
        m_tools.add_command(label="Verifica accesso (senza download)", command=self._verify_access)
        m_tools.add_command(label="Anteprima del corso evidenziato (senza download)", command=self._preview_course)
        m_tools.add_command(label="Diagnostica del corso evidenziato…", command=self._diagnose)
        m_tools.add_separator()
        m_tools.add_command(label="Documenti con Moodle Buddy…", command=lambda: open_extension_dialog(self, "moodle_buddy"))
        m_tools.add_command(label="Video con FetchV…", command=lambda: open_extension_dialog(self, "fetchv"))
        m_tools.add_command(label="Apri cartella dei registri", command=lambda: winutil.open_path(app_dir() / "logs"))
        m_tools.add_separator()
        m_tools.add_checkbutton(label="Salva anche i dati tecnici grezzi (solo per diagnosi)",
                                variable=self.raw_var, command=self._save_settings)
        m_tools.add_command(label="Rimuovi un gestore moodlemobile:// rimasto", command=self._restore_handler)
        m_tools.add_separator()
        m_tools.add_command(label="Revoca l'accesso anche in Moodle (Sicherheitsschlüssel)…",
                            command=self._open_security_keys)
        m_tools.add_command(label="Disconnetti (rimuovi l'accesso salvato)", command=self._logout)
        menubar.add_cascade(label="Strumenti", menu=m_tools)
        m_help = tk.Menu(menubar, tearoff=False)
        m_help.add_command(label="Guida rapida", command=self._show_help)
        m_help.add_command(label="Informazioni", command=self._show_about)
        menubar.add_cascade(label="Aiuto", menu=m_help)
        self.root.config(menu=menubar)

    def _build_header(self) -> None:
        bar = tk.Frame(self.root, bg="white", highlightthickness=0)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        tk.Frame(bar, bg=RED, width=6).grid(row=0, column=0, rowspan=2, sticky="ns")
        tk.Label(bar, text=APP_NAME, bg="white", fg=INK, font=self.font_title, anchor="w").grid(
            row=0, column=1, sticky="w", padx=(14, 0), pady=(10, 0))
        tk.Label(bar, text="Archivio locale dei corsi Moodle FFHS: struttura, testi e documenti",
                 bg="white", fg=MUTED, font=self.font_small, anchor="w").grid(row=1, column=1, sticky="w",
                                                                              padx=(14, 0), pady=(0, 10))
        right = tk.Frame(bar, bg="white")
        right.grid(row=0, column=2, rowspan=2, sticky="e", padx=16)
        self.conn_dot = tk.Label(right, text="●", bg="white", fg="#9aa1a9", font=(self.font[0], 12))
        self.conn_dot.grid(row=0, column=0, padx=(0, 6))
        self.conn_label = tk.Label(right, text="Non connesso", bg="white", fg=MUTED, font=self.font)
        self.conn_label.grid(row=0, column=1)
        tk.Frame(self.root, bg="#e3e5e8", height=1).grid(row=0, column=0, sticky="sew")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(16, 2, 16, 6))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(bar, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(bar, text=f"v{__version__} · solo lettura · i dati restano su questo computer",
                  style="Muted.TLabel").grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------ login view
    def _build_login_view(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent)
        outer.columnconfigure(0, weight=1)
        card = ttk.Frame(outer, padding=(28, 14, 28, 14))
        card.grid(row=0, column=0, sticky="n", pady=(6, 0))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Accedi a Moodle FFHS", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        host = allowed_site().split("://", 1)[-1]
        ttk.Label(card, text=f"Sito: {host} (fisso). L'accesso avviene nel tuo browser, come con l'app Moodle: "
                             "password e Microsoft Authenticator restano nel browser, questo programma non li vede.",
                  style="Muted.TLabel", wraplength=660, justify="left").grid(row=1, column=0, sticky="w",
                                                                               pady=(4, 12))
        steps = [
            "1.  Clicca «Accedi con FFHS-ID»: si apre il browser sulla pagina ufficiale di Moodle.",
            "2.  Accedi con il tuo FFHS-ID e conferma con Microsoft Authenticator.",
            "3.  Quando il browser chiede di aprire un'applicazione (Python / FFHS Moodle Archiver), clicca «Apri».",
        ]
        for i, text in enumerate(steps):
            ttk.Label(card, text=text, style="Step.TLabel", wraplength=660, justify="left").grid(
                row=2 + i, column=0, sticky="w", pady=2)
        buttons = ttk.Frame(card)
        buttons.grid(row=5, column=0, sticky="w", pady=(14, 4))
        self.btn_login = self._accent_button(buttons, "Accedi con FFHS-ID", lambda: self._start_login("ffhs-id"),
                                             big=True)
        self.btn_login.grid(row=0, column=0)
        self.btn_login_other = ttk.Button(buttons, text="Altre opzioni (edu-ID, SWITCHaai…)",
                                          command=lambda: self._start_login("other"))
        self.btn_login_other.grid(row=0, column=1, padx=(12, 0))
        ttk.Checkbutton(card, text="Ricevi il link direttamente dal browser (consigliato)", variable=self.handler_var,
                        command=self._save_settings).grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Label(card, text="Per la durata dell'accesso (al massimo 15 minuti) il programma registra il link "
                             "moodlemobile:// solo per il tuo utente Windows e lo rimuove subito dopo, ripristinando "
                             "esattamente quanto c'era prima. Senza questa opzione il registro non viene toccato e "
                             "copi il link a mano.",
                  style="Muted.TLabel", wraplength=660, justify="left").grid(row=7, column=0, sticky="w", padx=(22, 0))
        ttk.Label(card, text="Dopo l'accesso, usa «Documenti con Moodle Buddy» e «Video con FetchV» "
                             "per raccogliere nell'archivio i download delle estensioni già installate nel browser.",
                  style="Muted.TLabel", wraplength=660, justify="left").grid(row=9, column=0, sticky="w", pady=(6, 0))
        self.login_status = ttk.Label(card, text="", wraplength=660, justify="left")
        self.login_status.grid(row=8, column=0, sticky="w", pady=(8, 0))
        self.wait_frame = ttk.Frame(card)
        self.wait_frame.grid(row=9, column=0, sticky="w", pady=(4, 0))
        self.wait_bar = ttk.Progressbar(self.wait_frame, mode="indeterminate", length=220)
        self.wait_bar.grid(row=0, column=0, padx=(0, 10))
        ttk.Button(self.wait_frame, text="Riapri il browser", command=self.session.reopen_browser).grid(
            row=0, column=1)
        ttk.Button(self.wait_frame, text="Annulla", command=self._cancel_login).grid(row=0, column=2, padx=(8, 0))
        self.wait_frame.grid_remove()

        ttk.Separator(card).grid(row=10, column=0, sticky="ew", pady=14)
        ttk.Label(card, text="Il browser non ha aperto il programma?", style="H2.TLabel").grid(
            row=11, column=0, sticky="w")
        ttk.Label(card, text="Dopo il login il browser mostra una pagina con un link («Klicken Sie hier…» / "
                             "«Click here…»): cliccalo una volta. Se ancora non succede nulla, fai clic destro sul "
                             "link → «Copia indirizzo link» e incollalo qui (vale solo per l'accesso avviato adesso). "
                             "In alternativa puoi incollare la chiave «Moodle mobile web service» di Moodle: "
                             "Profil → Einstellungen → Sicherheitsschlüssel.",
                  style="Muted.TLabel", wraplength=660, justify="left").grid(row=12, column=0, sticky="w",
                                                                               pady=(4, 8))
        paste = ttk.Frame(card)
        paste.grid(row=13, column=0, sticky="ew")
        paste.columnconfigure(0, weight=1)
        self.paste_var = tk.StringVar()
        entry = ttk.Entry(paste, textvariable=self.paste_var, width=70, show="•")
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda e: self._use_paste())
        ttk.Button(paste, text="Usa questo link", command=self._use_paste).grid(row=0, column=1, padx=(8, 0))
        ttk.Separator(card).grid(row=14, column=0, sticky="ew", pady=14)
        pre = ttk.Frame(card)
        pre.grid(row=15, column=0, sticky="w")
        ttk.Button(pre, text="Verifica questo computer e la connessione (senza accedere)",
                   command=self._verify_access).grid(row=0, column=0)
        ttk.Label(pre, text="  nessun dato personale, nessun download", style="Muted.TLabel").grid(row=0, column=1)
        return outer

    # ------------------------------------------------------------------ main view
    def _build_main_view(self, parent) -> ttk.Frame:
        view = ttk.Frame(parent)
        view.columnconfigure(0, weight=3)
        view.columnconfigure(1, weight=2)
        view.rowconfigure(1, weight=3)
        view.rowconfigure(2, weight=2)

        # first test: read-only checks
        check = ttk.LabelFrame(view, text=" Primo test sulla FFHS — nessun file viene scaricato ",
                               padding=(10, 6, 10, 8))
        check.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        check.columnconfigure(3, weight=1)
        self.btn_verify = self._accent_button(check, "1 · Verifica accesso", self._verify_access)
        self.btn_verify.grid(row=0, column=0)
        self.btn_preview = ttk.Button(check, text="2 · Anteprima del corso evidenziato", command=self._preview_course)
        self.btn_preview.grid(row=0, column=1, padx=(10, 0))
        ttk.Label(check, text="3 · poi esporta un solo corso e confrontalo con Moodle (file «verifica_corso.md»)",
                  style="Muted.TLabel").grid(row=0, column=2, padx=(12, 0), sticky="w")
        self.verify_label = ttk.Label(check, text="", style="Muted.TLabel")
        self.verify_label.grid(row=0, column=3, sticky="e")

        # courses
        courses = ttk.LabelFrame(view, text=" I tuoi corsi ", padding=(10, 8, 10, 10))
        courses.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        courses.columnconfigure(0, weight=1)
        courses.rowconfigure(1, weight=1)
        top = ttk.Frame(courses)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Cerca:").grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._fill_tree())
        ttk.Entry(top, textvariable=self.search_var).grid(row=0, column=1, sticky="ew", padx=(6, 8))
        ttk.Button(top, text="Aggiorna elenco", command=self._load_courses).grid(row=0, column=2)
        cols = ("name", "code", "period", "last")
        tree_frame = ttk.Frame(courses)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self._make_check_images()
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=self._img_size + 22, stretch=False, anchor="center", minwidth=30)
        for col, text, width, stretch in (("name", "Corso", 280, True), ("code", "Codice", 130, True),
                                          ("period", "Periodo", 150, False),
                                          ("last", "Ultima esportazione", 130, False)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w", minwidth=60)
        self.tree.tag_configure("odd", background="#f7f7f8")
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<space>", self._on_tree_space)
        bottom = ttk.Frame(courses)
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(bottom, text="Seleziona tutti", command=lambda: self._check_all(True)).grid(row=0, column=0)
        ttk.Button(bottom, text="Nessuno", command=lambda: self._check_all(False)).grid(row=0, column=1, padx=6)
        self.count_label = ttk.Label(bottom, text="", style="Muted.TLabel")
        self.count_label.grid(row=0, column=2, padx=(8, 0))

        # export settings
        side = ttk.LabelFrame(view, text=" Esportazione ", padding=(12, 8, 12, 12))
        side.grid(row=1, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        ttk.Label(side, text="Cartella dell'archivio").grid(row=0, column=0, sticky="w")
        dest = ttk.Frame(side)
        dest.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        dest.columnconfigure(0, weight=1)
        self.dest_var = tk.StringVar(value=self.settings.archive_dir)
        ttk.Entry(dest, textvariable=self.dest_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(dest, text="Sfoglia…", command=self._choose_dest).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(dest, text="Apri", command=self._open_archive).grid(row=0, column=2, padx=(6, 0))
        self.opt_vars = {}
        options = [
            ("include_forums", "Forum e discussioni"),
            ("anonymize_others", "   nascondi i nomi degli altri studenti"),
            ("include_quiz_reviews", "Quiz già svolti (domande e feedback)"),
            ("include_submissions", "Mie consegne e feedback"),
            ("include_videos", "File video caricati in Moodle"),
            ("keep_versions", "Conserva le versioni precedenti"),
            ("obsidian_ready", "Prepara la cartella per Obsidian"),
        ]
        for i, (key, label) in enumerate(options):
            var = tk.BooleanVar(value=bool(getattr(self.settings, key)))
            self.opt_vars[key] = var
            cb = ttk.Checkbutton(side, text=label, variable=var)
            cb.grid(row=2 + i, column=0, sticky="w", pady=1)
        ttk.Label(side, text="Le estensioni scaricano nel browser; il programma raccoglie e organizza i file.",
                  style="Muted.TLabel", wraplength=300).grid(row=2 + len(options), column=0, sticky="ew", pady=(5, 2))
        size = ttk.Frame(side)
        size.grid(row=3 + len(options), column=0, sticky="w", pady=(6, 0))
        ttk.Label(size, text="Limite per file:").grid(row=0, column=0)
        self.maxmb_var = tk.StringVar(value=str(self.settings.max_file_mb or 0))
        ttk.Spinbox(size, from_=0, to=10000, increment=50, width=6, textvariable=self.maxmb_var).grid(
            row=0, column=1, padx=6)
        ttk.Label(size, text="MB (0 = nessuno)", style="Muted.TLabel").grid(row=0, column=2)
        actions = ttk.Frame(side)
        actions.grid(row=4 + len(options), column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Documenti con Moodle Buddy…",
                   command=lambda: open_extension_dialog(self, "moodle_buddy")).grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="Video con FetchV…",
                   command=lambda: open_extension_dialog(self, "fetchv")).grid(row=1, column=0, sticky="ew", pady=(5, 8))
        self.btn_export = self._accent_button(actions, "Esporta struttura, testi e file API", lambda: self._export(False),
                                              big=True)
        self.btn_export.grid(row=2, column=0, sticky="ew")
        self.btn_export_all = ttk.Button(actions, text="Esporta tutti i corsi", command=lambda: self._export(True))
        self.btn_export_all.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.btn_cancel = ttk.Button(actions, text="Interrompi", command=self._cancel_export, state="disabled")
        self.btn_cancel.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        hint = ttk.Label(side, text="La prima volta scarica tutto; poi solo novità e modifiche. Se interrompi, "
                                    "la prossima esportazione riprende da dove si era fermata.",
                         style="Muted.TLabel", wraplength=300, justify="left")
        hint.grid(row=5 + len(options), column=0, sticky="ew", pady=(10, 0))
        side.bind("<Configure>", lambda e: hint.configure(wraplength=max(160, e.width - 30)))

        # progress + log
        prog = ttk.LabelFrame(view, text=" Avanzamento ", padding=(10, 6, 10, 10))
        prog.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        prog.columnconfigure(1, weight=1)
        prog.rowconfigure(2, weight=1)
        self.course_prog_label = ttk.Label(prog, text="—", width=52)
        self.course_prog_label.grid(row=0, column=0, sticky="w")
        self.course_prog = ttk.Progressbar(prog, mode="determinate")
        self.course_prog.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.item_prog_label = ttk.Label(prog, text="", width=52, style="Muted.TLabel")
        self.item_prog_label.grid(row=1, column=0, sticky="w", pady=(4, 6))
        self.item_prog = ttk.Progressbar(prog, mode="determinate")
        self.item_prog.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 6))
        log_frame = ttk.Frame(prog)
        log_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=7, wrap="word", font=self.font_mono, relief="flat",
                                borderwidth=1, highlightthickness=1, highlightbackground="#d0d4d9",
                                background="white", foreground=INK, state="disabled", padx=8, pady=6)
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lsb.grid(row=0, column=1, sticky="ns")
        for tag, color in (("info", INK), ("success", OK_GREEN), ("warning", WARN), ("error", ERR),
                           ("phase", MUTED), ("time", "#8a929b")):
            self.log_text.tag_configure(tag, foreground=color)
        tools = ttk.Frame(prog)
        tools.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(tools, text="Apri l'ultimo rapporto", command=self._open_last_report).grid(row=0, column=0)
        ttk.Button(tools, text="Diagnostica corso evidenziato", command=self._diagnose).grid(row=0, column=1, padx=6)
        return view

    # ------------------------------------------------------------------ helpers
    def _run_bg(self, fn, on_done=None, on_error=None) -> None:
        def target() -> None:
            try:
                result = fn()
            except Exception as exc:  # delivered to the UI thread
                if isinstance(exc, (SSOError, MoodleError)):
                    log.info("Operazione non riuscita: %s", exc)  # expected, already shown to the user
                else:
                    log.error("Background task failed\n%s", traceback.format_exc())
                self.events.put(("error", on_error, exc))
                return
            self.events.put(("call", on_done, result))

        threading.Thread(target=target, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, *payload = self.events.get_nowait()
                if kind == "call":
                    cb, result = payload
                    if cb:
                        cb(result)
                elif kind == "error":
                    cb, exc = payload
                    (cb or self._generic_error)(exc)
                elif kind == "export":
                    self._on_export_event(payload[0])
                elif kind == "sso_link":
                    self._on_link_received(payload[0])
        except queue.Empty:
            pass
        except Exception:
            log.error("UI event failed\n%s", traceback.format_exc())
        if not self.closing:
            self.root.after(80, self._poll_events)

    def _generic_error(self, exc: Exception) -> None:
        if isinstance(exc, TokenInvalidError):
            self._session_expired(str(exc))
            return
        self._log(f"Errore: {exc}", "error")
        messagebox.showerror(APP_NAME, str(exc), parent=self.root)

    def _log(self, message: str, level: str = "info") -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", time.strftime("%H:%M:%S  "), "time")
        self.log_text.insert("end", message + "\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if level in ("warning", "error"):
            log.warning(message)

    def _heartbeat(self) -> None:
        try:
            winutil.heartbeat(app_dir())
        except OSError:
            pass
        if not self.closing:
            self.root.after(4000, self._heartbeat)

    def _bring_to_front(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(400, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def _highlighted_course(self) -> dict | None:
        iid = self.tree.focus() or (self.tree.selection()[0] if self.tree.selection() else "")
        if not iid and len(self.checked) == 1:
            iid = str(next(iter(self.checked)))
        return next((c for c in self.courses if str(c["id"]) == str(iid)), None) if iid else None

    # ------------------------------------------------------------------ startup / connection
    def _startup(self) -> None:
        for message in self.session.cleanup_leftovers():
            self._log(f"Pulizia all'avvio: {message}.", "warning")
        creds = self.session.saved_credentials()
        if creds:
            self.creds = creds
            self.client = self.session.client_for(creds)
            self._set_connected(creds)
            self._show_main()
            self._load_courses()
        else:
            self._show_login()

    def _show_login(self) -> None:
        self.main_view.grid_remove()
        self.login_view.grid(row=0, column=0, sticky="nsew")

    def _show_main(self) -> None:
        self.login_view.grid_remove()
        self.main_view.grid(row=0, column=0, sticky="nsew")
        if self.settings.verified_at:
            self.verify_label.configure(text=f"Ultima verifica: {fmt_dt(self.settings.verified_at)}")

    def _set_connected(self, creds) -> None:
        host = allowed_site().split("://", 1)[-1]
        self.conn_dot.configure(fg=OK_GREEN)
        self.conn_label.configure(text=f"Connesso come {creds.fullname} · {host}", fg=INK)
        self.status_var.set("Connesso.")

    def _set_disconnected(self, text: str = "Non connesso") -> None:
        self.conn_dot.configure(fg="#9aa1a9")
        self.conn_label.configure(text=text, fg=MUTED)

    def _session_expired(self, message: str) -> None:
        self.client = None
        self.creds = None
        self._set_disconnected("Accesso scaduto")
        self._show_login()
        self.login_status.configure(text=message or "Accesso scaduto: accedi di nuovo.", foreground=WARN)

    # ------------------------------------------------------------------ login actions
    def _on_sso_link(self, url: str) -> bool:
        """Called by the login channel thread when the browser hands over the link."""
        self.events.put(("sso_link", url))
        return True

    def _on_link_received(self, url: str) -> None:
        if self.session.attempt is None or self.finishing_login:
            return  # duplicate click on the link, or the login already ended
        self._bring_to_front()
        self._finish_login(url, require_signature=True)

    def _start_login(self, method: str) -> None:
        self._save_settings()
        use_handler = bool(self.handler_var.get())
        self.btn_login.configure(state="disabled")
        self.login_status.configure(text="Apertura del browser…", foreground=INK)

        def done(attempt) -> None:
            self.btn_login.configure(state="normal")
            self.wait_frame.grid()
            self.wait_bar.start(12)
            if attempt.handler_registered:
                how = "clicca «Apri» quando il browser chiede di aprire il programma"
            elif attempt.channel is not None and os.name != "nt":
                how = "il link arriverà al programma tramite il canale locale"
            else:
                how = "copia il link della pagina finale e incollalo qui sotto"
                if attempt.handler_error:
                    self._log(f"Apertura automatica dal browser non disponibile ({attempt.handler_error}): "
                              "usa il link incollato.", "warning")
            self.login_status.configure(text=f"In attesa del login nel browser (massimo 15 minuti)… Dopo l'accesso "
                                             f"{how}.", foreground=INK)
            self.root.after(5000, self._check_login_expiry)

        def failed(exc: Exception) -> None:
            self.btn_login.configure(state="normal")
            self.login_status.configure(text=str(exc), foreground=ERR)

        self._run_bg(lambda: self.session.start_login(method=method, open_browser=True, use_handler=use_handler,
                                                      on_link=self._on_sso_link), done, failed)

    def _check_login_expiry(self) -> None:
        attempt = self.session.attempt
        if attempt is None or self.closing:
            return
        if attempt.expired:
            self._cancel_login(expired=True)
            return
        self.root.after(5000, self._check_login_expiry)

    def _cancel_login(self, expired: bool = False) -> None:
        self.wait_bar.stop()
        self.wait_frame.grid_remove()
        self.session.end_login()
        if expired:
            self.login_status.configure(text="Il tempo per l'accesso (15 minuti) è scaduto: clicca di nuovo "
                                             "«Accedi con FFHS-ID».", foreground=WARN)
        else:
            self.login_status.configure(text="Accesso annullato.", foreground=MUTED)

    def _use_paste(self) -> None:
        text = self.paste_var.get().strip()
        if not text:
            self.login_status.configure(text="Incolla prima il link «moodlemobile://token=…» o la chiave.",
                                        foreground=WARN)
            return
        self._finish_login(text, require_signature=False)

    def _finish_login(self, text: str, require_signature: bool) -> None:
        if self.finishing_login:
            return
        self.finishing_login = True
        self.login_status.configure(text="Verifica dell'accesso…", foreground=INK)

        def done(result) -> None:
            self.finishing_login = False
            creds, client = result
            self.creds, self.client = creds, client
            self.wait_bar.stop()
            self.wait_frame.grid_remove()
            self.paste_var.set("")
            self.login_status.configure(text="")
            self._set_connected(creds)
            self._show_main()
            protection = {"dpapi": "cifrato con DPAPI di Windows",
                          "memory": "NON salvato (dovrai accedere di nuovo al prossimo avvio)"}.get(
                creds.protection, "salvato con permessi riservati")
            self._log(f"Accesso riuscito: {creds.fullname} – {creds.sitename} (Moodle {creds.release}). "
                      f"Token {protection}; la password non è mai passata dal programma.", "success")
            self._bring_to_front()
            self._load_courses()
            if not self.settings.verified_at:
                self._verify_access()

        def failed(exc: Exception) -> None:
            self.finishing_login = False
            if self.session.attempt is None:
                self.wait_bar.stop()
                self.wait_frame.grid_remove()
            self.login_status.configure(text=str(exc), foreground=ERR)

        keep_private = bool(self.advanced_var.get())
        self._run_bg(lambda: self.session.finish_login(text, require_signature=require_signature,
                                                       keep_private_token=keep_private), done, failed)

    def _logout(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_NAME, "Attendi la fine dell'esportazione.", parent=self.root)
            return
        if not messagebox.askyesno(APP_NAME, "Rimuovere l'accesso salvato da questo computer?", parent=self.root):
            return
        self.session.logout()
        self.client = None
        self.creds = None
        self._set_disconnected()
        self._show_login()
        self._log("Accesso rimosso da questo computer.", "info")
        if messagebox.askyesno(APP_NAME, "Vuoi revocare il token anche in Moodle?\n\nSi apre la pagina "
                                         "«Sicherheitsschlüssel»: alla riga «Moodle mobile web service» clicca "
                                         "«Zurücksetzen».", parent=self.root):
            self._open_security_keys()

    def _open_security_keys(self) -> None:
        webbrowser.open(allowed_site() + "/user/managetoken.php")

    def _restore_handler(self) -> None:
        if self.session.attempt is not None:
            messagebox.showinfo(APP_NAME, "C'è un accesso in corso: annullalo prima.", parent=self.root)
            return
        try:
            result = self.session.registrar.cleanup_leftover()
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Operazione non riuscita: {exc}", parent=self.root)
            return
        text = {"restored": "Gestore temporaneo rimosso; ripristinato quello che c'era prima.",
                "removed": "Gestore temporaneo rimosso.",
                "clean": "Nessun gestore temporaneo presente: niente da fare.",
                "unsupported": "Funzione disponibile solo su Windows."}.get(result, result)
        self._log(text, "info")
        messagebox.showinfo(APP_NAME, text, parent=self.root)

    # ------------------------------------------------------------------ first tests (read-only)
    def _verify_access(self) -> None:
        self.btn_verify.configure(state="disabled")
        self.status_var.set("Verifica in corso (nessun download)…")
        client, creds = self.client, self.creds

        def done(report: str) -> None:
            self.btn_verify.configure(state="normal")
            failed = "problemi da risolvere" in report
            self._log("Verifica accesso completata" + (" con problemi." if failed else "."),
                      "error" if failed else "success")
            if client is not None and not failed:
                self.settings.verified_at = time.time()
                self._save_settings()
                self.verify_label.configure(text=f"Ultima verifica: {fmt_dt(self.settings.verified_at)}")
            self.status_var.set("Verifica completata.")
            ReportWindow(self, "Verifica accesso", report, f"verifica_accesso_{time.strftime('%Y-%m-%d_%H%M')}.txt")

        def failed(exc: Exception) -> None:
            self.btn_verify.configure(state="normal")
            self._generic_error(exc)

        self._run_bg(lambda: verify.access_report(app_dir(), client, creds), done, failed)

    def _preview_course(self) -> None:
        if not self.client:
            messagebox.showinfo(APP_NAME, "Prima accedi a Moodle.", parent=self.root)
            return
        course = self._highlighted_course()
        if course is None:
            messagebox.showinfo(APP_NAME, "Evidenzia un corso nell'elenco (un clic sul nome), poi riprova.",
                                parent=self.root)
            return
        self.status_var.set(f"Anteprima di «{course.get('fullname', '')}» (nessun download)…")
        client = self.client

        def done(text: str) -> None:
            self.status_var.set("Anteprima pronta.")
            self._log(f"Anteprima di «{course.get('fullname', '')}» pronta (nessun file scaricato).", "success")
            ReportWindow(self, f"Anteprima: {course.get('fullname', '')}", text,
                         f"anteprima_{course['id']}_{time.strftime('%Y-%m-%d_%H%M')}.txt")

        self._run_bg(lambda: verify.course_preview(client, course), done)

    # ------------------------------------------------------------------ courses
    def _load_courses(self) -> None:
        if not self.client:
            return
        self.status_var.set("Caricamento dei corsi…")
        exporter = Exporter(self.client, ExportOptions(dest=self.dest_var.get()))

        def done(courses) -> None:
            self.courses = courses
            known = {int(c["id"]) for c in courses}
            self.checked &= known
            self._fill_tree()
            self.status_var.set(f"{len(courses)} corsi trovati.")
            self._log(f"{len(courses)} corsi trovati nel tuo Moodle.", "info")

        def failed(exc: Exception) -> None:
            self.status_var.set("Elenco dei corsi non disponibile.")
            if isinstance(exc, TokenInvalidError):
                self._session_expired(str(exc))
            else:
                self._log(f"Impossibile leggere i corsi: {exc}", "error")

        self._run_bg(exporter.list_courses, done, failed)

    def _archive_index(self) -> dict:
        try:
            return ArchiveIndex(Path(self.dest_var.get())).courses
        except Exception:
            return {}

    def _fill_tree(self) -> None:
        query = self.search_var.get().strip().lower()
        index = self._archive_index()
        self.tree.delete(*self.tree.get_children())
        n = 0
        for c in self.courses:
            text = f"{c.get('fullname', '')} {c.get('shortname', '')}".lower()
            if query and query not in text:
                continue
            cid = int(c["id"])
            start, end = fmt_d(c.get("startdate")), fmt_d(c.get("enddate"))
            period = f"{start} – {end}" if start and end else start
            last = index.get(str(cid), {}).get("last_export")
            self.tree.insert("", "end", iid=str(cid), tags=("odd",) if n % 2 else (),
                             image=self._img_on if cid in self.checked else self._img_off,
                             values=(c.get("fullname", ""), c.get("shortname", ""), period,
                                     fmt_dt(last) if last else "—"))
            n += 1
        self._update_count()

    def _update_count(self) -> None:
        self.count_label.configure(text=f"{len(self.checked)} di {len(self.courses)} selezionati")
        self.settings.selected_courses = sorted(self.checked)

    def _toggle(self, iid: str) -> None:
        cid = int(iid)
        if cid in self.checked:
            self.checked.discard(cid)
        else:
            self.checked.add(cid)
        self.tree.item(iid, image=self._img_on if cid in self.checked else self._img_off)
        self._update_count()

    def _on_tree_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) in ("tree", "cell") and \
                self.tree.identify_column(event.x) == "#0":
            iid = self.tree.identify_row(event.y)
            if iid:
                self._toggle(iid)
                return "break"
        return None

    def _on_tree_double(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid and self.tree.identify_column(event.x) != "#0":
            self._toggle(iid)

    def _on_tree_space(self, event) -> str:
        for iid in self.tree.selection() or ((self.tree.focus(),) if self.tree.focus() else ()):
            self._toggle(iid)
        return "break"

    def _check_all(self, value: bool) -> None:
        visible = [int(i) for i in self.tree.get_children()]
        if value:
            self.checked |= set(visible)
        else:
            self.checked -= set(visible)
        for iid in self.tree.get_children():
            self.tree.item(iid, image=self._img_on if int(iid) in self.checked else self._img_off)
        self._update_count()

    # ------------------------------------------------------------------ export
    def _choose_dest(self) -> None:
        path = filedialog.askdirectory(parent=self.root, initialdir=self.dest_var.get() or str(Path.home()),
                                       title="Cartella dell'archivio")
        if path:
            self.dest_var.set(os.path.normpath(path))
            self._save_settings()
            self._fill_tree()

    def _open_archive(self) -> None:
        path = Path(self.dest_var.get() or self.settings.archive_dir)
        path.mkdir(parents=True, exist_ok=True)
        winutil.open_path(path)

    def _open_last_report(self) -> None:
        folder = Path(self.dest_var.get()) / "_registro"
        reports = sorted(folder.glob("*_esportazione.md")) if folder.exists() else []
        if not reports:
            messagebox.showinfo(APP_NAME, "Non c'è ancora nessun rapporto di esportazione.", parent=self.root)
            return
        winutil.open_path(reports[-1])

    def _options(self) -> ExportOptions:
        try:
            maxmb = max(0, int(float(self.maxmb_var.get() or 0)))
        except ValueError:
            maxmb = 0
        return ExportOptions(dest=self.dest_var.get().strip(), max_file_mb=maxmb,
                             save_raw_json=bool(self.raw_var.get()), private_token="",
                             download_videos=False, capture_pages=False,
                             **{k: bool(v.get()) for k, v in self.opt_vars.items()})

    def _save_settings(self) -> None:
        s = self.settings
        s.download_videos = False
        s.capture_pages = False
        if hasattr(self, "dest_var"):
            s.archive_dir = self.dest_var.get().strip() or s.archive_dir
            for k, v in self.opt_vars.items():
                setattr(s, k, bool(v.get()))
            try:
                s.max_file_mb = max(0, int(float(self.maxmb_var.get() or 0)))
            except ValueError:
                s.max_file_mb = 0
            s.selected_courses = sorted(self.checked)
        s.save_raw_json = bool(self.raw_var.get())
        s.use_protocol_handler = bool(self.handler_var.get())
        try:
            s.window_geometry = self.root.geometry()
        except tk.TclError:
            pass
        s.save()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_export.configure(state=state, bg="#e8a3a6" if busy else RED)
        self.btn_export_all.configure(state=state)
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        self.root.configure(cursor="watch" if busy else "")

    def _export(self, all_courses: bool) -> None:
        if self.busy or not self.client:
            return
        selected = self.courses if all_courses else [c for c in self.courses if int(c["id"]) in self.checked]
        if not selected:
            messagebox.showinfo(APP_NAME, "Seleziona almeno un corso (clic sulla casella accanto al nome).",
                                parent=self.root)
            return
        if not self.settings.verified_at and not messagebox.askyesno(
                APP_NAME, "Non hai ancora eseguito la «Verifica accesso» (consigliata come primo test, non scarica "
                          "nulla).\n\nEsportare comunque?", parent=self.root):
            return
        if len(selected) > 1 and not self._archive_index() and not messagebox.askyesno(
                APP_NAME, f"Prima esportazione: consigliamo di provare con un solo corso e confrontarlo con Moodle "
                          f"(file «verifica_corso.md»).\n\nEsportare comunque tutti i {len(selected)} corsi?",
                parent=self.root):
            return
        dest = self.dest_var.get().strip()
        if not dest:
            messagebox.showwarning(APP_NAME, "Scegli la cartella dell'archivio.", parent=self.root)
            return
        try:
            Path(dest).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"La cartella non è utilizzabile:\n{exc}", parent=self.root)
            return
        self._save_settings()
        self.cancel_event = threading.Event()
        exporter = Exporter(self.client, self._options(), emit=lambda ev: self.events.put(("export", ev)),
                            cancel_event=self.cancel_event)
        self._set_busy(True)
        self.course_prog.configure(maximum=len(selected), value=0)
        self.item_prog.configure(maximum=1, value=0)
        self._log(f"Esportazione di {len(selected)} corsi in {dest}", "info")
        self._run_bg(lambda: exporter.export(selected), self._export_done, self._export_failed)

    def _cancel_export(self) -> None:
        if self.busy:
            self.cancel_event.set()
            self.btn_cancel.configure(state="disabled")
            self.item_prog_label.configure(text="Interruzione… (termina il file in corso)")

    def _on_export_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "log":
            self._log(ev.get("message", ""), ev.get("level", "info"))
        elif t == "course_start":
            self.course_prog.configure(maximum=ev["total"], value=ev["index"] - 1)
            self.course_prog_label.configure(text=f"Corso {ev['index']}/{ev['total']}: {ev['name']}"[:70])
            self.status_var.set(f"Esportazione: {ev['name']}")
        elif t == "course_done":
            self.course_prog.configure(value=ev["index"])
        elif t == "phase":
            self.item_prog_label.configure(text=ev.get("message", "")[:70])
            self._log(ev.get("message", ""), "phase")
        elif t == "progress":
            scope = ev.get("scope")
            if scope in ("items", "files"):
                total = max(1, int(ev.get("total") or 1))
                self.item_prog.configure(maximum=total, value=int(ev.get("done") or 0))
                prefix = "Attività" if scope == "items" else "File"
                msg = ev.get("message") or ""
                self.item_prog_label.configure(text=f"{prefix} {min(total, int(ev.get('done') or 0) + 1)}/{total}: "
                                                    f"{msg}"[:70])
            elif scope == "bytes":
                now = time.monotonic()
                if now - self._last_bytes_update > 0.25 and ev.get("total"):
                    self._last_bytes_update = now
                    pct = int(100 * ev["done"] / max(1, ev["total"]))
                    self.status_var.set(f"Download: {ev.get('message', '')} – {pct}%")

    def _export_done(self, summary: dict) -> None:
        self._set_busy(False)
        done = not summary.get("stopped")
        self.item_prog_label.configure(text="Completato." if done else "Interrotto.")
        if done:
            self.item_prog.configure(value=self.item_prog["maximum"])
        self._fill_tree()
        results = summary.get("results", [])
        ok = [r for r in results if r.get("status") == "ok"]
        new = sum((r.get("counts") or {}).get("new", 0) for r in ok)
        upd = sum((r.get("counts") or {}).get("updated", 0) for r in ok)
        missing = sum(r.get("missing", 0) for r in ok)
        if summary.get("auth_error"):
            self._session_expired(summary.get("stopped", ""))
            messagebox.showwarning(APP_NAME, "L'accesso a Moodle è scaduto. Accedi di nuovo e riavvia "
                                             "l'esportazione: riprenderà da dove si era fermata.", parent=self.root)
            return
        if summary.get("stopped"):
            self.status_var.set("Esportazione interrotta.")
            messagebox.showwarning(APP_NAME, summary["stopped"], parent=self.root)
            return
        failed = [r for r in results if r.get("status") != "ok"]
        self.status_var.set(f"Esportazione completata: {len(ok)} corsi.")
        text = (f"Esportazione completata.\n\nCorsi: {len(ok)}\nFile nuovi: {new}\nFile aggiornati: {upd}\n"
                f"Elementi non esportabili (vedi elementi_mancanti.md): {missing}\n\n"
                "Per il confronto con Moodle apri «verifica_corso.md» nella cartella del corso.")
        if failed:
            text += "\n\nCorsi con errori:\n" + "\n".join(f"• {r['name']}: {r.get('error', '')}" for r in failed)
        if messagebox.askyesno(APP_NAME, text + "\n\nAprire la cartella dell'archivio?", parent=self.root):
            self._open_archive()

    def _export_failed(self, exc: Exception) -> None:
        self._set_busy(False)
        if isinstance(exc, TokenInvalidError):
            self._session_expired(str(exc))
            return
        self._log(f"Esportazione non riuscita: {exc}", "error")
        messagebox.showerror(APP_NAME, f"Esportazione non riuscita:\n{exc}", parent=self.root)

    # ------------------------------------------------------------------ tools
    def _diagnose(self) -> None:
        if not self.client:
            messagebox.showinfo(APP_NAME, "Prima accedi a Moodle.", parent=self.root)
            return
        course = self._highlighted_course()
        if course is None:
            messagebox.showinfo(APP_NAME, "Evidenzia un corso nell'elenco (un clic sul nome).", parent=self.root)
            return
        dest = Path(self.dest_var.get()) / "_registro"
        exporter = Exporter(self.client, self._options())

        def work() -> Path:
            data = exporter.diagnose(int(course["id"]))
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"diagnostica_{course['id']}_{time.strftime('%Y-%m-%d_%H%M')}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            return path

        def done(path: Path) -> None:
            self._log(f"Diagnostica salvata: {path}", "success")
            if messagebox.askyesno(APP_NAME, f"Diagnostica salvata in:\n{path}\n\nContiene solo la struttura del "
                                             "corso (titoli, tipi di attività, funzioni disponibili), non i testi "
                                             "né i file.\n\nAprire la cartella?", parent=self.root):
                winutil.open_path(path.parent)

        self._log(f"Diagnostica di «{course.get('fullname', '')}»…", "phase")
        self._run_bg(work, done)

    def _create_shortcut(self) -> None:
        def done(path: str) -> None:
            self._log(f"Collegamento creato: {path}", "success")
            messagebox.showinfo(APP_NAME, f"Collegamento creato sul Desktop:\n{path}", parent=self.root)

        self._run_bg(winutil.create_desktop_shortcut, done)

    def _show_help(self) -> None:
        text = (
            "Download con le estensioni già installate:\n"
            "Evidenzia un corso e premi «Documenti con Moodle Buddy» oppure «Video con FetchV». "
            "Scegli cartella e destinazione, apri Moodle, scarica usando l'estensione nel browser, "
            "poi torna qui: «2 · Cerca download» e «3 · Importa selezionati». "
            "Per i casi dubbi usa la scheda «File da collegare»: scegli l'attività e premi "
            "«Collega selezionati», senza riscaricare nulla. "
            "Puoi raccogliere anche file già scaricati. Gli originali vengono conservati.\n\n"
            "Primo utilizzo (consigliato):\n"
            "1. Accedi con FFHS-ID: si apre il browser, fai il login come sempre (anche Microsoft Authenticator). "
            "Quando il browser chiede di aprire il programma, clicca «Apri».\n"
            "2. «Verifica accesso»: controlla computer, accesso e corsi senza scaricare nulla.\n"
            "3. Evidenzia un corso e clicca «Anteprima»: struttura e documenti, ancora senza download.\n"
            "4. Spunta solo quel corso, esportalo e confronta «verifica_corso.md» con Moodle.\n\n"
            "Poi: spunta gli altri corsi ed esporta. Le esportazioni successive scaricano solo le novità; i file "
            "sostituiti finiscono in «_versioni_precedenti».\n\n"
            "Nell'archivio, per ogni corso: course_overview.md (struttura), course_fulltext.md (tutti i testi, "
            "ideale per l'AI), elementi_mancanti.md (cosa non è esportabile e cosa puoi fare), verifica_corso.md "
            "(confronto con Moodle).\n\n"
            "Il programma usa solo funzioni di lettura dell'API ufficiale dell'app Moodle, con i tuoi permessi: "
            "non modifica nulla in Moodle, non aggira restrizioni e comunica solo con moodle.ffhs.ch."
        )
        messagebox.showinfo("Guida rapida", text, parent=self.root)

    def _show_about(self) -> None:
        messagebox.showinfo("Informazioni", f"{APP_NAME} {__version__}\n\nPython {sys.version.split()[0]} · "
                                            f"Tk {tk.TkVersion}\nSito: {allowed_site()}\n"
                                            f"Dati del programma: {app_dir()}", parent=self.root)

    # ------------------------------------------------------------------ closing
    def _on_close(self) -> None:
        if getattr(self, "_importing", False):
            messagebox.showinfo(APP_NAME, "Attendi il completamento dell'importazione prima di chiudere.", parent=self.root)
            return
        if self.busy:
            if not messagebox.askyesno(APP_NAME, "È in corso un'esportazione. Interromperla e chiudere?\n\n"
                                                 "Alla prossima esportazione riprenderà da dove si è fermata.",
                                       parent=self.root):
                return
            self.cancel_event.set()
        self.closing = True
        try:
            self._save_settings()
        except Exception:
            pass
        try:
            self.session.end_login()  # closes the login channel and removes the temporary link handler
            winutil.stop_heartbeat(app_dir())
        except Exception:
            pass
        self.root.after(300 if self.busy else 0, self.root.destroy)


def run_gui() -> int:
    if sys.version_info < (3, 10):
        messagebox.showerror(APP_NAME, "Serve Python 3.10 o più recente.")
        return 1
    setup_logging()
    winutil.enable_dpi_awareness()
    if winutil.app_running(app_dir(), max_age=10):
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, "Il programma è già aperto.")
        root.destroy()
        return 0
    root = tk.Tk()

    def report(exc, val, tb) -> None:  # pythonw has no console: log and show unexpected UI errors
        text = "".join(traceback.format_exception(exc, val, tb))
        log.error("Errore nell'interfaccia\n%s", text)
        try:
            messagebox.showerror(APP_NAME, f"Errore inatteso: {val}\n\nDettagli nel registro:\n"
                                           f"{app_dir() / 'logs' / 'app.log'}", parent=root)
        except tk.TclError:
            pass

    root.report_callback_exception = report
    App(root)
    root.mainloop()
    return 0

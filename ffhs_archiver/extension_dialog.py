"""Guided use of the installed Moodle Buddy and FetchV extensions."""

from __future__ import annotations

import json
import webbrowser
from dataclasses import replace
from pathlib import Path, PurePosixPath
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, winutil
from .exporter import Exporter
from .extension_bridge import EXTENSIONS
from .extension_workflow import ExtensionWorkflow, course_targets, assign_buddy_files, list_unmatched_buddy_files
from .naming import human_size


def open_extension_dialog(app, tool: str) -> None:
    """Prepare missing local metadata, then show the actual extension workflow."""
    if tool not in EXTENSIONS:
        raise ValueError("Estensione non riconosciuta")
    if app.busy:
        messagebox.showinfo(APP_NAME, "Attendi il completamento dell'operazione in corso.", parent=app.root)
        return
    course = app._highlighted_course()
    if course is None:
        messagebox.showinfo(APP_NAME, "Evidenzia il corso nell'elenco prima di continuare.", parent=app.root)
        return
    archive = Path(app.dest_var.get()).expanduser()
    course_id = int(course["id"])
    try:
        index_path = archive / "courses.json"
        needs_structure = not index_path.exists()
        if not needs_structure:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            needs_structure = not any(str(row.get("id")) == str(course_id)
                                      for row in index.get("courses", []))
        if not needs_structure:
            targets = course_targets(archive, course_id)
            _ExtensionDialog(app, tool, course, archive, targets)
            return
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        messagebox.showerror(APP_NAME, f"Impossibile leggere la struttura locale del corso:\n{exc}", parent=app.root)
        return
    if not app.client:
        messagebox.showinfo(APP_NAME, "Accedi a Moodle per preparare struttura e testi del corso.", parent=app.root)
        return

    # A real API export supplies titles/sections and matching metadata. The
    # browser extensions will perform the document and video downloads later.
    options = replace(app._options(), download_files=False, download_videos=False,
                      capture_pages=False, private_token="")
    app.cancel_event.clear()
    app._set_busy(True)
    app._importing = True
    app.btn_cancel.configure(state="disabled")
    app._log(f"Preparazione della struttura per {EXTENSIONS[tool]['name']}: {course.get('fullname', '')}", "phase")

    def work():
        summary = Exporter(app.client, options, cancel_event=app.cancel_event).export([course])
        result = summary["results"][0] if summary.get("results") else {}
        if summary.get("stopped") or result.get("status") != "ok" or result.get("errors"):
            detail = summary.get("stopped") or result.get("error") or "; ".join(result.get("errors") or [])
            raise ValueError("Preparazione del corso incompleta: " + (detail or "controlla il rapporto"))
        return course_targets(archive, course_id)

    def done(targets):
        app._importing = False
        app._set_busy(False)
        _ExtensionDialog(app, tool, course, archive, targets)

    def failed(exc):
        app._importing = False
        app._set_busy(False)
        messagebox.showerror(APP_NAME, str(exc), parent=app.root)

    app._run_bg(work, done, failed)


class _ExtensionDialog:
    def __init__(self, app, tool, course, archive, targets):
        self.app, self.tool, self.course, self.archive = app, tool, course, archive
        self.workflow = None
        self.working = False
        self.items = []
        self.pending = []
        self.report = None
        self.targets = [target for target in targets if tool != "fetchv" or not target["youtube_only"]]
        self.name = EXTENSIONS[tool]["name"]
        if tool == "fetchv" and not self.targets:
            messagebox.showinfo(APP_NAME, "Non ci sono attività locali da scegliere per FetchV. "
                                "YouTube rimane soltanto un link.", parent=app.root)
            return
        self.window = tk.Toplevel(app.root)
        self.window.title(f"Download con {self.name}")
        self.window.geometry("960x740")
        self.window.minsize(760, 600)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)
        ttk.Label(frame, text=f"{self.name} · {course.get('fullname') or course['id']}",
                  wraplength=790, font=(app.font[0], 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        instructions = (
            "Apri Moodle e scarica con " + self.name + ". Quando i download sono terminati (Ctrl+J), "
            "cerca i file e importa quelli del corso. Gli originali restano in Download."
        )
        ttk.Label(frame, text=instructions, wraplength=790, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        self.activity = ttk.Combobox(frame, state="readonly", width=55,
            values=[a.get("label", a["title"]) for a in self.targets])
        if tool == "fetchv":
            ttk.Label(frame, text="Attività del video:").grid(row=2, column=0, sticky="w")
            self.activity.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
            self.activity.current(0)
        else:
            # Documents are matched automatically. Remaining associations are
            # resolved directly in the second tab, without another collection.
            self.activity.configure(values=["Automatico"])
            self.activity.current(0)
        ttk.Label(frame, text="Cartella download:").grid(row=3, column=0, sticky="w")
        saved = getattr(app.settings, "extension_download_dir", "")
        default = Path.home() / "Downloads"
        self.folder = tk.StringVar(value=saved or str(default if default.is_dir() else Path.home()))
        self.folder_entry = ttk.Entry(frame, textvariable=self.folder)
        self.folder_entry.grid(row=3, column=1, sticky="ew", pady=4, padx=(0, 6))
        self.browse = ttk.Button(frame, text="Sfoglia…", command=self.choose_folder)
        self.browse.grid(row=3, column=2)
        ttk.Label(frame, text="Browser:").grid(row=4, column=0, sticky="w")
        self.browser = ttk.Combobox(frame, state="readonly", values=["Edge", "Browser predefinito"])
        self.browser.grid(row=4, column=1, sticky="w", pady=4)
        self.browser.current(0 if getattr(app.settings, "extension_browser", "edge") == "edge" else 1)
        ttk.Button(frame, text="Pagina ufficiale dell'estensione", command=self.open_store).grid(row=4, column=2)
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        self.start = ttk.Button(buttons, text="1 · Apri Moodle", command=self.begin)
        self.start.pack(side="left")
        self.scan = ttk.Button(buttons, text="2 · Cerca download", command=self.preview, state="disabled")
        self.scan.pack(side="left", padx=6)
        self.existing = ttk.Button(buttons, text="File già scaricati…", command=self.existing_files)
        self.existing.pack(side="left")
        ttk.Label(frame, text="Usa l'icona dell'estensione nella pagina Moodle aperta. "
                  "I file già importati non devono essere scaricati di nuovo.",
                  wraplength=790).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.status = tk.StringVar(value="Scegli una cartella e apri Moodle, oppure seleziona download già presenti.")
        ttk.Label(frame, textvariable=self.status, wraplength=790).grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.tabs = ttk.Notebook(frame)
        self.tabs.grid(row=8, column=0, columnspan=3, sticky="nsew")
        tree_frame = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(tree_frame, text="Download da importare")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("file", "size"), show="headings", selectmode="extended", height=9)
        self.tree.heading("file", text="Nome del file (gli ZIP possono contenere più documenti)")
        self.tree.heading("size", text="Dimensione")
        self.tree.column("file", width=600, minwidth=240)
        self.tree.column("size", width=110, minwidth=75, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=xscroll.set)
        self.file_detail = tk.StringVar(value="Controlla la selezione: deseleziona i file estranei al corso.")
        ttk.Label(tree_frame, textvariable=self.file_detail, wraplength=820).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=6)
        self.tree.bind("<<TreeviewSelect>>", self._selected_download)
        self.pending_frame = ttk.Frame(self.tabs, padding=6)
        self.pending_frame.columnconfigure(0, weight=1)
        self.pending_frame.rowconfigure(1, weight=1)
        self.tabs.add(self.pending_frame, text="File da collegare (0)")
        ttk.Label(self.pending_frame, text="Questi file sono già salvati nel corso. Selezionali, scegli "
                  "l'attività e premi Collega. Puoi sistemarli anche in un secondo momento.",
                  wraplength=820).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.pending_tree = ttk.Treeview(self.pending_frame, columns=("file", "context"),
                                        show="headings", selectmode="extended", height=6)
        self.pending_tree.heading("file", text="File già conservato")
        self.pending_tree.heading("context", text="Cartella originale")
        self.pending_tree.column("file", width=340, minwidth=160)
        self.pending_tree.column("context", width=450, minwidth=160)
        self.pending_tree.grid(row=1, column=0, sticky="nsew")
        pscroll = ttk.Scrollbar(self.pending_frame, orient="vertical", command=self.pending_tree.yview)
        pscroll.grid(row=1, column=1, sticky="ns")
        self.pending_tree.configure(yscrollcommand=pscroll.set)
        pxscroll = ttk.Scrollbar(self.pending_frame, orient="horizontal", command=self.pending_tree.xview)
        pxscroll.grid(row=2, column=0, sticky="ew")
        self.pending_tree.configure(xscrollcommand=pxscroll.set)
        self.pending_detail = tk.StringVar(value="Nessun collegamento da scegliere.")
        ttk.Label(self.pending_frame, textvariable=self.pending_detail, wraplength=820).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=6)
        self.pending_tree.bind("<<TreeviewSelect>>", self._selected_pending)
        association = ttk.Frame(self.pending_frame)
        association.grid(row=4, column=0, columnspan=2, sticky="ew")
        association.columnconfigure(1, weight=1)
        ttk.Label(association, text="Attività:").grid(row=0, column=0, padx=(0, 6))
        self.pending_activity = ttk.Combobox(association, state="readonly",
                           values=[a.get("label", a["title"]) for a in self.targets])
        self.pending_activity.grid(row=0, column=1, sticky="ew")
        self.pending_activity.bind("<<ComboboxSelected>>", self._selected_pending)
        self.assign_button = ttk.Button(association, text="Collega selezionati", command=self.assign, state="disabled")
        self.assign_button.grid(row=0, column=2, padx=(6, 0))
        bottom = ttk.Frame(frame)
        bottom.grid(row=10, column=0, columnspan=3, sticky="ew")
        self.import_button = ttk.Button(bottom, text="3 · Importa selezionati", command=self.collect, state="disabled")
        self.import_button.pack(side="left")
        self.report_button = ttk.Button(bottom, text="Apri rapporto", command=self.open_report, state="disabled")
        self.report_button.pack(side="left", padx=8)
        ttk.Button(bottom, text="Chiudi", command=self.close).pack(side="right")
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        if tool == "fetchv":
            self.tabs.hide(self.pending_frame)
        else:
            self.window.after_idle(self.load_pending)

    def choose_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.folder.get(), parent=self.window)
        if chosen:
            self.folder.set(chosen)

    def open_store(self):
        metadata = EXTENSIONS[self.tool]
        url = metadata.get("edge_store", metadata["store"]) if self.browser.current() == 0 else metadata["store"]
        webbrowser.open(url, new=2)

    def _workflow_arguments(self):
        index = self.activity.current()
        key = (self.targets[index]["key"] if self.tool == "fetchv" else
               self.targets[index - 1]["key"] if index > 0 else None)
        # Read Tk variables only on the UI thread; folder scanning happens in
        # the worker after these plain values have been captured.
        return {"archive_root": self.archive, "course_id": int(self.course["id"]),
                "tool": self.tool, "download_dir": self.folder.get(), "activity_key": key}

    def _freeze_scope(self):
        self.folder_entry.configure(state="disabled")
        self.browse.configure(state="disabled")
        self.activity.configure(state="disabled")
        self.browser.configure(state="disabled")

    def _tab_changed(self, event=None):
        importing = self.tabs.select() == str(self.tree.master)
        if importing:
            if not self.import_button.winfo_manager():
                self.import_button.pack(side="left", before=self.report_button)
        else:
            self.import_button.pack_forget()
        self.import_button.configure(state="normal" if self.items and not self.working else "disabled")

    def _selected_download(self, event=None):
        selected = self.tree.selection()
        if selected:
            candidate = self.items[int(selected[0])]
            self.file_detail.set("Percorso completo: " + candidate.relative_path)

    def _selected_pending(self, event=None):
        selected = self.pending_tree.selection()
        ready = bool(selected) and self.pending_activity.current() >= 0 and not self.working
        self.assign_button.configure(state="normal" if ready else "disabled")
        if selected:
            self.pending_detail.set("Percorso originale: " + self.pending[int(selected[0])]["original"])

    def _show_pending(self, pending, *, reveal=False):
        self.pending = pending
        self.pending_tree.delete(*self.pending_tree.get_children())
        for index, item in enumerate(pending):
            original = PurePosixPath(item["original"])
            self.pending_tree.insert("", "end", iid=str(index), values=(item["name"], str(original.parent)))
        self.tabs.tab(self.pending_frame, text=f"File da collegare ({len(pending)})")
        self.pending_detail.set("Scegli uno o più file e la loro attività." if pending else
                                "Tutti i file importati hanno un'attività di destinazione.")
        self._selected_pending()
        if pending and reveal:
            self.tabs.select(self.pending_frame)

    def load_pending(self):
        def done(pending):
            self._show_pending(pending, reveal=True)
            if pending:
                self.status.set(f"{len(pending)} collegamenti da scegliere per file già conservati nel corso. "
                                "Puoi sistemarli qui oppure importare altri download.")
        self._background(lambda: list_unmatched_buddy_files(self.archive, int(self.course["id"])), done)

    def assign(self):
        selected = self.pending_tree.selection()
        index = self.pending_activity.current()
        if not selected or index < 0:
            return
        identifiers = [self.pending[int(item)]["id"] for item in selected]
        activity = self.targets[index]["key"]

        def work():
            result = assign_buddy_files(self.archive, int(self.course["id"]), identifiers, activity)
            return result, list_unmatched_buddy_files(self.archive, int(self.course["id"]))

        def done(data):
            result, pending = data
            self.report = result["report_path"]
            self.report_button.configure(state="normal")
            self._show_pending(pending)
            self.status.set(f"Collegamenti aggiornati: {result['assigned']}. "
                            f"Restano {len(pending)} collegamenti da scegliere. Nessun file è stato ricopiato.")
            self.app._log(f"Collegamenti Moodle Buddy aggiornati: {self.report}", "success")
            if result.get("index_warning"):
                self.app._log("Collegamenti salvati; indice non aggiornato: " + result["index_warning"], "warning")
        self._background(work, done)

    def _save_preferences(self):
        self.app.settings.extension_download_dir = self.folder.get()
        self.app.settings.extension_browser = "edge" if self.browser.current() == 0 else "default"
        try:
            self.app.settings.save()
        except OSError as exc:
            self.app._log(f"Preferenze della cartella download non salvate: {exc}", "warning")

    def _background(self, work, done):
        if self.working or self.app.busy:
            messagebox.showinfo(APP_NAME, "Attendi il completamento dell'operazione in corso.", parent=self.window)
            return
        self.working = True
        self.app._importing = True
        self.app._set_busy(True)
        self.app.btn_cancel.configure(state="disabled")
        self.import_button.configure(state="disabled")
        self.scan.configure(state="disabled")
        self.existing.configure(state="disabled")
        self.start.configure(state="disabled")
        self.assign_button.configure(state="disabled")

        def finish():
            self.working = False
            self.app._importing = False
            self.app._set_busy(False)
            self.existing.configure(state="normal")
            self.scan.configure(state="normal" if self.workflow else "disabled")
            self.start.configure(state="normal")
            self.import_button.configure(state="normal" if self.items else "disabled")
            self._selected_pending()

        def success(result):
            finish()
            done(result)

        def failed(exc):
            finish()
            self.status.set("Operazione non completata. Controlla il motivo e riprova.")
            messagebox.showerror(APP_NAME, str(exc), parent=self.window)

        self.app._run_bg(work, success, failed)

    def begin(self):
        browser = "edge" if self.browser.current() == 0 else "default"
        arguments = self._workflow_arguments()
        self._save_preferences()

        def work():
            workflow = self.workflow or ExtensionWorkflow(**arguments)
            workflow.open(browser)
            return workflow

        def done(workflow):
            self.workflow = workflow
            self._freeze_scope()
            self.scan.configure(state="normal")
            self.status.set(f"Pagina Moodle aperta. Usa {self.name}, attendi la fine e poi cerca i file qui.")
        self._background(work, done)

    def _show_candidates(self, candidates):
        self.items = candidates
        self.tree.delete(*self.tree.get_children())
        for index, candidate in enumerate(candidates):
            self.tree.insert("", "end", iid=str(index), values=(candidate.path.name, human_size(candidate.size)))
        if candidates:
            self.tree.selection_set(*self.tree.get_children())
        self.import_button.configure(state="normal" if candidates else "disabled")
        self.tabs.select(self.tree.master)
        self.status.set(f"{len(candidates)} file trovati. Controlla la selezione e importa soltanto i materiali del corso."
                        if candidates else "Nessun nuovo file trovato. Verifica la cartella usata dal browser oppure scegli File già scaricati.")

    def preview(self):
        if self.workflow:
            self._background(self.workflow.preview, self._show_candidates)

    def existing_files(self):
        sources = filedialog.askopenfilenames(title=f"Scegli i download completati di {self.name}",
                                              initialdir=self.folder.get(), parent=self.window)
        if not sources:
            return
        arguments = self._workflow_arguments()
        current_workflow = self.workflow
        self._save_preferences()

        def work():
            workflow = current_workflow or ExtensionWorkflow(**arguments)
            candidates = workflow.review_existing(sources)
            return workflow, candidates

        def done(result):
            self.workflow, candidates = result
            self._freeze_scope()
            self.scan.configure(state="normal")
            self._show_candidates(candidates)
        self._background(work, done)

    def collect(self):
        selected = [self.items[int(index)] for index in self.tree.selection()]
        if not selected:
            messagebox.showinfo(APP_NAME, "Seleziona almeno un file completato da importare.", parent=self.window)
            return

        def work():
            result = self.workflow.collect(selected)
            pending = []
            if self.tool == "moodle_buddy":
                try:
                    pending = list_unmatched_buddy_files(self.archive, int(self.course["id"]))
                except (OSError, ValueError) as exc:
                    result["pending_warning"] = str(exc)
            return result, pending

        def done(data):
            result, pending = data
            self.report = result["report_path"]
            self.report_button.configure(state="normal")
            self.items = []
            self.tree.delete(*self.tree.get_children())
            self.import_button.configure(state="disabled")
            self.file_detail.set("Importazione completata. Puoi cercare altri download senza riaprire questa finestra.")
            self._show_pending(pending, reveal=True)
            counts = (f"{len(result['files'])} file conservati: {result.get('new_files', 0)} nuovi, "
                      f"{result.get('reused_files', 0)} già presenti. ")
            detail = (f"{len(pending)} collegamenti da scegliere nella scheda qui sotto."
                      if pending else "Tutti collegati alle attività. Gli originali restano in Download.")
            if result.get("pending_warning"):
                detail = "File salvati; impossibile aggiornare l'elenco dei collegamenti. Consulta il rapporto."
                self.app._log(detail + " " + result["pending_warning"], "warning")
            self.status.set(counts + detail)
            self.app._log(f"Importazione {self.name}: {self.report}", "success")
            if result.get("index_warning"):
                self.app._log("Importazione riuscita; indice estensioni non aggiornato: " + result["index_warning"], "warning")
        self._background(work, done)

    def open_report(self):
        if self.report:
            winutil.open_path(self.report)

    def close(self):
        if self.working:
            messagebox.showinfo(APP_NAME, "Attendi il completamento della copia prima di chiudere.", parent=self.window)
            return
        self.window.destroy()

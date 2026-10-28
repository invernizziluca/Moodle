"""Guided use of the installed Moodle Buddy and FetchV extensions."""

from __future__ import annotations

import json
import webbrowser
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, winutil
from .exporter import Exporter
from .extension_bridge import EXTENSIONS
from .extension_workflow import ExtensionWorkflow, course_targets
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
        self.report = None
        self.targets = [target for target in targets if not target["youtube_only"]]
        self.name = EXTENSIONS[tool]["name"]
        if tool == "fetchv" and not self.targets:
            messagebox.showinfo(APP_NAME, "Non ci sono attività locali da scegliere per FetchV. "
                                "YouTube rimane soltanto un link.", parent=app.root)
            return
        self.window = tk.Toplevel(app.root)
        self.window.title(f"Download con {self.name}")
        self.window.geometry("860x690")
        self.window.minsize(660, 590)
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
            "1. Scegli la cartella in cui il browser salva i download.\n"
            "2. Premi Apri Moodle, poi usa l'icona di " + self.name + " nella vera pagina del corso.\n"
            "3. Attendi la fine dei download nel browser (Ctrl+J), torna qui e cerca i file.\n"
            "4. Controlla la selezione e importa: gli originali restano nella cartella Download."
        )
        ttk.Label(frame, text=instructions, wraplength=790, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        ttk.Label(frame, text="Attività del video:" if tool == "fetchv" else "Destinazione:").grid(
            row=2, column=0, sticky="w")
        self.activity = ttk.Combobox(frame, state="readonly", width=55,
            values=[f"{a['key']} · {a['title']}" for a in self.targets])
        if tool == "fetchv":
            self.activity.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
            self.activity.current(0)
        else:
            self.activity.configure(values=["Automatico: nomi di file esatti e univoci"] +
                                    [f"{a['key']} · {a['title']}" for a in self.targets])
            self.activity.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
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
        self.start = ttk.Button(buttons, text=f"Apri Moodle per {self.name}", command=self.begin)
        self.start.pack(side="left")
        self.scan = ttk.Button(buttons, text="Download terminati: cerca file", command=self.preview, state="disabled")
        self.scan.pack(side="left", padx=6)
        self.existing = ttk.Button(buttons, text="File già scaricati…", command=self.existing_files)
        self.existing.pack(side="left")
        ttk.Label(frame, text="Le estensioni funzionano nella pagina Moodle, non in edge://newtab. "
                  "Il programma non avvia i loro pulsanti di download automaticamente.",
                  wraplength=790).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.status = tk.StringVar(value="Scegli una cartella e apri Moodle, oppure seleziona download già presenti.")
        ttk.Label(frame, textvariable=self.status, wraplength=790).grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=8, column=0, columnspan=3, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("file", "size"), show="headings", selectmode="extended", height=9)
        self.tree.heading("file", text="File da controllare prima dell'importazione")
        self.tree.heading("size", text="Dimensione")
        self.tree.column("file", width=600, minwidth=240)
        self.tree.column("size", width=110, minwidth=75, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        ttk.Label(frame, text="Deseleziona i file estranei al corso. La presenza nell'elenco non prova "
                  "che il download sia concluso: controlla Ctrl+J nel browser.", wraplength=790).grid(
            row=9, column=0, columnspan=3, sticky="ew", pady=7)
        bottom = ttk.Frame(frame)
        bottom.grid(row=10, column=0, columnspan=3, sticky="ew")
        self.import_button = ttk.Button(bottom, text="Importa selezionati nell'archivio", command=self.collect, state="disabled")
        self.import_button.pack(side="left")
        self.report_button = ttk.Button(bottom, text="Apri rapporto", command=self.open_report, state="disabled")
        self.report_button.pack(side="left", padx=8)
        ttk.Button(bottom, text="Chiudi", command=self.close).pack(side="right")

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
        self.start.configure(state="disabled")

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

        def finish():
            self.working = False
            self.app._importing = False
            self.app._set_busy(False)
            self.existing.configure(state="normal")
            self.scan.configure(state="normal" if self.workflow else "disabled")
            self.start.configure(state="disabled" if self.workflow else "normal")
            self.import_button.configure(state="normal" if self.items else "disabled")

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
            workflow = ExtensionWorkflow(**arguments)
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
            self.tree.insert("", "end", iid=str(index), values=(candidate.relative_path, human_size(candidate.size)))
        if candidates:
            self.tree.selection_set(*self.tree.get_children())
        self.import_button.configure(state="normal" if candidates else "disabled")
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

        def done(result):
            self.report = result["report_path"]
            self.report_button.configure(state="normal")
            matched, unmatched = len(result.get("matched", [])), len(result.get("unmatched", []))
            self.status.set(f"Importazione completata: {result.get('new_files', 0)} nuovi, "
                            f"{result.get('reused_files', 0)} già presenti, {matched} associati, "
                            f"{unmatched} da associare. Gli originali sono stati conservati.")
            self.app._log(f"Importazione {self.name}: {self.report}", "success")
            if unmatched:
                messagebox.showinfo(APP_NAME, f"{unmatched} file non hanno un abbinamento univoco. "
                    "Sono conservati nel corso ed elencati nel rapporto come Da associare. "
                    "Per assegnarli, apri una nuova raccolta, scegli l'attività come Destinazione "
                    "e usa File già scaricati per selezionarli.", parent=self.window)
            if result.get("index_warning"):
                self.app._log("Importazione riuscita; indice estensioni non aggiornato: " + result["index_warning"], "warning")
        self._background(lambda: self.workflow.collect(selected), done)

    def open_report(self):
        if self.report:
            winutil.open_path(self.report)

    def close(self):
        if self.working:
            messagebox.showinfo(APP_NAME, "Attendi il completamento della copia prima di chiudere.", parent=self.window)
            return
        self.window.destroy()

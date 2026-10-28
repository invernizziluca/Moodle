"""One preparation action for a course's user-supplied downloads."""
import json
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import APP_NAME, winutil
from .exporter import Exporter
from .study_bundle import prepare_bundle


def open_study_dialog(app):
    if app.busy:
        return
    course = app._highlighted_course()
    base = Path(app.dest_var.get()).expanduser() / 'STUDIO_AI' / (f"corso_{course['id']}" if course else 'corso_locale')
    win = tk.Toplevel(app.root)
    win.title('Prepara il corso per AI')
    win.geometry('780x430')
    win.columnconfigure(0, weight=1)
    body = ttk.Frame(win, padding=20)
    body.grid(sticky='nsew')
    body.columnconfigure(0, weight=1)
    ttk.Label(body, text=course.get('fullname', 'Materiali locali') if course else 'Materiali locali',
              wraplength=720, font=(app.font[0], 12, 'bold')).grid(sticky='w', pady=(0, 12))
    ttk.Label(body, text='Metti qui i download di Moodle-DL, Moodle Buddy e FetchV, anche in sottocartelle.\n'
              'Usa una cartella per ogni corso. Non occorre associare i file alle attività.',
              wraplength=720).grid(sticky='w')
    folder = tk.StringVar(value=str(base / 'DA_AGGIUNGERE'))
    ttk.Entry(body, textvariable=folder).grid(sticky='ew', pady=10)
    buttons = ttk.Frame(body)
    buttons.grid(sticky='w')
    def choose():
        selected = filedialog.askdirectory(parent=win, initialdir=folder.get() if Path(folder.get()).exists() else str(Path.home()))
        if selected: folder.set(selected)
    def open_folder():
        try:
            path = Path(folder.get()).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            winutil.open_path(path)
        except OSError as exc: messagebox.showerror(APP_NAME, str(exc), parent=win)
    ttk.Button(buttons, text='Apri cartella dove mettere i download', command=open_folder).pack(side='left')
    ttk.Button(buttons, text='Scegli un’altra cartella…', command=choose).pack(side='left', padx=8)
    use_moodle = tk.BooleanVar(value=bool(course and app.client))
    check = ttk.Checkbutton(body, text='Aggiungi testi e struttura Moodle del corso (senza riscaricare documenti e video)', variable=use_moodle)
    check.grid(sticky='w', pady=15)
    if not course or not app.client: check.configure(state='disabled')
    status = tk.StringVar(value='Il risultato sarà in PRONTO_AI e PRONTO_AI.zip accanto alla cartella dei download.')
    ttk.Label(body, textvariable=status, wraplength=720).grid(sticky='w', pady=10)
    working = False
    def close():
        if not working: win.destroy()
    win.protocol('WM_DELETE_WINDOW', close)
    def prepare():
        nonlocal working
        source = Path(folder.get()).expanduser().absolute()
        destination = source.parent / (source.name + '_PRONTO_AI')
        include = use_moodle.get()
        options = replace(app._options(), dest=str(base / '.moodle'), download_files=False,
                          download_videos=False, capture_pages=False, private_token='',
                          include_forums=False, include_quiz_reviews=False, include_submissions=False)
        working = True
        app._importing = True
        app._set_busy(True)
        app.btn_cancel.configure(state='disabled')
        action.configure(state='disabled')
        status.set('Preparazione in corso… I video vengono inclusi, senza trascrizione automatica.')
        def work():
            source.mkdir(parents=True, exist_ok=True)
            text = None
            if include:
                summary = Exporter(app.client, options).export([course])
                results = summary.get('results') or []
                if summary.get('stopped') or not results or results[0].get('status') != 'ok' or results[0].get('errors'):
                    raise ValueError('Testi Moodle non aggiornati: verifica accesso e riprova, oppure disattiva Aggiungi testi Moodle.')
                index = json.loads((base / '.moodle' / 'courses.json').read_text(encoding='utf-8'))
                row = next(r for r in index['courses'] if str(r['id']) == str(course['id']))
                text = (base / '.moodle' / row['folder'] / 'course_fulltext.md').read_text(encoding='utf-8')
            return prepare_bundle(source, destination, moodle_text=text,
                                  title=course.get('fullname', 'Corso') if course else 'Corso')
        def finish():
            nonlocal working
            working = False
            app._importing = False
            app._set_busy(False)
            action.configure(state='normal')
        def done(result):
            finish()
            status.set(f"Pronto: {result['files']} file unici, {result['duplicates']} duplicati evitati. "
                       f"{len(result['warnings'])} avvisi; {len(result['skipped'])} file esclusi. "
                       'Apri INIZIA_QUI.md. Carica la cartella o lo ZIP se il tuo servizio AI li supporta.')
            winutil.open_path(result['folder'])
        def failed(exc):
            finish()
            status.set('Preparazione non completata. Gli originali sono conservati.')
            messagebox.showerror(APP_NAME, str(exc), parent=win)
        app._run_bg(work, done, failed)
    action = ttk.Button(body, text='PREPARA PER AI', command=prepare)
    action.grid(sticky='ew', pady=10)

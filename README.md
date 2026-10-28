# FFHS Moodle Archiver

**[Scarica la versione 0.5.1 pronta all'uso](https://github.com/invernizziluca/Moodle/raw/refs/tags/v0.5.1/download/FFHS_Archiver_v0.5.1.zip)**

La 0.5 usa **Moodle Buddy e FetchV già installati nel browser** per i download. L'app prepara
la struttura del corso, raccoglie i file e li collega alle attività nell'archivio. Mantiene
il layout leggibile per Obsidian della versione aggiornata allegata dall'utente.

1. Estrai tutto in una cartella nuova e breve, per esempio `C:\FFHS`.
2. Avvia `FFHS Moodle Archiver.pyw` o `Avvia FFHS Moodle Archiver.bat` (Python 3.10+ con Tk).
3. Accedi, **evidenzia un corso** e premi **Documenti con Moodle Buddy…** o **Video con FetchV…**.
4. Scegli la cartella Download del browser e, per FetchV, l'attività del video. Premi **Apri Moodle**.
5. Usa l'estensione vera nella pagina Moodle e attendi la fine del download (`Ctrl+J`).
6. Torna all'app: **2 · Cerca download**, controlla la selezione, poi **3 · Importa selezionati**.

Sono supportati anche **File già scaricati…**. Gli originali restano in Download. L'associazione
automatica usa nomi esatti univoci. Nella scheda **File da collegare** scegli l'attività per i casi
dubbi e premi **Collega selezionati**, senza riaprire la raccolta o riscaricare i file.
I video FetchV vanno nell'attività scelta. L'indice `download_estensioni.md` collega rapporti e copie reali.

I clic nelle estensioni restano nel browser, come previsto dall'alternativa 1. Non serve installare
una terza estensione o attivare la modalità sviluppatore. L'avviso su `edge://newtab` è normale:
usa l'estensione sulla pagina Moodle aperta dall'app. **YouTube resta un link.**

La verifica ordinaria non richiede auto-login Kaltura: evita le richieste che nel rapporto ricevuto
incontravano il cooldown di sei minuti. Non occorre trasferire cookie, password o token del browser.

[Guida completa](LEGGIMI.md) · [Novità 0.5](docs/NOVITA_0.5.md) · [Sviluppo](docs/SVILUPPO.md).
Il progetto è pubblicato sul branch `main`.

La 0.5.1 recupera anche i file da associare importati con la 0.5. I conteggi distinguono copie
uniche e collegamenti da scegliere; la tabella mostra prima il nome del file, poi il suo percorso.

# FFHS Moodle Archiver – Guida

Programma per Windows che esporta i tuoi corsi Moodle FFHS in un archivio locale **strutturato**:
sezioni e sottosezioni (LERNZIELE, VORBEREITUNG, …) nell'ordine originale, testi delle pagine in Markdown,
obiettivi di apprendimento, istruzioni dei compiti, quiz già svolti, forum e tutti i documenti (PDF, Word,
PowerPoint, Excel, …) salvati **accanto alla loro descrizione**.

Versione 0.4. Funziona localmente: l'app usa il servizio Moodle; l'assistente opzionale Edge/Chrome
usa la normale sessione del browser per i player Kaltura/SWITCHcast incorporati in Moodle.

**Novità della 0.4:** ZIP con percorsi brevi, altri formati di indirizzo dei file Moodle, download
di HLS VOD supportati (playlist **e segmenti**), assistente browser per file/HLS dei player e pulsanti
per importare file o cartelle video nell'attività corretta. **YouTube resta solo un link.**
Leggi [Video Kaltura e installazione 0.4](docs/NOVITA_0.4.md).

**Novità della 0.3:** riconosce anche video Moodle diretti nelle attività Link (URL) e nei tag
`iframe`, `object` ed `embed`. I video che rimangono online, inclusi provider esterni e manifest
di streaming HLS/DASH, vengono segnalati tra i contenuti mancanti. Dettagli e prova consigliata
su un singolo corso in [Novità 0.3](docs/NOVITA_0.3.md); istruzioni per chi sviluppa in
[Sviluppo](docs/SVILUPPO.md).

Il nuovo `video_e_media.md` mostra quali video/audio sono stati scaricati, esclusi, non scaricati
per errore o conservati solo come link, insieme alla fonte e alla nota dell'attività.

**Novità della 0.2** (dettagli in `docs/REVISIONE_SICUREZZA.md`):
- login rivisto: il link con il token passa solo in memoria (canale locale autenticato), mai su file;
  la modifica temporanea del registro ha un backup completo e si ripara da sola anche dopo un blocco;
- sito bloccato su moodle.ffhs.ch; il programma può usare solo funzioni Moodle **di lettura**;
- **Verifica accesso** e **Anteprima del corso**: primo test sulla FFHS senza scaricare nulla;
- privacy: nomi degli altri studenti nascosti nei forum, nessuna copia dei dati tecnici grezzi;
- `verifica_corso.md` per confrontare l'esportazione con Moodle; i file che aggiungi a mano nelle
  cartelle (per esempio la stampa PDF di un'attività H5P) vengono collegati automaticamente.

---

## 1. Requisiti

- Windows 10/11 con **Python 3.10 o più recente** (la tua 3.15 va bene).
- Nessun pacchetto da installare: il programma usa solo la libreria standard di Python.
- L'interfaccia usa Tk, incluso nell'installazione standard di Python («tcl/tk and IDLE»).

## 2. Installazione (una volta)

1. Prima di estrarre: tasto destro sullo ZIP → **Proprietà** → spunta **«Sblocca»** → OK.
   Poi estrai in una cartella **stabile e breve**, per esempio `C:\FFHS`
   (non nella cartella Download: il login usa questo percorso).
2. Fai **doppio clic su `FFHS Moodle Archiver.pyw`**.
   Se invece si apre un editor di testo, usa `Avvia FFHS Moodle Archiver.bat`.
3. Nel programma: **File → Crea collegamento sul Desktop**. Da lì in poi basta l'icona.

Il vecchio errore `0x80010135` era causato dal campione storico con percorsi troppo lunghi:
ora è conservato come ZIP opzionale, non serve estrarlo. Il pacchetto pronto all'uso ha la cartella
breve `FFHS_Archiver` e non contiene il campione. Non estrarre sopra la vecchia cartella incompleta.

## 3. Primo test sulla FFHS (nessun download)

Questo è il test consigliato prima di esportare qualsiasi cosa.

**Passo 0 – solo il computer (facoltativo, prima di accedere).** Nella schermata di accesso clicca
**«Verifica questo computer e la connessione (senza accedere)»**. Controlla, senza dati personali:
Python e Tk, la cifratura del token (DPAPI), una prova di salvataggio e ripristino nel registro su una
chiave di prova (non quella di Moodle), il canale locale usato dal login, la raggiungibilità di Moodle.

**Passo 1 – accesso e verifica.** Clicca **«Accedi con FFHS-ID»** (vedi §4). Dopo il primo accesso parte
da sola la **Verifica accesso**: account, versione di Moodle, funzioni disponibili, download consentito,
elenco dei tuoi corsi. Non scarica nulla e non crea l'archivio. Il rapporto si può copiare o salvare.

**Passo 2 – anteprima di un corso.** Evidenzia un corso (un clic sul nome, per esempio BWL8) e clicca
**«Anteprima del corso evidenziato»**: vedi sezioni, sottosezioni, attività, tempi di studio, documenti e
dimensioni, elementi ad accesso limitato. Confrontala con la pagina del corso in Moodle.

**Passo 3 – esporta solo quel corso.** Spuntalo, clicca **«Esporta i corsi selezionati»** e apri nella
sua cartella `verifica_corso.md`: per ogni sezione mostra attività, testi, note create, file salvati/previsti,
elementi non esportati, più una lista di controlli da spuntare (titoli, sottosezioni, Lernziele, tempi di
studio, documenti).

Se qualcosa non torna puoi inviare: il rapporto della Verifica accesso (contiene i nomi dei corsi) e/o la
**Diagnostica del corso** (Strumenti → Diagnostica del corso evidenziato: solo struttura e tipi di attività,
nessun testo né documento).

## 4. Accesso (FFHS-ID + Microsoft Authenticator)

1. Clicca **«Accedi con FFHS-ID»**: si apre il tuo browser (Edge/Chrome) sulla pagina ufficiale di Moodle.
2. Accedi come sempre e conferma con Microsoft Authenticator.
3. Alla fine il browser chiede di aprire un'applicazione (può chiamarsi «Python» o «pythonw»):
   clicca **«Apri»**. Il programma mostra «Connesso come …».

Hai 15 minuti per completare l'accesso; poi il tentativo scade e va ripetuto.

**Se il browser non chiede nulla:** nella pagina finale di Moodle («Bestätigt» / «Confirmed») c'è un
link «Klicken Sie hier, …»: cliccalo una volta. Se ancora non succede nulla, fai clic destro sul link →
**«Copia indirizzo link»**, incollalo nel campo «Il browser non ha aperto il programma?» e clicca
«Usa questo link». Il link vale solo per l'accesso avviato in quel momento (il campo nasconde i caratteri).

**Senza toccare il registro:** togli la spunta «Ricevi il link direttamente dal browser» e usa sempre il
link incollato.

Cosa succede in sicurezza (sintesi):
- La password e l'MFA restano nel browser, sul sito della FFHS. Moodle rilascia un *token*, come per
  l'app Moodle ufficiale; un codice casuale monouso («passport», valido 15 minuti e solo in memoria)
  garantisce che il link appartenga al tuo accesso.
- Solo durante l'accesso il programma registra il link `moodlemobile://` per il tuo utente Windows
  (`HKEY_CURRENT_USER\Software\Classes\moodlemobile`), dopo aver salvato una copia completa di quanto
  c'era; poi lo rimuove e ripristina la copia. Se il programma si blocca, la pulizia avviene al prossimo
  avvio (o subito, se clicchi il link dopo la scadenza).
- Il link arriva al programma attraverso un canale locale in memoria protetto da una chiave casuale,
  mai attraverso file.
- Il token viene salvato cifrato con DPAPI di Windows: solo il tuo utente su questo computer può leggerlo.

## 5. Esportazione e sincronizzazione

- **Esporta i corsi selezionati** / **Esporta tutti i corsi**: la prima volta scarica tutto, poi solo
  novità e modifiche.
- File cambiati → aggiornati; la versione precedente va in `_versioni_precedenti/`.
- Sezioni o attività rinominate/spostate → i file vengono spostati, non riscaricati.
- Elementi rimossi da Moodle → la copia locale resta, marcata «Non più disponibile in Moodle» ed
  elencata in fondo a `course_overview.md`.
- Interruzione (rete, chiusura, «Interrompi») → la prossima esportazione riprende da dove si era fermata.
- Rapporto di ogni esecuzione in `_registro/`.

Opzioni (pannello «Esportazione»): forum, **nascondi i nomi degli altri studenti** (attivo: diventano
«Partecipante 1, 2, …»; restano il tuo nome e quelli dei docenti), quiz già svolti, tue consegne e
feedback, video caricati in Moodle, versioni precedenti, limite di dimensione per file.
Strumenti → «Salva anche i dati tecnici grezzi» è disattivato: serve solo per diagnosi.

## 6. Struttura dell'archivio

```text
FFHS_ARCHIVE/
├── index.md                     elenco dei corsi esportati
├── courses.json
├── _registro/                   rapporto di ogni esportazione (+ diagnostiche)
└── HRM4_Human_Resource_Management_FS26/
    ├── course_overview.md       struttura completa: sezioni, obiettivi, attività, file, link
    ├── course_fulltext.md       tutti i testi del corso in un unico file (ideale per l'AI)
    ├── verifica_corso.md        tabella e controlli per il confronto con Moodle
    ├── elementi_mancanti.md     cosa non è esportabile, perché, e cosa puoi fare
    ├── course_info.md           dati del corso e ultima sincronizzazione
    ├── course_structure.json    struttura in formato dati (per AI e strumenti)
    ├── 01_Ueberblick_HRM_Organisation_und/
    │   ├── 01_Ueberblick_HRM_Organisation_und.md            testo della sezione
    │   ├── 01_LERNZIELE/01_LERNZIELE.md                      obiettivi di apprendimento
    │   └── 02_VORBEREITUNG/
    │       ├── 02_VORBEREITUNG.md
    │       ├── 01_Thema_1_Skript_Ziele_und_Aufgaben/
    │       │   ├── 01_Thema_1_Skript_Ziele_und_Aufgaben.md   descrizione + link al file
    │       │   └── Skript_Ziele_und_Aufgaben_HRM.pdf
    │       └── 02_Thema_1_Ziele_und_Aufgaben_des/
    │           ├── 02_Thema_1_Ziele_und_Aufgaben_des.md       testo della pagina
    │           └── _media/                                    immagini della pagina
    └── _versioni_precedenti/    versioni sostituite di file e testi
```

I nomi delle cartelle sono accorciati (percorsi brevi per Windows); i titoli completi sono nelle note.
Ogni nota `.md` ha in testa titolo, tipo, sezione e **link originale Moodle**. I compiti contengono
istruzioni, scadenze, allegati, la tua consegna e il feedback; i quiz le revisioni dei tentativi conclusi
(solo ciò che Moodle ti mostra); i forum le discussioni con gli allegati. Nell'archivio non finisce nessuna
chiave di accesso: i link a file Moodle vengono ripuliti e puntano alla pagina normale di Moodle.

## 7. Contenuti non esportabili come testo

Per H5P, lezioni (Lektion), SCORM, strumenti esterni (LTI) e questionari il programma conserva
descrizione, date e link; non esporta integralmente l'esperienza interattiva.

I file video diretti serviti da Moodle vengono scaricati quando sono accessibili tramite il servizio
autenticato e l'opzione video è attiva. Questo include file individuati nelle attività URL e nei tag
`iframe`, `object` ed `embed`; non significa che ogni pagina incorporata contenga un video scaricabile.
Gli HLS VOD non cifrati esposti come file Moodle vengono scaricati con tutti i segmenti supportati:
apri la playlist locale `video.m3u8` con VLC e conserva tutta la cartella.

Per **Kaltura/SWITCHcast** premi **Video Kaltura / browser…** e installa una volta l'assistente
Edge/Chrome dalla cartella `browser_helper`. Apri l'attività Moodle, autorizza il rilevamento dei
player incorporati e riproduci il video; l'assistente propone i file e gli HLS supportati rilevati.
Per HLS salva anche l'eventuale audio separato e pubblica la playlist locale dopo i segmenti.
Usa **Importa file dal browser…** oppure **Importa cartella video HLS…** e scegli l'attività di
destinazione: gli originali rimangono in Download, la copia viene collegata nell'archivio.

YouTube non viene scaricato. DASH, live, cifratura/DRM e richieste con intestazioni speciali non
sono tutti supportati: i motivi restano visibili. La prova sul player Kaltura FFHS reale resta da fare;
la presenza di un video dentro Moodle non garantisce da sola che il suo server permetta il download.

Per conservarne il contenuto, aprili in Moodle e salva la pagina come PDF (Ctrl+P → «Salva come PDF»)
**nella cartella dell'attività** (oppure i tuoi appunti, es. `appunti.md`): alla prossima esportazione
il file viene collegato nella nota, nella panoramica, nel testo completo e in `verifica_corso.md`, e non
viene mai cancellato dalla sincronizzazione. `elementi_mancanti.md` raggruppa questi elementi e spiega
cosa puoi fare per ciascun gruppo.

Il rapporto descrive ciò che il programma ha individuato: non certifica che le API espongano ogni
contenuto del corso. I libri soltanto citati nel Modulplan o dentro un PDF non diventano automaticamente
file scaricabili e questa versione non ne ricostruisce una bibliografia completa.

Quiz: solo i tentativi conclusi e solo se il docente permette la revisione; il programma non avvia
tentativi e non recupera risposte nascoste. Elementi ad accesso limitato o nascosti: elencati, con il link.

## 8. Usare l'archivio con ChatGPT, Claude e Obsidian

- **AI:** carica `course_fulltext.md` (tutti i testi, in ordine) e i PDF che ti servono; per la struttura
  usa anche `course_overview.md` o `course_structure.json`.
- **Obsidian:** apri la cartella `FFHS_ARCHIVE` come vault; i link tra note e file funzionano.
- I materiali sono protetti da diritto d'autore: prima di caricarli su servizi AI esterni verifica le
  regole FFHS. Il programma in sé non invia nulla a nessuno.

## 9. Disconnettersi e revocare

**Strumenti → Disconnetti** rimuove il token da questo computer e propone di aprire la pagina Moodle
**Sicherheitsschlüssel**, dove alla riga «Moodle mobile web service» clicchi «Zurücksetzen» per revocarlo
anche sul server. **Strumenti → Rimuovi un gestore moodlemobile:// rimasto** ripulisce il registro a mano
(di solito non serve: avviene da sola).

## 10. Problemi frequenti

| Problema | Soluzione |
| --- | --- |
| Il browser non chiede di aprire il programma | Copia il link della pagina finale (vedi §4) |
| «Il tempo per l'accesso (15 minuti) è scaduto» | Clicca di nuovo «Accedi con FFHS-ID» |
| «Accesso scaduto» durante l'esportazione | Accedi di nuovo; l'esportazione riprende da dove era |
| «Impossibile salvare … file aperto?» | Chiudi il PDF/Word aperto e riesporta |
| Doppio clic sul `.pyw` apre un editor | Usa `Avvia FFHS Moodle Archiver.bat`, poi crea il collegamento |
| Righe [!!] o [NO] nella Verifica accesso | Leggi il dettaglio della riga; registro in `%LOCALAPPDATA%\FFHS Moodle Archiver\logs` |

## 11. Riga di comando (facoltativa)

```text
py "FFHS Moodle Archiver.pyw" verify                  verifica accesso, senza download
py "FFHS Moodle Archiver.pyw" login                   accesso con link incollato (non tocca il registro)
py "FFHS Moodle Archiver.pyw" courses                 elenco dei corsi con il loro numero
py "FFHS Moodle Archiver.pyw" preview --course 123    anteprima di un corso, senza download
py "FFHS Moodle Archiver.pyw" export --course 123     esporta un corso
py "FFHS Moodle Archiver.pyw" sync                    corsi spuntati nell'interfaccia (per pianificazioni)
py "FFHS Moodle Archiver.pyw" diagnose --course 123 --out diag.json
```

# FFHS Moodle Archiver – Analisi tecnica e architettura

Documento storico della base 0.3 allegata. Per il flusso corrente con Moodle Buddy e FetchV
consulta [Novità 0.5](NOVITA_0.5.md): la GUI non attiva auto-login o cattura nativa dei video.

Stato: 28 ottobre 2026, versione 0.3 (Fasi 1–3 completate; 0.3 aggiunge download dei video delle lezioni
Kaltura/SWITCHcast, cattura del Modulplan e delle pagine del corso, e cartella pronta per Obsidian — vedi
`COSA_SI_PUO_SCARICARE.md`; sicurezza in `REVISIONE_SICUREZZA.md` §7; prossimo passo: test sulla FFHS reale
con i video e, poi, Fasi 4–5: sincronizzazione pianificata ed eseguibile Windows).

## Fase 1 – Analisi tecnica

### Cosa espone moodle.ffhs.ch (verificato dalla configurazione pubblica)

| Verifica | Risultato |
| --- | --- |
| Web service / servizio per app (`enablewebservices`, `enablemobilewebservice`) | attivi |
| Tipo di login per app (`typeoflogin`) | 2 = **login tramite browser** |
| URL di avvio app | `https://moodle.ffhs.ch/admin/tool/mobile/launch.php` |
| Provider di identità | **FFHS-ID** (OAuth 2, emittente `id=1`) e SWITCHaai; edu-ID/VHO via Shibboleth |
| MFA Moodle (`tool_mfa_enabled`) | attivo |
| Versione Moodle | **5.2.x** (i campi `tool_mfa_enabled`, `enableloginrecaptcha`, `enableforgotpasswordrecaptcha` della configurazione pubblica esistono solo da Moodle 5.2) |
| Funzioni dell'app disattivate da FFHS | H5P offline, attività H5P, homepage del sito, blog (solo interfaccia app) |
| Tema | `boost_ffhs` (basato su Boost) |

Conseguenze:
- L'**API ufficiale dell'app Moodle è disponibile**: la stessa che usa l'app «Moodle» sul telefono.
- Il login corretto per un programma esterno è quello «via browser» (`launch.php`), compatibile con
  FFHS-ID e Microsoft Authenticator: l'MFA avviene nel browser e non va aggirato.
- Moodle 5.x usa le **sottosezioni** (`mod_subsection`): «LERNZIELE», «VORBEREITUNG» ecc. sono molto
  probabilmente sottosezioni; l'esportatore le annida correttamente.
- Video FFHS: l'informativa privacy cita Kaltura e YouTube → video in streaming, esportati come link.

### Perché Moodle-DL rimane su una pagina bianca

Moodle-DL 2.4.0 (GUI PySide6) apre `launch.php?...&urlscheme=moodledl` in un browser incorporato
(QtWebEngine) e intercetta **solo** lo schema `moodledl://`. Ma Moodle, per impostazione predefinita,
**forza lo schema `moodlemobile://`** (impostazione `tool_mobile/forcedurlscheme`, valore predefinito
`moodlemobile`, verificato nel codice di Moodle 4.5–5.2). Il reindirizzamento finale va quindi a
`moodlemobile://token=…`, che Moodle-DL non riconosce: la pagina resta bianca e il token non arriva mai.
Inoltre il token viene salvato in chiaro in `config.json` e l'output è orientato ai file, non alla
struttura didattica.

### Altre soluzioni valutate

| Soluzione | Esito |
| --- | --- |
| Moodle-DL | API corretta, ma login SSO non funzionante su FFHS (schema), token in chiaro, nessun riepilogo strutturato |
| Moodle Buddy / Moodle Downloader (estensioni) | solo download in massa di file, senza testi, struttura, quiz o forum |
| SingleFile, Obsidian Web Clipper | una pagina alla volta, manuale |
| «Download course content» di Moodle | ZIP per singolo corso se abilitato dall'amministrazione; senza quiz rivisti, forum, sincronizzazione |
| Riutilizzo dei cookie del browser | escluso: Chrome/Edge cifrano i cookie per applicazione, e sarebbe fragile e poco sicuro |
| Scraping HTML | escluso: l'API ufficiale fornisce gli stessi contenuti in forma strutturata |

**Decisione:** applicazione propria, basata sull'API ufficiale dell'app Moodle, con login tramite il
browser normale. Nessuna dipendenza esterna (solo libreria standard di Python), così funziona subito
con Python 3.15 senza compilare pacchetti.

## Fase 2 – Architettura

```text
 FFHS Moodle Archiver.pyw (doppio clic)              browser (Edge/Chrome)
          │                                                │  FFHS-ID + Authenticator
          ▼                                                ▼
   gui.py (tkinter, italiano) ── session.py ── sso.py ── launch.php?passport=…&confirmed=1
          │                        │                             │
          │                        │      moodlemobile://token=… ▼  (link della pagina finale)
          │                        │      Windows → helper (sso.handle_callback, nessuna finestra)
          │                        │        ├─ protocol.py: gestore HKCU temporaneo, backup completo, scadenza
          │                        │        └─ ipc.py: named pipe con autenticazione reciproca (in memoria)
          │                        │      oppure link incollato → firma md5(wwwroot + passport monouso)
          │                        ├─ securestore.py: record cifrato con DPAPI
          │                        └─ verify.py: Verifica accesso e Anteprima (sola lettura, nessun download)
          ▼
   exporter.py ── moodle.py (REST /webservice/rest/server.php + /webservice/pluginfile.php)
     │  1. struttura: core_course_get_contents (+ sottosezioni)
     │  2. dati per tipo: mod_*_get_*_by_courses, revisioni quiz, stato consegne, forum, glossari, wiki
     │  3. html2md.py: HTML → Markdown (tabelle, liste, immagini, video, domande dei quiz)
     │  4. pianificazione percorsi stabili, spostamenti, download con ripresa (Range) e 304
     │  5. state.py: stato per corso (sincronizzazione, versioni, elementi rimossi)
     ▼
   FFHS_ARCHIVE/<corso>/ course_overview.md · course_fulltext.md · course_structure.json ·
                         elementi_mancanti.md · cartelle per sezione/attività con i file
```

Scelte principali:
- **Sicurezza** (dettagli in `REVISIONE_SICUREZZA.md`): password mai vista; link firmato e legato a un
  passport monouso in memoria; link consegnato in memoria tramite canale autenticato; gestore
  `moodlemobile://` temporaneo con backup completo e pulizia automatica; token cifrato con DPAPI; sito
  fisso moodle.ffhs.ch con blocco di ogni altro host; solo funzioni Moodle di lettura; segreti rimossi da
  log e archivio; nomi degli altri studenti nascosti.
- **Fedeltà didattica:** ordine e titoli originali, sottosezioni annidate, testi delle etichette
  («Text- und Medienfeld») nel punto esatto in cui appaiono, descrizioni, date, condizioni di accesso,
  link Moodle originali in ogni nota; collegamenti interni riscritti verso le note e i file locali.
- **Sincronizzazione:** percorsi deterministici; file identificati da Moodle (non dal nome cartella),
  quindi rinominare/spostare in Moodle sposta i file anche in locale; versioni precedenti conservate;
  stato salvato durante il download per riprendere dopo un'interruzione.
- **Limiti rispettati:** solo ciò che l'API mostra al tuo account; quiz solo se la revisione è
  permessa; nessun tentativo avviato; contenuti interattivi esterni elencati come «non esportati».

## Fase 3 – Prototipo: come è stato verificato

Non avendo accesso al tuo account FFHS, il programma è stato provato su un **Moodle 5.2.4 reale**
installato in laboratorio, configurato come moodle.ffhs.ch (servizio app attivo, login via browser,
schema forzato `moodlemobile`) con un corso «HRM4» costruito come quello descritto: sezione
«1 Überblick HRM, Organisation und Strategie» con sottosezioni LERNZIELE / VORBEREITUNG / TRANSFER,
pagine con tabelle, immagini e video incorporati (YouTube, SWITCHtube, MP4), PDF/PPTX/DOCX/XLSX,
cartella con sottocartelle, libro, glossario, forum, quiz con tentativo svolto (e uno senza revisione),
compito con consegna e feedback, elementi con accesso limitato o nascosti, LTI, lezione, H5P.

Risultati:
- login completo via `launch.php` con firma verificata; link falsificati rifiutati;
- esportazione di 2 corsi con interfaccia grafica e da riga di comando (Python 3.11, 3.13, 3.15);
- seconda esecuzione senza modifiche: 0 download (file invariati, immagini verificate con risposta 304);
- dopo modifiche del docente: 1 file nuovo, 1 aggiornato (vecchia versione conservata), sezione rinominata
  e cartella spostata senza riscaricare, attività eliminata conservata e marcata;
- interruzione a metà e ripresa: nessun duplicato; ripresa di un file parziale con HTTP Range;
- token scaduto/non valido: messaggio chiaro e nuovo login;
- versione 0.2: 61 test automatici senza rete + 9 test sul Moodle di laboratorio superati (Python 3.11,
  3.13, 3.15).

Da validare su FFHS (non riproducibile in laboratorio): il passaggio da FFHS-ID/Microsoft al link
`moodlemobile://` nel tuo browser, eventuali plugin FFHS specifici (formato del corso, Kaltura, MUMIE)
e il modo in cui FFHS mostra i tempi di studio. La diagnostica integrata serve proprio a questo.

## Fase 4 e 5 – Prossimi passi

- **Ora:** «Verifica accesso» sul tuo account FFHS, «Anteprima» di un corso (es. BWL8), esportazione di
  quel solo corso e confronto con Moodle tramite `verifica_corso.md`.
- **Fase 4:** dopo la prova su un corso FFHS: adattamenti dalla diagnostica; poi sincronizzazione
  automatica con l'Utilità di pianificazione di Windows (il comando `sync` è già pronto).
- **Fase 5:** eseguibile autonomo (PyInstaller, costruito e provato su Windows) ed eventuale installer.
  Oggi il programma parte già con doppio clic, senza PowerShell né pacchetti da installare.

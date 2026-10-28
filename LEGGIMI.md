# FFHS Moodle Archiver 0.5 — guida

Il flusso principale usa le **vere estensioni Moodle Buddy e FetchV** installate nel tuo browser.
Il programma esporta struttura e testi del corso, poi organizza i download delle estensioni.
Serve Windows con Python 3.10 o successivo e Tk. Non occorrono pacchetti pip.

## Installazione e accesso

Estrai tutto in una cartella nuova e breve, per esempio `C:\FFHS`. Non avviare l'app dentro lo ZIP
e non estrarla sopra una vecchia installazione incompleta. Apri `FFHS Moodle Archiver.pyw`; se si
apre un editor, usa `Avvia FFHS Moodle Archiver.bat`. Il campione storico non serve per l'avvio.

L'accesso salvato resta nella cartella del tuo utente Windows ed è protetto con DPAPI.
La versione mantiene le impostazioni precedenti e il normale login FFHS-ID/MFA. Per il nuovo
flusso non occorre conservare il token privato di auto-login video. La verifica ordinaria non
esegue il probe Kaltura che poteva causare il cooldown di sei minuti; quelle opzioni vengono
disattivate nella migrazione delle vecchie impostazioni.

## Documenti con Moodle Buddy

1. Accedi nell'app e **evidenzia un corso** con un clic sul nome.
2. Premi **Documenti con Moodle Buddy…**. Se manca la struttura locale, l'app la prepara con testi
   e metadati API, senza scaricare i materiali o chiedere auto-login video.
3. Scegli la cartella in cui il browser salva i download. Sono controllate anche le sottocartelle.
4. Lascia **Destinazione: Automatico** per abbinare i nomi esatti univoci, oppure scegli un'attività
   per associare esplicitamente a essa tutti i file che selezionerai nella raccolta.
5. Premi **Apri Moodle per Moodle Buddy**. Nella pagina del corso usa l'icona dell'estensione vera.
6. Attendi la fine dei download in `Ctrl+J`, torna all'app e premi **Download terminati: cerca file**.
7. Controlla la selezione, deseleziona eventuali file estranei e premi **Importa selezionati**.

Moodle Buddy può produrre file singoli in una gerarchia corso/sezione e ZIP delle cartelle Moodle.
L'app copia entrambi, estrae gli ZIP sicuri e accorcia i nomi locali quando necessario, conservando
i percorsi originali nel rapporto. Gli originali del browser non vengono spostati né cancellati.

L'associazione automatica richiede un nome esatto presente in **una sola attività** nei metadati.
Nomi ambigui, nomi cambiati dal browser come `lezione (1).pdf` e file non esposti dalle API restano
**Da associare**. Per risolverli, apri una nuova raccolta, scegli l'attività e usa **File già scaricati…**.

## Video con FetchV

1. Evidenzia il corso e premi **Video con FetchV…**.
2. Scegli l'attività del video, la cartella Download e il browser in cui hai FetchV.
3. Premi **Apri Moodle per FetchV**. Avvia il video nella pagina reale e scaricalo con FetchV.
4. Attendi il file finale in `Ctrl+J` (per esempio MP4), non un segmento o un file temporaneo.
5. Torna nell'app, cerca i nuovi file, controlla la selezione e importa.

Il video viene copiato nella cartella allegati dell'attività scelta e collegato da una nota.
Ripetere l'importazione dello stesso contenuto nella stessa attività riusa la copia già presente.
Non serve l'estensione personalizzata proposta nella 0.4: puoi rimuoverla dal browser.

**YouTube resta un link.** L'app non propone FetchV per un'attività identificata come solo YouTube.
L'origine di un file locale non è deducibile con certezza dal nome: controlla tu i file selezionati.
I limiti del player, dei permessi, del DRM e delle estensioni rimangono quelli dei servizi usati.

## File già scaricati

Usa **File già scaricati…** per scegliere file completati nella cartella Download indicata.
Non devi scaricare di nuovo materiali già sul PC. Un file cambiato tra l'anteprima e l'importazione
viene rifiutato: attendi la fine e aggiorna l'elenco.

Il programma non legge profili o cronologie del browser. La ricerca confronta la cartella scelta
prima e dopo il download: non dimostra da quale estensione provenga ogni file. Per questo la
selezione viene mostrata prima di importare. Una cartella troppo grande viene segnalata esplicitamente.

## Dove trovi il materiale

Il layout della versione allegata è mantenuto: nomi leggibili, sezioni come cartelle, note delle
attività nella sezione e allegati nella cartella omonima. È compatibile anche con il vecchio layout
che teneva nota e allegati nella stessa cartella dell'attività.

- La pagina iniziale e `course_overview.md` collegano **`download_estensioni.md`**.
- Questo indice mostra rapporti e copie importate da Moodle Buddy e FetchV.
- Ogni rapporto distingue file associati e **Da associare** e collega i file veri.
- Le attività associate hanno note `materiali_moodle_buddy.md` o `materiali_fetchv.md` accanto agli allegati.
- Struttura JSON, testi e collegamenti originali Moodle rimangono disponibili per Obsidian e lo studio.
- `elementi_mancanti.md` descrive gli esiti dell'esportatore API: per le copie delle estensioni consulta
  anche `download_estensioni.md`. Un import non certifica la completezza dell'intero corso.

Il pulsante **Esporta struttura, testi e file API** mantiene anche l'esportatore normale. Le funzioni
avanzate di auto-login/cattura della base allegata restano opzionali per la CLI, disattivate nel flusso
grafico con estensioni. Non sono necessarie per l'alternativa 1.

## Problemi frequenti

| Situazione | Cosa fare |
| --- | --- |
| Estensioni non consentite in edge://newtab | Apri la pagina Moodle con l'app e clicca l'estensione in quella scheda. |
| Elenco vuoto | Controlla la cartella effettiva e usa File già scaricati per quelli precedenti alla sessione. |
| Troppi file nella cartella | Scegli una sottocartella usata dall'estensione. La lista non viene troncata in silenzio. |
| Download ancora in corso | Aspetta la fine in Ctrl+J e aggiorna l'elenco. |
| File Da associare | Scegli esplicitamente l'attività in una nuova raccolta. |
| Account scaduto | Accedi nell'app per le API e nel browser per le estensioni. |
| Vecchio campione troppo lungo da estrarre | È facoltativo; usa il pacchetto dell'app con percorsi brevi. |

## Estensioni ufficiali e verifica

Le estensioni restano separate e vengono installate dai loro store. Non espongono un comando pubblico
verificato per avviare il download da Python: i clic nell'estensione restano a carico dell'utente,
come previsto dall'alternativa 1. Nessun codice FetchV è incorporato o ridistribuito.

FetchV Edge: https://microsoftedge.microsoft.com/addons/detail/dbepbhhcmhodojepbagfppgpieeplpik

Moodle Buddy: https://chromewebstore.google.com/detail/nomahjpllnbcpbggnpiehiecfbjmcaeo

I test verificano byte, ZIP, associazioni, deduplica, link e preparazione senza auto-login. La GUI è
stata provata su display Linux con copie reali e un MP4 riproducibile; l'apertura del browser era
simulata. Il login Windows/FFHS e i download delle tue estensioni vanno provati sul tuo PC.

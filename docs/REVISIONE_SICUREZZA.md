# Revisione di sicurezza — FFHS Moodle Archiver 0.2

Data: 22 ottobre 2026 · Ambito: login SSO, gestione del token, registro di Windows, canale tra browser e
programma, controllo degli URL, dati personali nell'archivio.

Metodo: revisione del codice riga per riga (moduli `sso`, `session`, `protocol`, `ipc`, `securestore`,
`moodle`, `exporter`, `gui`), confronto con il codice sorgente di Moodle 5.2 (`admin/tool/mobile/launch.php`),
test automatici e prove reali su un Moodle 5.2.4 di laboratorio configurato come moodle.ffhs.ch.

## 1. Sintesi

- I due punti ad **alta** priorità della revisione esterna (registro di Windows e link con il token in un
  file in chiaro) sono **risolti**; anche i punti a priorità media sono stati affrontati.
- La revisione interna del codice ha trovato e corretto altri punti (tabella §2, F3–F17).
- Il punto «manca una verifica reale sulla FFHS» è affrontato con la Verifica accesso (sotto).
- Nuova **Verifica accesso** e **Anteprima del corso**: il primo test sulla FFHS non scarica nulla e non
  crea l'archivio.
- Rischi residui accettati e motivati in §3. Inventario di ciò che il programma tocca in §4.

## 2. Problemi e correzioni

| ID | Gravità | Problema nella 0.1 | Correzione nella 0.2 | Verifica |
| --- | --- | --- | --- | --- |
| F1 | Alta | Il link con il token passava da un file in chiaro (`sso_callback.txt`), che restava sul disco se il programma non si avviava. | Il link passa in memoria, da un canale locale (named pipe di Windows) con autenticazione reciproca HMAC-SHA256 e chiave casuale a 256 bit nuova per ogni tentativo. Nessun file. I file della 0.1 vengono cancellati all'avvio. | `ChannelTests` (5), login reali |
| F2 | Alta | Registro: veniva salvato solo il comando del gestore `moodlemobile://` e il ripristino avveniva solo a chiusura regolare; dopo un blocco la registrazione restava; la propria registrazione poteva essere salvata come "precedente". | Copia completa della chiave (tutti i valori con i loro tipi e le sottochiavi) prima di ogni modifica; marcatore con scadenza (15 min); pulizia automatica all'avvio successivo e dall'helper scaduto; ripristino immediato se una scrittura fallisce; nessuna modifica se la copia non si può salvare; una registrazione di un'altra applicazione non viene mai cancellata. | `RegistrarTests` (11) con registro simulato; autotest sul registro reale nella Verifica accesso |
| F3 | Alta | I "passport" (che legano il link al tentativo) erano salvati su disco e più di uno restava valido per un'ora. | Un solo passport, solo in memoria, monouso, valido 15 minuti. | link di un altro tentativo, link riusato, tentativo scaduto: rifiutati |
| F4 | Media | L'helper avviato dal browser apriva il programma e completava un accesso anche senza tentativo in corso: una pagina web con un link `moodlemobile://` poteva far partire il programma. | L'helper non apre mai il programma: consegna il link solo a un programma che sta aspettando quel tentativo; altrimenti avvisa e ripulisce il registro. | `HelperProcessTests` (6) |
| F5 | Media | Gli argomenti dell'helper erano letti insieme agli altri comandi. | Gestione separata e rigida: solo `moodlemobile://token=…` (max 2 KB, caratteri ammessi verificati), canale verificato, interprete Python isolato (`-I`). | input malformati rifiutati |
| F6 | Media | Una chiave "nuda" di 32 caratteri era accettata anche dal canale del browser. | Dal browser solo link firmati; la chiave nuda è accettata solo se l'utente la incolla. | test dedicato |
| F7 | Media | Indirizzo del sito modificabile (anche `http`); indirizzi ricevuti dalla configurazione pubblica di Moodle non controllati. | Sito fisso `https://moodle.ffhs.ch` (eccezione solo per `localhost` in sviluppo); verifica di `wwwroot`, della pagina di login e del sito del token; ogni richiesta verso un altro host viene bloccata prima dell'invio. | `test_site_is_locked`, `test_unexpected_wwwroot_aborts` |
| F8 | Media | Il token (come quello dell'app Moodle) permette anche di scrivere: messaggi nei forum, consegne, tentativi di quiz. | Elenco fisso di 36 funzioni di **sola lettura** nel codice (`moodle.py`); ogni altra funzione è rifiutata prima di contattare Moodle. Un test controlla che il programma non ne usi altre. | `test_read_only_functions_only`, `test_every_called_function_is_whitelisted` |
| F9 | Media | Il filtro dei log nascondeva i segreti solo nel testo fisso del messaggio, non nei parametri né negli errori. | Il filtro agisce sulla riga completa e sulle tracce degli errori (token, privatetoken, passport, chiave del canale, chiavi `tokenpluginfile`, sesskey). | `test_log_filter_redacts_args_and_tracebacks` |
| F10 | Media | I dati tecnici grezzi (copie complete delle risposte di Moodle, con messaggi di altri studenti e chiavi di sessione) erano salvati di default. | Disattivati di default (Strumenti), cancellati se l'opzione viene spenta, ripuliti se attivati. | scansione degli archivi |
| F11 | Media | Nomi degli altri studenti nei forum esportati. | Di default diventano «Partecipante 1, 2, …»; restano il tuo nome, i docenti del corso e gli autori degli annunci. | scansione degli archivi, `test_pseudonymizer` |
| F12 | Media | Contenuti non esportabili (H5P, lezioni, SCORM, Kaltura): solo un elenco. | `elementi_mancanti.md` raggruppa per tipo con «cosa puoi fare»; i PDF o gli appunti salvati a mano nella cartella dell'attività vengono collegati ovunque e mai cancellati. | test sul laboratorio |
| F13 | Bassa | Nell'archivio potevano finire chiavi personali presenti nei contenuti (link `tokenpluginfile.php/<chiave>`, parametri `token=`). | Ogni testo scritto nell'archivio viene ripulito; i link a file Moodle diventano link normali (`pluginfile.php`). | `test_scrub_secrets` |
| F14 | Bassa | `credentials.json` conteneva nome e nome utente in chiaro (solo il token era cifrato). | L'intero record è cifrato con DPAPI; il formato 0.1 viene convertito automaticamente. Se DPAPI non funziona, il token non viene salvato (resta solo in memoria). | autotest DPAPI nella Verifica accesso |
| F15 | Bassa | «Verifica sistema» registrava davvero il gestore `moodlemobile://` per provarlo. | Autotest su una chiave di prova dedicata (`HKCU\Software\FFHS Moodle Archiver\SelfTest`), poi cancellata. | `test_self_test_uses_only_its_test_key` |
| F16 | Bassa | Il collegamento sul Desktop era creato con PowerShell e `-ExecutionPolicy Bypass`. | Rimosso il parametro: un singolo comando, nessuno script. | — |
| F17 | Info | Nomi di file e cartelle ricevuti da Moodle. | Verificato: nessun nome può portare fuori dalla cartella del corso (`..`, barre, nomi riservati di Windows). | `test_paths_from_moodle_stay_inside_the_course_folder` |

## 3. Rischi residui (accettati)

- **Token nei link di download** (`webservice/pluginfile.php?token=…`): è il metodo documentato da Moodle
  e usato dall'app ufficiale. Il token va solo a moodle.ffhs.ch via HTTPS e non compare mai nei log né
  nell'archivio.
- **Link come argomento dell'helper**: con i gestori di protocollo di Windows il browser passa il link
  sulla riga di comando dell'helper, visibile per pochi millisecondi ai processi del tuo stesso utente.
  È un limite di Windows; chi vuole evitarlo può disattivare «Ricevi il link direttamente dal browser».
- **Potere del token**: è lo stesso dell'app Moodle. Il programma si limita alla lettura (F8), ma un
  malware che gira con il tuo utente Windows potrebbe decifrarlo, come qualunque dato protetto da DPAPI.
  Revoca in Moodle: Profil → Einstellungen → Sicherheitsschlüssel → Zurücksetzen.
- **`confirmed=1` nell'indirizzo di login**: è un parametro di Moodle che mostra una pagina con il link
  invece di un reindirizzamento silenzioso (Moodle lo usa dopo la conferma di un'email). Non salta alcun
  controllo: verificato nel codice di `admin/tool/mobile/launch.php`. Senza di esso il browser
  mostrerebbe una pagina bianca, come succedeva con Moodle-DL.
- **Prove su Windows**: registro, DPAPI e named pipe sono stati provati con un registro simulato fedele
  all'API e con il canale locale su Linux; non è stato possibile eseguirli su un Windows reale in questo
  ambiente. Per questo la **Verifica accesso** esegue sul tuo PC un autotest di ciascun meccanismo prima
  di qualsiasi esportazione (anche prima dell'accesso). Se un autotest fallisce, il programma usa il link
  incollato e non modifica il registro.

## 4. Cosa tocca il programma

- **Rete**: solo `https://moodle.ffhs.ch`:
  `/lib/ajax/service-nologin.php` (configurazione pubblica, senza login),
  `/admin/tool/mobile/launch.php` (aperta nel tuo browser per il login),
  `/webservice/rest/server.php` (solo funzioni di lettura),
  `/webservice/pluginfile.php` (download dei file),
  `/user/managetoken.php` (aperta nel browser solo se chiedi la revoca).
  Immagini o link verso altri siti presenti nei contenuti non vengono scaricati: restano link.
- **Registro di Windows** (solo il tuo utente, nessun diritto di amministratore):
  `HKCU\Software\Classes\moodlemobile` solo durante un accesso (massimo 15 minuti);
  `HKCU\Software\FFHS Moodle Archiver\SelfTest` solo durante l'autotest, poi cancellata.
- **File**: `%LOCALAPPDATA%\FFHS Moodle Archiver\` (`credentials.json` cifrato, `settings.json`,
  `logs\`, `protocol_backup.json` solo durante un accesso) e la cartella dell'archivio che scegli.
- **Processi**: nessun servizio, nessun avvio automatico; l'helper esiste solo nell'istante in cui il
  browser apre il link.

## 5. Come controllare da sé

- Registro: Win+R → `regedit` → `HKEY_CURRENT_USER\Software\Classes\moodlemobile`. Prima e dopo
  l'accesso deve essere come prima (di solito: assente).
- Funzioni Moodle usate: elenco `READ_ONLY_FUNCTIONS` in `ffhs_archiver/moodle.py`.
- Test automatici (nessuna rete): `py -m unittest tests.test_unit tests.test_security`.

## 6. Test eseguiti per la 0.2

- 61 test automatici senza rete, superati con Python 3.11, 3.13 e 3.15 (31 di base, 30 di sicurezza).
- 6 test di login sul Moodle 5.2.4 di laboratorio: accesso completo (browser simulato → helper →
  canale → firma), link di un altro tentativo, chiave incollata, tentativo scaduto, indirizzi inattesi
  nella configurazione di Moodle, file della 0.1; più 3 test di download (ripresa con Range, 304,
  token non valido).
- Scenario completo: verifica, esportazione, riesportazione senza modifiche, sincronizzazione dopo
  modifiche del docente, interruzione e ripresa; interfaccia pilotata con schermate.
- Scansione degli archivi prodotti: nessun token o chiave, nessun dato grezzo, nessun nome di altri
  studenti.

# Assistente video per Edge e Chrome

Questa estensione opzionale serve quando il browser vede un file video che le API dell’app non
espongono. Usa la normale sessione del browser: non copiare token, cookie o password nell’app.
Non scarica YouTube, non esporta credenziali e non invia materiali a un server dello sviluppatore.

## Installazione, una volta

1. In Edge apri `edge://extensions` (in Chrome: `chrome://extensions`).
2. Attiva **Modalità sviluppatore**, poi **Carica estensione decompressa**.
3. Scegli questa cartella `browser_helper`, quella contenente `manifest.json`.
4. Fissa **FFHS — Video dal browser** nella barra delle estensioni, se vuoi.

## Scaricare

1. Apri un’attività su `https://moodle.ffhs.ch` e accedi normalmente con FFHS-ID/MFA.
2. Avvia il video, poi clicca l’icona dell’assistente e **Aggiorna elenco**.
3. Se il player è su un altro dominio, premi **Rileva player incorporati** e autorizza l’accesso
   HTTPS quando il browser lo chiede. Ricarica l’attività, avvia il video e aggiorna l’elenco.
   Il permesso è ampio perché i server dei player variano; il codice filtra comunque per schede
   la cui pagina principale è Moodle FFHS. Puoi revocarlo dalle impostazioni dell’estensione.
4. Premi **Scarica file** per un MP4/WebM. Per Kaltura/SWITCHcast prova **Scarica streaming HLS**:
   si apre una scheda dell’assistente, poi premi **Avvia download HLS** e lasciala aperta.
   Il programma sceglie la qualità più alta del manifest e salva anche una traccia audio separata,
   quando indicata dal player. Sono supportati HLS VOD non cifrati, segmenti TS e fMP4 con init.
   Controlla il completamento nei download del browser (`Ctrl+J`).
   I file vengono salvati in `Download/FFHS_Moodle/cm_NUMERO/` quando l’attività è riconosciuta,
   oppure `course_NUMERO/` o `moodle/`. Non c’è una verifica di completezza di tutti i video.
5. Per HLS apri `video.m3u8` con **VLC**: la cartella include realmente segmenti video/audio e
   playlist locali, non è un singolo MP4. Conserva e importa **l’intera cartella HLS**. La playlist
   principale viene scritta solo dopo il completamento di tutti i segmenti.
6. Importa il file/cartella nell’archivio con il comando dell’applicazione, oppure aggiungilo alla cartella
   della relativa attività e riesporta: sarà collegato come materiale aggiunto manualmente.

L’assistente propone i file MP4/WebM e audio rilevati, oltre al download HLS
VOD non cifrato. DASH, streaming live, intervalli di byte, DRM e flussi cifrati non vengono scaricati;
il programma spiega il motivo. Blob generati dal player e richieste che dipendono da intestazioni
speciali possono non essere scaricabili. I manifest che Kaltura espone possono variare tra corsi:
la prova su uno specifico player FFHS resta necessaria. Il downloader dell’app resta disponibile
quando il manifest è esposto direttamente dalle API Moodle.
Un download avviato non equivale a un download concluso: controlla il browser e apri il file.
Un URL MP4 può anche essere un frammento del player: i frammenti con nomi comuni vengono esclusi,
ma il tipo MIME non garantisce che ogni file rilevato sia l’intero filmato. Per un player HLS scegli
**Scarica streaming HLS** per salvare il manifest con tutti i segmenti dichiarati.
Se annulli o chiudi la scheda di download HLS, una cartella incompleta può restare in Download;
non importarla come video completo e ripeti il download dal player.

Gli URL firmati vengono conservati soltanto nello storage di sessione dell’estensione e non mostrati
nel popup. I candidati vengono eliminati alla navigazione/chiusura della scheda Moodle; una richiesta
HLS già avviata può restare in memoria dopo un errore o la chiusura della scheda assistente, fino al
completamento o al riavvio del browser. Dopo 30 minuti la richiesta non è più utilizzabile. Non
esportare profili del browser.

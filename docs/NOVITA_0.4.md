# Versione 0.4 — percorsi brevi, file e video dei player Moodle

## Installazione senza l'errore di estrazione

Il messaggio `0x80010135: Percorso troppo lungo` era causato dalle cartelle profonde del campione
dimostrativo incluso nel vecchio ZIP. Il campione ora è compresso separatamente e non va estratto
per usare l'app. Il pacchetto utente `FFHS_Archiver_v0.4.zip` include solo l'app, la guida e
l'assistente browser, con nomi brevi. Estrai in una nuova cartella come `C:\FFHS`.

Anche lo ZIP del codice GitHub non contiene più i vecchi percorsi dimostrativi annidati.
L'archivio dei tuoi corsi è distinto dal pacchetto dell'app: conserva la copia precedente.

## Documenti e video Moodle

- Riconosce sia `pluginfile.php/percorso` sia `pluginfile.php?file=/percorso`.
- Legge i file restituiti dalle attività oltre ai contenuti del corso quando disponibili.
- I download PDF, documenti e file video diretti continuano a rispettare sincronizzazione e limiti.
- Per HLS VOD non cifrati esposti direttamente da Moodle salva playlist e segmenti insieme.
  Supporta master playlist e inizializzazione fMP4; i casi non supportati hanno un motivo esplicito.
  Apri `video.m3u8` con VLC: non è una conversione in un singolo MP4.

## Kaltura / SWITCHcast nel browser

Il player Kaltura usa spesso un dominio video e la sessione del browser, distinti dal token API
Moodle. L'assistente opzionale incluso usa quella sessione; non devi copiare password, cookie o token.

1. Premi **Video Kaltura / browser…** nell'app e segui l'installazione Edge/Chrome, una volta.
2. Apri la vera attività su Moodle, abilita nell'assistente il rilevamento dei player incorporati,
   ricarica la pagina e avvia il video. Aggiorna l'elenco dell'assistente.
3. Scegli **Scarica file** oppure **Scarica streaming HLS** e lascia aperta la scheda fino al termine.
4. Per HLS vengono salvati segmenti, inizializzazione ed eventuale audio separato, poi le playlist
   riscritte con percorsi locali. Verifica `video.m3u8` con VLC anche senza connessione.
5. Nell'app evidenzia il corso esportato e scegli **Importa file dal browser…** o
   **Importa cartella video HLS…**. Seleziona l'attività corretta. La copia non sostituisce gli originali.
   La nota `materiali_browser.md` collega subito i file; la prossima esportazione aggiorna la panoramica.

I permessi HTTPS opzionali servono per rilevare i CDN del player: il codice limita il rilevamento
alle schede la cui pagina principale è Moodle FFHS. Puoi revocarli dalle impostazioni dell'estensione.
**YouTube e il suo CDN sono esclusi**: nell'archivio rimane il link. Non vengono aggirati DRM o MFA.

## Cosa è verificato e cosa resta da provare

I test esercitano download, byte scritti, playlist locali, limiti, errori e importazione. Un filmato
con audio viene anche generato, esportato e decodificato offline con ffmpeg quando disponibile.
I test dell'estensione simulano gli eventi Chrome e le risposte di rete: non sono una prova sul
tuo account né sul player FFHS reale. Il test grafico Windows, SSO/MFA e Kaltura FFHS resta da fare.

Gli HLS cifrati, i live, i byte range e i DASH non sono supportati dall'assistente. I file rilevati
dal browser possono essere parziali o dipendere da particolari intestazioni: aprili per verificarli.
La versione non promette che ogni contenuto visibile nel browser sia scaricabile o esposto dalle API.
`video_e_media.md` ed `elementi_mancanti.md` mantengono distinti gli esiti e i contenuti ancora online;
l'importazione manuale non dichiara automaticamente risolte tutte le lacune del corso.

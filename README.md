# FFHS Moodle Archiver

Applicazione locale Windows per esportare corsi Moodle FFHS, documenti, testi e video supportati.

**[Scarica il pacchetto pronto all'uso v0.4](https://github.com/invernizziluca/Moodle/raw/refs/tags/v0.4.0/download/FFHS_Archiver_v0.4.zip)**

1. Estrai tutto in una cartella nuova e breve, per esempio `C:\FFHS`.
2. Apri `FFHS Moodle Archiver.pyw` oppure `Avvia FFHS Moodle Archiver.bat`.
3. Serve Python 3.10+ con Tk. Non servono pacchetti pip.
4. Esporta inizialmente un solo corso e confrontalo con Moodle.

Il pacchetto usa percorsi brevi e non contiene le vecchie cartelle dimostrative che causavano
l'errore Windows `0x80010135`.

Per **Kaltura/SWITCHcast**, premi **Video Kaltura / browser…** e segui l'installazione una volta
dell'assistente Edge/Chrome incluso. Avvia il video dentro Moodle, rilevalo e scaricalo. Gli HLS
supportati vengono salvati con i segmenti: apri `video.m3u8` con VLC e importa tutta la cartella
usando **Importa cartella video HLS…**. Per i file diretti usa **Importa file dal browser…**.
**YouTube resta soltanto un link.**

La versione è di prova: i test offline passano, ma Windows/SSO e lo specifico player FFHS non sono
stati verificati nel cloud. DRM, HLS cifrati/live/byte range e DASH non sono supportati dall'assistente.

Leggi [la guida completa](LEGGIMI.md), [le novità e le istruzioni video](docs/NOVITA_0.4.md)
e [la guida dell'assistente](browser_helper/README.md). Il codice corrente è sul branch `main`.

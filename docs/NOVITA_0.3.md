# Versione 0.3 — video e contenuti mancanti

Note storiche della precedente release. Per i pulsanti e il flusso attuali con le estensioni
reali consulta [Novità 0.5](NOVITA_0.5.md) e la guida corrente.

La 0.3 estende l'individuazione dei file video diretti di Moodle: oltre ai materiali elencati nelle
attività, riconosce gli indirizzi presenti nelle attività Link (URL) e nei tag `iframe`, `object` ed
`embed`. Il download usa lo stesso servizio autenticato degli altri file e rispetta l'opzione video
e il limite di dimensione configurati. Il file viene collegato al contesto dell'attività nell'archivio.

Un player incorporato non coincide necessariamente con un file video. I provider esterni, i player
non esportati e i manifest HLS (`.m3u8`) o DASH (`.mpd`) vengono conservati come link e segnalati
nel rapporto `elementi_mancanti.md`. Questa versione non scarica o ricompone stream esterni e non
aggira DRM, MFA o permessi. La disponibilità di un download ufficiale del provider va verificata
nel suo player; un file ottenuto in quel modo può essere aggiunto manualmente alla cartella attività.

Il rapporto distingue il materiale effettivamente scaricato dai contenuti rimasti online. Non può
certificare la completezza delle API Moodle, trovare materiale nascosto o ricostruire tutti i libri
citati in documenti come il Modulplan. I riferimenti bibliografici e la loro eventuale disponibilità
locale restano da verificare separatamente.

Il nuovo `video_e_media.md`, collegato alla panoramica del corso, raccoglie in una tabella gli stati
**Scaricato**, **Escluso**, **Download fallito** e **Solo link**, con il file locale quando presente,
la fonte e il contesto dell'attività. Il limite per file vale anche quando le API non comunicano
la dimensione: il trasferimento viene interrotto se la risposta supera il limite impostato.
Gli errori sul disco compaiono anche nel rapporto dei mancanti.

## Prova consigliata su Windows

1. Avvia la nuova cartella dell'applicazione con `FFHS Moodle Archiver.pyw`, mantenendo una copia
   dell'archivio precedente. Esegui l'accesso nel browser soltanto se richiesto dal programma.
2. Esegui **Verifica accesso**, quindi **Anteprima del corso** su un solo corso, per esempio BWL8.
3. Spunta quel corso, abilita **Video caricati in Moodle** e scegli una cartella di prova separata.
   Avvia l'esportazione.
4. Confronta con Moodle `verifica_corso.md`, i link locali in `course_overview.md` e i file video
   scaricati: aprili localmente e controlla che siano i contenuti attesi, non pagine di login.
5. Apri `video_e_media.md` ed `elementi_mancanti.md`: controlla stati, motivi e link dei video ancora online. Un link conservato
   non equivale a un video disponibile offline.
6. Ripeti l'esportazione nella stessa cartella: i file invariati devono essere riutilizzati e i
   contenuti mancanti devono restare visibili. Solo dopo questa prova estendi agli altri corsi.

I test offline non dimostrano il funzionamento sulla FFHS o su Windows. In cloud non è stato
eseguito un accesso FFHS live. L'archivio BWL8 allegato supera 32 MiB, quindi non è stato trasferito
né verificato: non costituisce una prova della nuova versione.

# Versione 0.5 — Moodle Buddy e FetchV reali

## Aggiornamento 0.5.1 — meno passaggi per i casi dubbi

I file da associare si sistemano nella stessa finestra: scheda **File da collegare**, scelta
dell'attività e **Collega selezionati**. Non occorre riaprire la raccolta o selezionare nuovamente
i download. Sono recuperati anche quelli importati con la 0.5; le scelte vengono salvate e rispettate
nelle importazioni successive. Nessuna copia aggiuntiva dei materiali durante l'assegnazione.

Il messaggio con 7 nuovi, 8 associati e 4 da associare mescolava copie uniche e occorrenze nei
download. Ora i file conservati e i collegamenti da scegliere sono indicati separatamente.
Niente popup informativo dopo l'importazione. I pulsanti sono numerati e i nomi dei file sono
visibili prima dei lunghi percorsi del corso. La scheda rimane disponibile per riprendere più tardi.
L'indice collega l'ultimo stato delle associazioni e distingue i rapporti storici.

## Base 0.5

La base è la 0.3 aggiornata allegata dall'utente: layout leggibile per Obsidian, note nelle sezioni,
cartelle allegati, pagina iniziale e indice dei file. Queste funzioni sono mantenute insieme alle
correzioni per percorsi ZIP e URL `?file=`.

Il nuovo flusso grafico sostituisce l'assistente della 0.4 con **Documenti con Moodle Buddy…** e
**Video con FetchV…**. Prepara la struttura se manca senza scaricare materiali o chiedere auto-login.
Poi apre la pagina Moodle, l'utente usa l'estensione vera e conferma la fine dei download. L'app
mostra la selezione prima di copiare e organizzare. Sono supportati anche file già scaricati.

L'abbinamento automatico dei documenti usa nomi esatti univoci; i dubbi restano Da associare oppure
si sceglie esplicitamente l'attività. FetchV richiede sempre la scelta dell'attività. Gli originali
sono conservati e i duplicati vengono riutilizzati. I rapporti sono in `download_estensioni.md`.

La verifica ricevuta il 28 ottobre riportava un accesso Moodle valido e un cooldown di sei minuti
all'auto-login Kaltura. La verifica ordinaria 0.5 non esegue quel probe. Le impostazioni precedenti
vengono migrate al flusso con estensioni, senza richiedere il token privato video.

Le estensioni rimangono separate: nessun codice FetchV è incorporato, nessuna terza estensione
è necessaria e i clic per il download restano nel browser come previsto dall'alternativa 1.

Fonti verificate:

- FetchV Edge: https://microsoftedge.microsoft.com/addons/detail/dbepbhhcmhodojepbagfppgpieeplpik
- Manifest Microsoft: https://microsoftedge.microsoft.com/addons/getproductdetailsbycrxid/dbepbhhcmhodojepbagfppgpieeplpik
- Moodle Buddy: https://moodlebuddy.com/
- Sorgente AGPL-3.0: https://github.com/marcelreppi/moodle-buddy
- Opzioni download: https://github.com/marcelreppi/moodle-buddy/blob/main/extension/src/shared/defaultExtensionOptions.ts

Test locali verificano byte, ZIP, associazioni, rollback, deduplica e link. La GUI è stata provata
con import reali su display Linux. Windows/FFHS e i download delle estensioni richiedono una prova
sul PC dell'utente; il rapporto della precedente versione non certifica questa release.

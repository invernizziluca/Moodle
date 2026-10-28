# FFHS Moodle Archiver 0.6 — cartella per AI

1. Estrai l'app in una cartella nuova e breve, ad esempio C:\FFHS. Serve Python 3.10+ con Tk, nessun pacchetto pip.
2. Avvia «Avvia FFHS Moodle Archiver.bat».
3. Per aggiungere testi Moodle, accedi ed evidenzia un corso. Puoi anche usare «Prepara download locali per AI (senza login)».
4. Premi «Prepara corso per AI», poi «Apri cartella dove mettere i download».
5. Copia in questa cartella quello che scarichi con Moodle-DL, Moodle Buddy, FetchV o altri strumenti. Sottocartelle libere; una cartella per corso. Puoi scegliere anche una cartella di download già esistente.
6. Premi **PREPARA PER AI**. Il programma apre il risultato.

## Cosa dare all'AI

Accanto alla cartella di ingresso vengono creati `<nome>_PRONTO_AI` e `<nome>_PRONTO_AI.zip`.
La cartella contiene INIZIA_QUI.md, MATERIALI, il catalogo delle fonti, istruzioni suggerite per l'AI e, se richiesti, TESTI_MOODLE.md.
Usa la cartella intera o lo ZIP se il servizio li supporta; altrimenti carica i file al suo interno. Dimensioni, formati e numero di fonti dipendono dal servizio AI: non esiste un pacchetto accettato senza limiti da qualsiasi AI.

PDF, documenti, slide, testi, immagini, sottotitoli, audio e video vengono copiati. I file identici sono presenti una sola volta, conservando i percorsi di provenienza nel catalogo. File tecnici come JSON di configurazione, cache e download incompleti non sono inclusi: gli esclusi sono elencati nell'indice.
I file ZIP nei download vengono conservati senza estrazione: estraili prima nella cartella d'ingresso per ottenere documenti direttamente leggibili dall'AI. I video restano file originali: non vengono trascritti automaticamente. Usa audio/trascrizioni o un servizio in grado di elaborare i video. L'indice segnala questi casi.

## Originali e aggiornamenti

I download originali non vengono spostati o cancellati. Aggiungi nuovi materiali e premi nuovamente PREPARA PER AI. Il pacchetto generato viene ricostruito, evitando copie identiche. Non modificare i file dentro PRONTO_AI: le tue aggiunte vanno nella cartella d'ingresso, altrimenti saranno sostituite dalla preparazione successiva.
L'app rifiuta di sostituire una cartella preesistente che non riconosce come proprio pacchetto. Un errore di preparazione conserva il pacchetto precedente.

## Moodle è opzionale

Con un corso evidenziato e una sessione valida puoi aggiungere struttura e testi Moodle. Non vengono riscaricati documenti o video, né richiesto auto-login Kaltura. Forum, consegne e revisioni dei quiz sono esclusi da questo flusso di studio. Se usi soltanto Moodle-DL puoi lavorare senza login.
Le vecchie funzioni di esportazione e importazione delle estensioni restano accessibili come funzioni avanzate. I vecchi archivi non vengono migrati o cancellati automaticamente.

Il programma prepara le fonti, non genera riassunti e non carica dati online su servizi AI. Le fonti possono essere incomplete: il pacchetto non certifica che tutto Moodle sia stato scaricato. Chiedi all'AI di citare documento/pagina o minuto e di segnalare materiali non leggibili.

# Sviluppo e verifiche locali

Il progetto usa la libreria standard di Python 3.10 o successivo. Non serve `pip install`.
La GUI è destinata a Windows con Tk; l'esportatore e i test offline possono essere eseguiti su Linux.

Dal checkout, ad esempio `/workspace/Moodle` nell'ambiente cloud:

```bash
cd /workspace/Moodle
FFHS_ARCHIVER_HOME="$(mktemp -d)" python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m ffhs_archiver --help
```

La prima istruzione usa una cartella applicativa temporanea senza credenziali esistenti.
I test che richiedono un Moodle di laboratorio autenticato vengono saltati: uno skip non dimostra
che login e download reali funzionino. Non eseguire `tests/e2e_login.py` o altre prove di accesso
come sostituti dei test offline.

I test del codice Windows che simulano registro o canali locali non sostituiscono una verifica
su Windows di DPAPI, protocollo `moodlemobile://`, ripristino del registro, browser SSO e MFA.
Non chiedere password, token o cookie in chat e non inserirli nei test o negli archivi di esempio.

Non è stata effettuata una validazione FFHS live nell'ambiente cloud. Il rapporto allegato
dell'utente descrive una prova precedente sul suo computer; non è un risultato dei test qui eseguiti.
L'archivio allegato `BWL8_Decision_Making_and_Change.zip` supera il limite di trasferimento
di 32 MiB e non è stato aperto né verificato qui.

La prova reale consigliata per Windows è descritta in [Novità 0.3](NOVITA_0.3.md).

La versione corrente è 0.4: vedi anche [Novità 0.4](NOVITA_0.4.md) e `browser_helper/README.md`.
I test dell'assistente richiedono Node soltanto per lo sviluppo, non per usare l'app su Windows.
Il test di playback HLS usa ffmpeg se presente, altrimenti viene saltato esplicitamente.

Per creare lo ZIP utente con percorsi brevi:

```bash
python3 tools/package_release.py --output /tmp/FFHS_Archiver_v0.4.zip
```

Il campione storico è conservato in `docs/esempio_archivio/esempio_v02.zip`; non eseguire il
validatore sulla sua cartella esterna come se fosse un archivio di corsi. Non serve estrarlo per
avviare l'app. I nuovi archivi temporanei creati dai test vengono verificati direttamente.

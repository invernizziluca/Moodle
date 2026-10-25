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

# Sviluppo 0.5.1

Python 3.10+, libreria standard e Tk. Nessuna dipendenza pip, browser dedicato o estensione nostra.
Da `/workspace/Moodle` esegui test isolati da eventuali credenziali:

```bash
python3 -m ffhs_archiver --help
python3 - <<'PY'
import os, subprocess, sys, tempfile
with tempfile.TemporaryDirectory(prefix='ffhs-tests-') as home:
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                            env=dict(os.environ, FFHS_ARCHIVER_HOME=home))
sys.exit(result.returncode)
PY
```

I test di laboratorio autenticati sono saltati quando il servizio non è disponibile. Gli skip
non validano FFHS. Le funzioni video avanzate della base allegata conservano i propri test, ma
il flusso grafico scelto non le attiva.

Il nuovo flusso è verificato da `test_extension_bridge.py`, `test_extension_import.py`,
`test_extension_workflow.py` e `test_browser_import.py`: file/ZIP reali, byte, link, rollback,
deduplica, scelta della destinazione, metadati senza download/auto-login e rifiuto dei file modificati.

La 0.5.1 aggiunge recupero dei registri 0.5, assegnazione delle copie già archiviate, persistenza
nelle reimportazioni, indice aggiornato preservato dalla riesportazione e scansioni successive
che escludono solo i download già importati. Suite verificata: 194 test, 185 passati, 9 saltati.

```bash
python3 tools/package_release.py --output /tmp/FFHS_Archiver_v0.5.1.zip
```

Il vecchio campione profondo è in `docs/esempio_archivio/esempio_v02.zip` e non serve per l'avvio.
Non validare la sua cartella esterna come archivio. Per un archivio reale o temporaneo esportato:
`python3 tests/verify_archive.py <cartella_archivio>`.

Smoke GUI eseguito con Xorg dummy rootless temporaneo: App e dialoghi Tk reali, pulsanti e import
reali, zero errori Tk. MP4 con video/audio generato, importato con SHA identico e decodificato offline.
Solo lancio del browser simulato; nessun login FFHS reale o test delle estensioni installate.
Il server di prova è stato fermato senza modificare policy o installazioni del sistema.

Per ripetere lo smoke con un display già disponibile:
`python3 tools/smoke_extension_gui.py`. La prova 0.5.1 include ZIP ambiguo, recupero vecchi registri,
assegnazione nella stessa finestra senza ricopia e layout a 960×740 e 760×600. Tutti i dati sono
temporanei; non serve un accesso FFHS.

# Sviluppo 0.5

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

```bash
python3 tools/package_release.py --output /tmp/FFHS_Archiver_v0.5.zip
```

Il vecchio campione profondo è in `docs/esempio_archivio/esempio_v02.zip` e non serve per l'avvio.
Non validare la sua cartella esterna come archivio. Per un archivio reale o temporaneo esportato:
`python3 tests/verify_archive.py <cartella_archivio>`.

Smoke GUI eseguito con Xorg dummy rootless temporaneo: App e dialoghi Tk reali, pulsanti e import
reali, zero errori Tk. MP4 con video/audio generato, importato con SHA identico e decodificato offline.
Solo lancio del browser simulato; nessun login FFHS reale o test delle estensioni installate.
Il server di prova è stato fermato senza modificare policy o installazioni del sistema.

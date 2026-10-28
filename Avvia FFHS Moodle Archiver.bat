@echo off
rem Avvio alternativo: usalo se il doppio clic su "FFHS Moodle Archiver.pyw" apre un editor invece del programma.
setlocal
set "APP=%~dp0FFHS Moodle Archiver.pyw"
where pyw >nul 2>nul && (start "" pyw "%APP%" & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw "%APP%" & exit /b 0)
where py >nul 2>nul && (start "" py "%APP%" & exit /b 0)
where python >nul 2>nul && (start "" python "%APP%" & exit /b 0)
echo.
echo Python non e' stato trovato.
echo Installa Python 3.10 o piu' recente da https://www.python.org/downloads/ e riprova.
echo.
pause

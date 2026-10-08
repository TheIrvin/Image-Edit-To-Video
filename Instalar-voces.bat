@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto error
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto error
)
.venv\Scripts\python.exe scripts\install_voice.py
if errorlevel 1 goto error
echo Motor de voz listo.
pause
exit /b 0
:error
echo Fallo la instalacion. Revisa el mensaje anterior o los registros de data\jobs.
pause

@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto error
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto error
)
.venv\Scripts\python.exe run.py
if errorlevel 1 goto error
exit /b 0
:error
echo No se pudo iniciar. Instala Python 3.12 y revisa el mensaje anterior.
pause

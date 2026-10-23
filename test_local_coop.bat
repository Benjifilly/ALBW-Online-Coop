@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\pythonw.exe" (
    echo Lancez d'abord run_coop.bat pour installer les composants.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m src.local_session

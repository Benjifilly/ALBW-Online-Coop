@echo off
setlocal
chcp 65001 >nul
title ALBW Online Co-op
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" goto components

echo.
echo   ALBW Online Co-op
echo   Preparation du premier lancement...
call :find_python
if not defined PY (
    echo   Python 3.11 ou plus recent est introuvable.
    echo   Installation de Python 3.13 pour votre compte Windows, via winget...
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    call :find_python
)
if not defined PY goto python_error
%PY% -m venv .venv
if errorlevel 1 goto setup_error

:components
rem Ce bloc reste en memoire si la mise a jour remplace ce fichier.
(
    .venv\Scripts\python.exe -m src.updater
    if errorlevel 10 if not errorlevel 11 (
        call "%~f0" %*
        exit /b
    )
)
.venv\Scripts\python.exe -c "import os; os.environ['PYGAME_HIDE_SUPPORT_PROMPT']='1'; import PyQt6.QtWidgets, pygame" >nul 2>&1
if errorlevel 1 (
    echo   Installation des composants : PyQt6, pygame...
    .venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto setup_error
)
if "%~1"=="" (
    .venv\Scripts\python.exe -m src.launcher
) else (
    .venv\Scripts\python.exe -m src.main %*
)
if errorlevel 1 (
    echo.
    echo   Le lancement a echoue. Le message ci-dessus indique la cause.
    pause
    exit /b 1
)
exit /b 0

:find_python
rem The Microsoft Store "python" alias fails this test; a fresh winget
rem install is found in its folder before PATH is refreshed.
set "PY="
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && (set "PY=python" & goto :eof)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && (set "PY=py -3" & goto :eof)
for %%V in (313 312 311) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :eof
    )
)
goto :eof

:python_error
echo.
echo   Python n'a pas pu etre installe automatiquement.
echo   Installez-le depuis https://www.python.org/downloads/ (cochez "Add python.exe to PATH"),
echo   puis relancez run_coop.bat.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:setup_error
echo.
echo   Preparation impossible. Verifiez votre connexion Internet.
pause
exit /b 1

@echo off
setlocal
cd /d "%~dp0"
echo.
echo === Installation OneNote Voice Notes ===
echo.
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
  goto install
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  "%LocalAppData%\Programs\Python\Python312\python.exe" -m venv .venv
  goto install
)
echo Python Launcher ^(py^) introuvable.
echo Le Python actuellement present est Anaconda 3.8.3, trop ancien pour Whisper.
echo.
echo Installez Python 3.10, 3.11 ou 3.12 pour votre utilisateur,
echo cochez "Add python.exe to PATH", puis relancez ce fichier.
echo Cette operation ne necessite normalement pas les droits administrateur :
echo choisissez "Install Now" et non "Customize installation".
echo.
pause
exit /b 1

:install
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Installation terminee. Lancez start.bat.
pause

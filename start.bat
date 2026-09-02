@echo off
cd /d "%~dp0"
echo.
echo === Demarrage OneNote Voice Notes ===
if not exist .venv\Scripts\pythonw.exe (
  echo L'application n'est pas encore installee.
  echo Double-cliquez d'abord sur install.bat puis lisez le message affiche.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul
if errorlevel 1 (
  echo L'environnement actuel utilise une version de Python non compatible.
  echo Installez Python 3.10, 3.11 ou 3.12, puis relancez install.bat.
  pause
  exit /b 1
)
start "Voice Notes" /b .venv\Scripts\pythonw.exe app.py

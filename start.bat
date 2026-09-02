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
start "Voice Notes" /b .venv\Scripts\pythonw.exe app.py

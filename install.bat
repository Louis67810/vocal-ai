@echo off
setlocal
cd /d "%~dp0"
echo.
echo === Installation OneNote Voice Notes ===
echo.
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -m venv .venv >nul 2>nul && goto install
  py -3.11 -m venv .venv >nul 2>nul && goto install
  py -3.10 -m venv .venv >nul 2>nul && goto install
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  "%LocalAppData%\Programs\Python\Python312\python.exe" -m venv .venv
  goto install
)
echo Aucune version compatible de Python n'a ete trouvee.
echo Python 3.10, 3.11 ou 3.12 est necessaire. Python 3.13 et 3.14 ne sont pas pris en charge par ce projet.
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
where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js est necessaire pour l'interface React. Installez la version LTS depuis nodejs.org.
  pause
  exit /b 1
)
npm.cmd install
echo.
echo Installation terminee. Lancez start.bat.
pause

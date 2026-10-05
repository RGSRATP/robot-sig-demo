@echo off
rem Lance le démonstrateur SIG : construit l'interface si besoin, démarre le serveur et ouvre le navigateur.
rem Pour reconstruire l'interface après une modification : « lancer build ».
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Environnement Python absent. Dans ce dossier, lancez d'abord :
  echo   py -3.12 -m venv .venv
  echo   .venv\Scripts\python -m pip install --proxy http://127.0.0.1:9000 -r serveur\requirements.txt
  pause
  exit /b 1
)

if /i "%~1"=="build" rmdir /s /q "interface\dist" 2> nul
if not exist "interface\dist\index.html" (
  echo Construction de l'interface...
  pushd interface
  if not exist "node_modules" call npm install
  call npm run build
  popd
  if not exist "interface\dist\index.html" (
    echo La construction de l'interface a échoué.
    pause
    exit /b 1
  )
)

rem Un serveur déjà lancé (ancienne version) occuperait le port : le navigateur s'ouvrirait dessus.
netstat -ano | findstr /r /c:":8000 .*LISTENING" > nul
if not errorlevel 1 (
  echo Un serveur occupe déjà le port 8000, probablement un démonstrateur lancé précédemment.
  echo Fermez sa fenêtre ^(ou Ctrl+C dedans^), puis relancez « lancer ».
  pause
  exit /b 1
)

echo.
echo Démonstrateur SIG : http://127.0.0.1:8000/   (fermer cette fenêtre ou Ctrl+C pour arrêter)
echo.
start "" "http://127.0.0.1:8000/"
".venv\Scripts\python.exe" -m uvicorn serveur.sig.app:app --port 8000
pause

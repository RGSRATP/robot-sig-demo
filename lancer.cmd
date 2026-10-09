@echo off
rem Lance Synergie-SIG-BDM-POC (démonstrateur SIG) : construit l'interface si besoin, démarre le serveur et ouvre le navigateur.
rem L'interface est reconstruite automatiquement si une source est plus récente que la dernière construction.
rem Pour forcer la reconstruction : « lancer build ».
chcp 65001 > nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Environnement Python absent. Dans ce dossier, lancez d'abord :
  echo   py -3.12 -m venv .venv
  echo   .venv\Scripts\python -m pip install --proxy http://127.0.0.1:9000 -r serveur\requirements.txt
  pause
  exit /b 1
)

if /i "%~1"=="build" rmdir /s /q "interface\dist" 2> nul

rem Dépendances de l'interface absentes ou package.json modifié (nouvelle bibliothèque) : npm install.
set "INSTALLER="
if not exist "interface\node_modules\.package-lock.json" set "INSTALLER=1"
if not defined INSTALLER (
  powershell -NoProfile -Command "if ((Get-Item 'interface\package.json').LastWriteTime -gt (Get-Item 'interface\node_modules\.package-lock.json').LastWriteTime) { exit 1 }"
  if errorlevel 1 set "INSTALLER=1"
)
if defined INSTALLER (
  echo Installation des dépendances de l'interface...
  pushd interface
  call npm install --proxy http://127.0.0.1:9000 --https-proxy http://127.0.0.1:9000
  popd
  rmdir /s /q "interface\dist" 2> nul
)

rem Convertisseur 3D des maquettes (web-ifc, Node) : dépendances absentes ou package.json modifié.
set "INSTALLER="
if not exist "commun\ifc\convertisseur\node_modules\.package-lock.json" set "INSTALLER=1"
if not defined INSTALLER (
  powershell -NoProfile -Command "if ((Get-Item 'commun\ifc\convertisseur\package.json').LastWriteTime -gt (Get-Item 'commun\ifc\convertisseur\node_modules\.package-lock.json').LastWriteTime) { exit 1 }"
  if errorlevel 1 set "INSTALLER=1"
)
if defined INSTALLER (
  echo Installation du convertisseur 3D des maquettes...
  pushd commun\ifc\convertisseur
  call npm install --proxy http://127.0.0.1:9000 --https-proxy http://127.0.0.1:9000
  popd
)

rem Interface construite mais plus ancienne que ses sources : on la reconstruit.
if exist "interface\dist\index.html" (
  powershell -NoProfile -Command "$d = (Get-Item 'interface\dist\index.html').LastWriteTime; $s = Get-ChildItem 'interface\sig','interface\main.js','interface\index.html','interface\vite.config.js','interface\package.json' -Recurse -File | Where-Object { $_.LastWriteTime -gt $d }; if ($s) { exit 1 }"
  if errorlevel 1 (
    echo Sources de l'interface modifiées depuis la dernière construction.
    rmdir /s /q "interface\dist" 2> nul
  )
)

if not exist "interface\dist\index.html" (
  echo Construction de l'interface...
  pushd interface
  call npm run build
  popd
  if not exist "interface\dist\index.html" (
    echo La construction de l'interface a échoué.
    pause
    exit /b 1
  )
)

rem Un serveur déjà lancé (ancienne version) occupe le port : le navigateur s'ouvrirait dessus.
set "PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do set "PID=%%p"
if defined PID (
  tasklist /fi "PID eq %PID%" /nh | findstr /i "python" > nul
  if errorlevel 1 (
    echo Le port 8000 est occupé par une autre application ^(processus %PID%^) : libérez-le puis relancez.
    pause
    exit /b 1
  )
  echo Un ancien serveur du démonstrateur tourne encore sur le port 8000 ^(processus %PID%^).
  choice /c ON /m "L'arrêter et lancer la version actuelle"
  if errorlevel 2 (
    echo Lancement annulé : l'ancien serveur reste actif.
    pause
    exit /b 1
  )
  taskkill /pid %PID% /f > nul
  timeout /t 2 /nobreak > nul
)

echo.
echo Synergie-SIG-BDM-POC : http://127.0.0.1:8000/   (fermer cette fenêtre ou Ctrl+C pour arrêter)
echo.
start "" "http://127.0.0.1:8000/"
".venv\Scripts\python.exe" -m uvicorn serveur.sig.app:app --port 8000
pause

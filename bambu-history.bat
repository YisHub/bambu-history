@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title Bambu Print History

REM ============================================================================
REM  Equivalente a:  SERVE=1 REFRESH_INTERVAL=300 docker compose up bambu-history
REM  pero sin Docker: usa Python directo con un entorno virtual propio.
REM  Lo que ya este en el entorno gana, igual que en Linux.
REM ============================================================================
if not defined SERVE set "SERVE=1"
if not defined REFRESH_INTERVAL set "REFRESH_INTERVAL=300"

echo.
echo   Bambu Print History
echo   -------------------
echo.

REM --- 1. Buscar Python ---------------------------------------------------
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :hay_python
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :hay_python

echo   [ERROR] No se encontro Python.
echo.
echo   Instalalo desde https://www.python.org/downloads/
echo   IMPORTANTE: tildar "Add python.exe to PATH" durante la instalacion.
echo.
pause
exit /b 1

:hay_python
echo   [1/4] Python encontrado.

REM --- 2. Entorno virtual -------------------------------------------------
if exist ".venv\Scripts\python.exe" goto :hay_venv
echo   [2/4] Creando entorno virtual (solo la primera vez)...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   [ERROR] No se pudo crear el entorno virtual.
    pause
    exit /b 1
)
goto :venv_listo

:hay_venv
echo   [2/4] Entorno virtual ya existe.

:venv_listo
set "VENVPY=.venv\Scripts\python.exe"

REM --- 3. Dependencias ----------------------------------------------------
"%VENVPY%" -c "import requests, PIL" >nul 2>&1
if errorlevel 1 (
    echo   [3/4] Instalando dependencias...
    "%VENVPY%" -m pip install --quiet --upgrade pip
    "%VENVPY%" -m pip install --quiet -r requirements.txt
) else (
    echo   [3/4] Dependencias OK.
)
"%VENVPY%" -c "import requests, PIL" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

REM --- 4. Configuracion ---------------------------------------------------
if exist ".env" goto :hay_env
if not exist ".env.example" (
    echo.
    echo   [ERROR] Falta .env.example. Bajaste el repo completo?
    pause
    exit /b 1
)
copy ".env.example" ".env" >nul
echo.
echo   Se creo el archivo .env  --^>  completa BAMBU_EMAIL y BAMBU_PASSWORD
echo   Lo abro en el Bloc de notas: guardalo y volve a ejecutar este archivo.
echo.
notepad .env
pause
exit /b 0

:hay_env
echo   [4/4] Configuracion OK.

REM Puerto del visor: entorno > .env > default 8766
if not defined VIEWER_PORT for /f "tokens=2 delims==" %%p in ('findstr /b /c:"VIEWER_PORT=" .env 2^>nul') do set "VIEWER_PORT=%%p"
if not defined VIEWER_PORT set "VIEWER_PORT=8766"
echo.
echo   Modo servidor: SERVE=%SERVE%  REFRESH_INTERVAL=%REFRESH_INTERVAL%
echo   La primera vez puede pedir un codigo de 6 digitos que llega por mail.
echo   Para cortar: Ctrl+C, o cerra esta ventana.
echo.

"%VENVPY%" -u bambu_history.py

echo.
echo   ============================================================
echo     Visor:  http://localhost:%VIEWER_PORT%/historial.html
echo.
echo     Si cerraste el servidor, el visor igual se puede abrir
echo     como archivo:  output\historial.html
echo   ============================================================
echo.
pause

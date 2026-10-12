@echo off
REM ============================================================
REM [DEMO] Empaqueta el SIMULADOR de báscula para demostraciones.
REM        En producción NO se usa este exe: se despliega el
REM        SERVICIO de báscula. Ver guia_integracion_bascula_produccion.txt.
REM ============================================================
REM  build_exe.bat - Empaqueta simulador_gui.py en un .exe real
REM  con PyInstaller (un solo archivo, SIN consola de fondo).
REM
REM  Resultado:  dist\simulador_gui.exe
REM ============================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Build de simulador_gui.exe con PyInstaller
echo ============================================================
echo.

REM --- 1) Comprobar / instalar PyInstaller ---------------------
python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [i] PyInstaller no esta instalado. Instalando...
    python -m pip install --upgrade pyinstaller
    if errorlevel 1 goto :error
)

REM --- 2) Icono opcional (coloca assets\bascula.ico) -----------
set "ICONO_ARG="
if exist "assets\bascula.ico" (
    set "ICONO_ARG=--icon assets\bascula.ico"
    echo [i] Usando icono: assets\bascula.ico
) else (
    echo [i] Sin icono. Opcional: coloca assets\bascula.ico
)

REM --- 3) Empaquetado ------------------------------------------
REM   --onefile     : un unico .exe autocontenido
REM   --windowed    : modo GUI, sin ventana de consola de fondo
REM   --collect-submodules serial : asegura los backends de pyserial
echo.
echo [i] Empaquetando...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name simulador_gui ^
    %ICONO_ARG% ^
    --collect-submodules serial ^
    simulador_gui.py
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  [OK] Ejecutable generado:  dist\simulador_gui.exe
echo ============================================================
echo  Ejecuta con:  dist\simulador_gui.exe --puerto COM10
echo.
goto :eof

:error
echo.
echo [ERROR] El empaquetado fallo. Revisa el mensaje anterior.
exit /b 1

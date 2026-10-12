@echo off
REM ============================================================
REM [DEMO] Crea un par de puertos VIRTUALES (com0com) para simular
REM        un cable null-modem. Descartar en producción: se usa el
REM        hardware/puerto real de la báscula.
REM ============================================================
REM  crear_par_com0com.bat
REM  Crea el par de puertos virtuales COM10 <-> COM11 con com0com.
REM  Se auto-eleva a Administrador (acepta el UAC si aparece).
REM ============================================================

REM --- Autoelevacion ---
net session >nul 2>nul
if %errorlevel% neq 0 (
    echo [i] Solicitando privilegios de administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

setlocal
set "SETUPC="

REM --- Localizar setupc.exe en las rutas tipicas ---
if exist "%ProgramFiles(x86)%\com0com\setupc.exe" set "SETUPC=%ProgramFiles(x86)%\com0com\setupc.exe"
if not defined SETUPC if exist "%ProgramFiles%\com0com\setupc.exe" set "SETUPC=%ProgramFiles%\com0com\setupc.exe"
if not defined SETUPC if exist "%~dp0tools\com0com\manual\amd64\setupc.exe" set "SETUPC=%~dp0tools\com0com\manual\amd64\setupc.exe"

if not defined SETUPC (
    echo [ERROR] No se encontro setupc.exe. Instala com0com primero:
    echo         %~dp0tools\com0com\com0com-3.0.0.0-Win-x64_Setup-Signed.exe
    exit /b 1
)

echo [i] Usando: "%SETUPC%"
echo [i] Creando par COM10 ^<--^> COM11 ...

REM Idempotente: elimina el par si ya existia (ignora el error si no existe).
"%SETUPC%" remove PortName=COM10 >nul 2>nul
"%SETUPC%" install PortName=COM10 PortName=COM11

echo.
echo [i] Pares configurados:
"%SETUPC%" list

echo.
echo [OK] Listo. Ahora usa COM10 en el simulador y COM11 en el POS Web.
echo.
pause
endlocal

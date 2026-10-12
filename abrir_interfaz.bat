@echo off
REM ============================================================
REM [DEMO] Lanzador del SIMULADOR (demostración a clientes).
REM        Descartar en producción (se usa el servicio de báscula).
REM ============================================================
REM  abrir_interfaz.bat  -  Lanzador rapido de la interfaz
REM  Uso:  abrir_interfaz.bat [PUERTO]
REM  Ej.:  abrir_interfaz.bat COM10
REM  Si no se indica puerto, se usa COM1.
REM ============================================================
setlocal
cd /d "%~dp0"

set "PUERTO=COM1"
if not "%~1"=="" set "PUERTO=%~1"

REM Usa pythonw (sin ventana de consola) si esta disponible.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "interfaz_bascula.py" --puerto %PUERTO%
) else (
    start "" python "interfaz_bascula.py" --puerto %PUERTO%
)

endlocal

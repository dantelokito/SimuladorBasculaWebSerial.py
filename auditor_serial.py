#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [CONSERVAR] Herramienta de diagnóstico de puertos seriales. NO es demo:
#             se reutiliza en producción para validar el puerto de la báscula
#             real en el punto de venta (ver guias .txt, sección QA/arranque).
# =============================================================================
"""
auditor_serial.py
==================

Script de **diagnóstico y auditoría** de puertos seriales para el entorno de
simulación de báscula. Analiza el sistema operativo, busca los puertos virtuales
creados (com0com en Windows, socat/pty en Linux) e intenta abrir el puerto que
usará el navegador para verificar:

  1. Si el puerto **existe**.
  2. Si está **bloqueado** por otro proceso (Resource busy / Access denied).
  3. Si hay problemas de **permisos** (p. ej. no pertenecer al grupo ``dialout``
     en Linux).

Al terminar imprime un **reporte** claro y sugiere el comando exacto para
solucionar el problema encontrado.

Uso
---
    python auditor_serial.py --puerto COM4
    python auditor_serial.py --puerto /dev/pts/2
    python auditor_serial.py --listar

Códigos de salida
-----------------
    0  -> el puerto existe y se pudo abrir correctamente.
    1  -> el puerto existe pero no se pudo abrir (ocupado / permisos).
    2  -> el puerto no existe o no se detectó ningún puerto.
    3  -> error de dependencias / entorno (p. ej. falta pyserial).
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - dependencia externa
    print("ERROR: falta la dependencia 'pyserial'. Instálala con:")
    print("    pip install -r requirements.txt")
    sys.exit(3)

from registro_bascula import obtener_logger

_log = obtener_logger("auditor_serial")


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------

class Color:
    RESET = "\033[0m"
    ROJO = "\033[31m"
    VERDE = "\033[32m"
    AMARILLO = "\033[33m"
    CIAN = "\033[36m"
    NEGRITA = "\033[1m"


def _terminal_es_tty() -> bool:
    flujo = sys.stdout
    try:
        return flujo is not None and flujo.isatty()
    except (AttributeError, ValueError):
        return False


_USAR_COLOR = _terminal_es_tty()


def c(texto: str, color: str) -> str:
    return f"{color}{texto}{Color.RESET}" if _USAR_COLOR else texto


def titulo(texto: str) -> None:
    print()
    print(c(f"== {texto} ==", Color.NEGRITA + Color.CIAN))
    _log.info("== %s ==", texto)


def ok(texto: str) -> None:
    print(c(f"  [ OK ]  {texto}", Color.VERDE))
    _log.info("%s", texto)


def fallo(texto: str) -> None:
    print(c(f"  [FAIL]  {texto}", Color.ROJO))
    _log.error("%s", texto)


def aviso(texto: str) -> None:
    print(c(f"  [WARN]  {texto}", Color.AMARILLO))
    _log.warning("%s", texto)


def dato(texto: str) -> None:
    print(f"          {texto}")
    _log.info("%s", texto.strip())


def comando(texto: str) -> None:
    print(c(f"          $ {texto}", Color.CIAN))
    _log.info("SUGERENCIA: %s", texto)


# ---------------------------------------------------------------------------
# Resultado del análisis
# ---------------------------------------------------------------------------

@dataclass
class Resultado:
    puerto: str
    existe: bool = False
    abrible: bool = False
    error: Optional[str] = None
    categoria: str = "desconocido"   # ok | inexistente | ocupado | permisos | otro
    sugerencias: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilidades por sistema operativo
# ---------------------------------------------------------------------------

def puerto_por_defecto() -> str:
    return "COM4" if platform.system() == "Windows" else "/dev/pts/2"


def lista_puertos() -> list:
    return list(list_ports.comports())


def grupo_dialout() -> tuple[bool, Optional[str]]:
    """En Linux devuelve (pertenece_a_dialout, nombre_usuario)."""
    if platform.system() != "Linux":
        return (True, None)
    try:
        import grp  # solo disponible en POSIX
    except ImportError:
        return (True, None)

    usuario = os.environ.get("USER") or os.environ.get("LOGNAME")
    try:
        gid_dialout = grp.getgrnam("dialout").gr_gid
    except KeyError:
        return (True, usuario)  # no existe el grupo; no podemos culparlo

    grupos_usuario = set(os.getgroups())
    return (gid_dialout in grupos_usuario, usuario)


def proceso_que_usa(puerto: str) -> Optional[str]:
    """Intenta identificar qué proceso ocupa el puerto, si las herramientas existen."""
    try:
        if platform.system() == "Windows":
            salida = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-CimInstance Win32_SerialPort | Where-Object {{$_.DeviceID -eq '{puerto}'}} | Select-Object -ExpandProperty Description"],
                capture_output=True, text=True, timeout=10,
            )
            texto = (salida.stdout or "").strip()
            return texto or None
        else:
            salida = subprocess.run(
                ["lsof", puerto], capture_output=True, text=True, timeout=10
            )
            return (salida.stdout or "").strip() or None
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None


# ---------------------------------------------------------------------------
# Auditoría principal
# ---------------------------------------------------------------------------

def auditar(puerto: str) -> Resultado:
    res = Resultado(puerto=puerto)

    # 1) ¿Existe el puerto?
    titulo("1. Existencia del puerto")
    detectados = lista_puertos()
    if detectados:
        dato("Puertos detectados:")
        for p in detectados:
            dato(f"  - {p.device:<12} {p.description}")
    else:
        dato("No se detectó ningún puerto serial en el sistema.")

    coincide = any(p.device == puerto for p in detectados)
    en_fs = os.path.exists(puerto) if platform.system() != "Windows" else coincide

    if coincide or en_fs:
        res.existe = True
        ok(f"El puerto '{puerto}' existe.")
    else:
        res.existe = False
        res.categoria = "inexistente"
        fallo(f"El puerto '{puerto}' NO se encontró.")
        if platform.system() == "Windows":
            res.sugerencias += [
                f"Crea el par con com0com:  install PortName={puerto} PortName=<otro_COM_libre>",
                "Revisa los COM ocupados en el Administrador de dispositivos.",
            ]
        else:
            res.sugerencias += [
                "Crea el par con socat:  socat -d -d pty,raw,echo=0,link=/tmp/ttyBASCULA pty,raw,echo=0,link=/tmp/ttyPOS",
                "O usa par_pty.py para generar los pseudo-terminales.",
            ]
        return res

    # 2) Intento de apertura
    titulo("2. Apertura del puerto")
    try:
        conexion = serial.Serial(puerto, baudrate=9600, timeout=0.2)
        conexion.close()
        res.abrible = True
        res.categoria = "ok"
        ok("El puerto se abrió y cerró correctamente. Está libre.")
        return res
    except SerialException as exc:
        res.error = str(exc)
        fallo(f"No se pudo abrir: {exc}")
        categoria = clasificar_error(str(exc), puerto)
        res.categoria = categoria

    # 3) Diagnóstico específico
    titulo("3. Diagnóstico")
    if res.categoria == "ocupado":
        aviso("El puerto está BLOQUEADO por otro proceso.")
        ocupante = proceso_que_usa(puerto)
        if ocupante:
            dato("Posible proceso ocupante:")
            dato(ocupante)
        if platform.system() == "Windows":
            res.sugerencias += [
                "Cierra el programa que tiene abierto el puerto (simulador, otra terminal).",
                "Identifica el proceso:  Get-CimInstance Win32_SerialPort | Format-List",
            ]
        else:
            res.sugerencias += [
                "Identifica el proceso:  sudo lsof " + puerto,
                "Mata el proceso:        sudo fuser -k " + puerto,
            ]
    elif res.categoria == "permisos":
        aviso("Problema de PERMISOS sobre el puerto (Access denied).")
        pertenece, usuario = grupo_dialout()
        if platform.system() == "Linux" and usuario:
            if pertenece:
                dato(f"El usuario '{usuario}' YA pertenece al grupo 'dialout'.")
                dato("Puede requerir reiniciar la sesión para aplicar el grupo.")
            else:
                fallo(f"El usuario '{usuario}' NO pertenece al grupo 'dialout'.")
            res.sugerencias += [
                f"sudo usermod -aG dialout {usuario}",
                "Cierra sesión y vuelve a entrar (o ejecuta: newgrp dialout).",
                "Comprueba con:  groups",
            ]
        else:
            res.sugerencias += [
                "Ejecuta la terminal como administrador o revisa los permisos del puerto.",
            ]
    else:
        aviso("Error no clasificado al abrir el puerto.")
        res.sugerencias += [
            "Verifica que el baud rate y el par null-modem sean correctos.",
        ]

    return res


def clasificar_error(mensaje: str, puerto: str) -> str:
    texto = mensaje.lower()
    if ("access is denied" in texto or "permission" in texto
            or "errno 13" in texto or "permiso" in texto):
        return "permisos"
    if ("resource busy" in texto or "device or resource busy" in texto
            or "could not open port" in texto or "busy" in texto
            or "errno 16" in texto):
        return "ocupado"
    if ("no such file" in texto or "file not found" in texto
            or "could not open" in texto or "no existe" in texto):
        return "inexistente"
    return "otro"


# ---------------------------------------------------------------------------
# Reporte final
# ---------------------------------------------------------------------------

def imprimir_reporte(res: Resultado) -> None:
    titulo("REPORTE Y SUGERENCIAS")
    categorias = {
        "ok": ("El puerto está listo para usarse.", Color.VERDE),
        "inexistente": ("El puerto no existe. Créalo antes de continuar.", Color.ROJO),
        "ocupado": ("El puerto está ocupado por otro proceso.", Color.ROJO),
        "permisos": ("Faltan permisos para acceder al puerto.", Color.ROJO),
        "otro": ("Se produjo un error no catalogado.", Color.AMARILLO),
    }
    mensaje, color = categorias.get(res.categoria, categorias["otro"])
    print(c(f"  Estado: {mensaje}", color))
    if res.sugerencias:
        print("  Comandos sugeridos:")
        for sugerencia in res.sugerencias:
            comando(sugerencia)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parsear_argumentos(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auditor de puertos seriales para el simulador de báscula.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--puerto",
        default=os.environ.get("AUDITOR_PUERTO", puerto_por_defecto()),
        help="Puerto a auditar (COM4 o /dev/pts/2).",
    )
    parser.add_argument(
        "-l", "--listar",
        action="store_true",
        help="Solo lista los puertos disponibles y sale.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    if not hasattr(serial, "VERSION"):
        print("ERROR: pyserial no está correctamente instalado.")
        return 3

    args = parsear_argumentos(argv)

    titulo("ENTORNO")
    dato(f"Sistema operativo : {platform.system()} {platform.release()}")
    dato(f"Python            : {platform.python_version()}")
    dato(f"pyserial          : {serial.VERSION}")
    dato(f"Puerto analizado  : {args.puerto}")

    if args.listar:
        puertos = lista_puertos()
        titulo("PUERTOS DISPONIBLES")
        if not puertos:
            aviso("No se detectó ningún puerto serial.")
        for p in puertos:
            dato(f"  - {p.device:<12} {p.description}")
        return 0 if puertos else 2

    res = auditar(args.puerto)
    imprimir_reporte(res)

    if res.abrible:
        return 0
    if not res.existe:
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())

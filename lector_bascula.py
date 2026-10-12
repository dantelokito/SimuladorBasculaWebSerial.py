#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [DEMO] Lector POS de consola: consume las tramas de la báscula SIMULADA.
#        SOLO para demostraciones a clientes. DESCARTAR en producción:
#        el POS real consume el servicio de báscula (REST/WebSocket), no el
#        puerto directo (ver guia_pos_completo_produccion.txt). El patrón de
#        lectura con buffer y parseo [CONSERVAR] se reutiliza en el servicio.
# =============================================================================
"""
lector_bascula.py
==================

Cliente de lectura que simula el software de Punto de Venta (POS).

Se conecta al otro extremo del par de puertos seriales virtuales (null-modem),
lee las tramas que envía ``simulador_bascula.py``, las limpia y las muestra
en consola como haría una aplicación de facturación.

Uso
---
    python lector_bascula.py --puerto COM11 --baudrate 9600
    python lector_bascula.py --puerto /dev/pts/6

El formato esperado de cada trama es:

    PPP.PP KG\\r\\n

El programa extrae el número y la unidad, ignorando caracteres de control.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import threading
from datetime import datetime
from typing import Optional

import serial
from serial import SerialException
from serial.tools import list_ports

from registro_bascula import obtener_logger

_log = obtener_logger("lector_bascula")

BAUDRATE_POR_DEFECTO = 9600

# Expresión regular para extraer peso y unidad de una línea recibida.
# Acepta relleno de ceros, signos, espacios y las unidades más comunes.
PATRON_TRAMA = re.compile(
    r"^\s*(?P<peso>[+-]?\d{1,3}(?:\.\d{1,2})?)\s*(?P<unidad>KG|LB|G|OZ|T)?\s*$",
    re.IGNORECASE,
)

def _terminal_es_tty() -> bool:
    flujo = sys.stdout
    try:
        return flujo is not None and flujo.isatty()
    except (AttributeError, ValueError):
        return False


_USAR_COLOR = _terminal_es_tty()


def _c(texto: str, codigo: str) -> str:
    if not _USAR_COLOR:
        return texto
    return f"\033[{codigo}m{texto}\033[0m"


def info(msg: str) -> None:
    print(_c(f"[INFO] {msg}", "36"))
    _log.info(msg)


def ok(msg: str) -> None:
    print(_c(f"[ OK ] {msg}", "32"))
    _log.info(msg)


def error(msg: str) -> None:
    print(_c(f"[ERR ] {msg}", "31"), file=sys.stderr)
    _log.error(msg)


def advertir(msg: str) -> None:
    print(_c(f"[WARN] {msg}", "33"), file=sys.stderr)
    _log.warning(msg)


def limpiar_linea(bruto: bytes) -> str:
    """Convierte bytes crudos en texto limpio, sin CR/LF ni bytes basura."""
    texto = bruto.decode("ascii", errors="ignore")
    return texto.replace("\r", "").replace("\n", "").strip()


def parsear_peso(linea: str) -> Optional[tuple[float, str]]:
    """Extrae (peso, unidad) de una línea. Devuelve None si no es válida."""
    coincidencia = PATRON_TRAMA.match(linea)
    if not coincidencia:
        return None
    peso = float(coincidencia.group("peso"))
    unidad = (coincidencia.group("unidad") or "KG").upper()
    return peso, unidad


class LectorBascula:
    """Encapsula la conexión y el bucle de lectura del POS."""

    def __init__(self, puerto: str, baudrate: int) -> None:
        self.nombre_puerto = puerto
        self.baudrate = baudrate
        self.conexion: Optional[serial.Serial] = None
        self.detener = threading.Event()

    # -- conexión -----------------------------------------------------------
    def abrir(self) -> None:
        if not any(p.device == self.nombre_puerto for p in list_ports.comports()):
            advertir(
                f"El puerto '{self.nombre_puerto}' no aparece en la lista del sistema. "
                "Se intentará abrir de todos modos."
            )
        try:
            self.conexion = serial.Serial(
                port=self.nombre_puerto,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0,
            )
        except SerialException as exc:
            mensaje = str(exc).lower()
            if "access is denied" in mensaje or "permission" in mensaje:
                raise SerialException(
                    f"No se pudo abrir '{self.nombre_puerto}': el puerto está ocupado. "
                    "Cierra el proceso que lo esté usando."
                ) from exc
            if "could not open" in mensaje or "no such file" in mensaje:
                raise SerialException(
                    f"No se pudo abrir '{self.nombre_puerto}': el puerto no existe. "
                    "Verifica que el par null-modem esté creado y el nombre sea correcto."
                ) from exc
            raise

    def cerrar(self) -> None:
        if self.conexion is not None:
            try:
                if self.conexion.is_open:
                    self.conexion.close()
            except SerialException:
                pass

    # -- bucle --------------------------------------------------------------
    def ejecutar(self) -> None:
        assert self.conexion is not None

        def _manejador_senal(signum, frame):  # noqa: ANN001
            advertir("Cierre solicitado. Saliendo del lector...")
            self.detener.set()

        for senal in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(senal, _manejador_senal)
            except (ValueError, AttributeError, OSError):
                pass

        info(
            f"POS conectado a {self.nombre_puerto} @ {self.baudrate} bps. "
            "Esperando tramas de la báscula... (Ctrl+C para salir)"
        )

        buffer = b""
        ultima_lectura: Optional[tuple[float, str]] = None

        try:
            while not self.detener.is_set():
                try:
                    # readline respeta el timeout configurado y no bloquea indefinidamente.
                    trozo = self.conexion.readline()
                except serial.SerialTimeoutException:
                    continue
                except SerialException as exc:
                    error(f"Error de lectura serial: {exc}")
                    break

                if not trozo:
                    # Timeout sin datos: la báscula quizá se desconectó o está en silencio.
                    continue

                buffer += trozo
                while b"\n" in buffer:
                    cruda, buffer = buffer.split(b"\n", 1)
                    linea = limpiar_linea(cruda)
                    if not linea:
                        continue

                    resultado = parsear_peso(linea)
                    marca = datetime.now().strftime("%H:%M:%S")

                    if resultado is None:
                        advertir(f"[{marca}] Trama no reconocida: {linea!r}")
                        continue

                    peso, unidad = resultado
                    if resultado != ultima_lectura:
                        ok(f"[{marca}] PESO LEÍDO: {peso:08.2f} {unidad}")
                        ultima_lectura = resultado
                    else:
                        # Misma lectura: se muestra atenuada como "latido".
                        print(
                            _c(f"[{marca}] (sin cambios) {peso:08.2f} {unidad}", "90")
                        )

        finally:
            self.cerrar()
            info("Lector detenido. Puerto cerrado.")


def parsear_argumentos(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lector POS que consume tramas de la báscula simulada.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--puerto",
        default=os.environ.get("LECTOR_PUERTO", "COM2"),
        help="Puerto serial a leer (el extremo opuesto al simulador).",
    )
    parser.add_argument(
        "-b", "--baudrate",
        type=int,
        default=int(os.environ.get("LECTOR_BAUDRATE", BAUDRATE_POR_DEFECTO)),
        help="Velocidad en baudios.",
    )
    parser.add_argument(
        "-l", "--listar",
        action="store_true",
        help="Lista los puertos seriales disponibles y sale.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parsear_argumentos(argv)

    if args.listar:
        puertos = list(list_ports.comports())
        if not puertos:
            advertir("No se han detectado puertos seriales.")
        else:
            print("Puertos seriales detectados:")
            for p in puertos:
                print(f"  - {p.device:<12} {p.description}")
        return 0

    lector = LectorBascula(args.puerto, args.baudrate)
    try:
        lector.abrir()
    except SerialException as exc:
        error(str(exc))
        return 1

    try:
        lector.ejecutar()
    finally:
        lector.cerrar()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        advertir("Interrupción por teclado. Saliendo.")
        sys.exit(130)

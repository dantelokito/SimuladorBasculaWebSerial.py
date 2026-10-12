#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [DEMO] Par de pseudo-terminales (null-modem virtual) para pruebas en Linux.
#        SOLO para el entorno de demostración. DESCARTAR en producción:
#        se usa el hardware real (cable null-modem físico / puerto de la
#        báscula). Ver guia_integracion_bascula_produccion.txt.
# =============================================================================
"""
par_pty.py
===========

Crea un par de pseudo-terminales (pty) enlazados en espejo, emulando un cable
null-modem, sin necesidad de instalar ``socat``. Solo es necesario en Linux.

Expone dos enlaces simbólicos estables:

    /tmp/ttyBASCULA   -> extremo que abre el simulador de báscula
    /tmp/ttyPOS       -> extremo que abre el lector (POS)

Uso
---
    python par_pty.py

Déjalo corriendo en su propia terminal; al pulsar Ctrl+C se cierra y elimina
los enlaces creados.
"""

from __future__ import annotations

import os
import select
import sys

ENLACE_BASCULA = "/tmp/ttyBASCULA"
ENLACE_POS = "/tmp/ttyPOS"


def crear_enlace(ruta: str, destino: str) -> None:
    """Crea un enlace simbólico, reemplazándolo si ya existía."""
    if os.path.islink(ruta) or os.path.exists(ruta):
        os.remove(ruta)
    os.symlink(destino, ruta)


def main() -> int:
    if not hasattr(os, "openpty"):
        print("Este script solo funciona en sistemas tipo POSIX (Linux/macOS).",
              file=sys.stderr)
        return 1

    master_a, slave_a = os.openpty()
    master_b, slave_b = os.openpty()
    nombre_a = os.ttyname(slave_a)
    nombre_b = os.ttyname(slave_b)

    crear_enlace(ENLACE_BASCULA, nombre_a)
    crear_enlace(ENLACE_POS, nombre_b)

    print(f"Báscula -> {nombre_a}  ({ENLACE_BASCULA})")
    print(f"POS     -> {nombre_b}  ({ENLACE_POS})")
    print("Puenteando datos entre ambos extremos. Ctrl+C para salir.")

    try:
        while True:
            listos, _, _ = select.select([master_a, master_b], [], [], 1.0)
            for fd in listos:
                try:
                    datos = os.read(fd, 1024)
                except OSError:
                    continue
                if not datos:
                    continue
                destino = master_b if fd == master_a else master_a
                os.write(destino, datos)
    except KeyboardInterrupt:
        print("\nCerrando puente de pseudo-terminales.")
    finally:
        for enlace in (ENLACE_BASCULA, ENLACE_POS):
            try:
                if os.path.islink(enlace):
                    os.remove(enlace)
            except OSError:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

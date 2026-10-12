#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [CONSERVAR] Logging global con rotación. NO es demo: se reutiliza tal cual en
#             producción (servicio de báscula y POS). Ruta/nivel configurables
#             por $BASCULA_LOG / $BASCULA_LOG_NIVEL.
# =============================================================================
"""
registro_bascula.py
====================

Configuración centralizada de **registro (logging)** para el proyecto de
simulación de báscula.

Características
---------------
- **Un único archivo global con rotación**: ``logs/bascula.log``
  (tamaño máximo 1 MiB, 3 copias de respaldo). Evita que el archivo crezca
  sin límite en ejecuciones largas (días / semanas).
- Salida a consola **opcional** y tolerante a ``sys.stdout = None`` (propio de
  los ejecutables empaquetados con PyInstaller ``--windowed``).
- Configuración idempotente: se puede invocar desde cualquier módulo sin
  duplicar manejadores.

Uso
---
    from registro_bascula import obtener_logger
    log = obtener_logger("simulador_gui")
    log.info("Conectado a COM4 @ 9600 bps")

La ruta del archivo puede sobreescribirse con la variable de entorno
``BASCULA_LOG``.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

NOMBRE_LOGGER = "bascula"
RUTA_LOG_POR_DEFECTO = os.path.join("logs", "bascula.log")
MAX_BYTES = 1 * 1024 * 1024
COPIAS_RESPALDO = 3
NIVEL_POR_DEFECTO = "INFO"
FORMATO = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

_configurado = False
_ruta_actual: Optional[str] = None


def _terminal_es_tty() -> bool:
    flujo = sys.stdout
    try:
        return flujo is not None and flujo.isatty()
    except (AttributeError, ValueError):
        return False


class _HandlerConsola(logging.StreamHandler):
    """Handler de consola que no falla si ``sys.stdout`` es ``None``."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        if self.stream is None:
            return
        try:
            super().emit(record)
        except (ValueError, OSError):
            # Consola cerrada o no disponible: no interrumpimos la app.
            pass


def _resolver_nivel(nivel: Optional[str]) -> int:
    if isinstance(nivel, int):
        return nivel
    nombre = (nivel or os.environ.get("BASCULA_LOG_NIVEL") or NIVEL_POR_DEFECTO)
    return getattr(logging, str(nombre).upper(), logging.INFO)


def _resolver_ruta(ruta: Optional[str]) -> str:
    return ruta or os.environ.get("BASCULA_LOG") or RUTA_LOG_POR_DEFECTO


def configurar(
    ruta_archivo: Optional[str] = None,
    nivel: Optional[str] = None,
    consola: bool = False,
    forzar: bool = False,
) -> logging.Logger:
    """Configura (una sola vez) el logger raíz del proyecto.

    Parámetros
    ----------
    ruta_archivo : ruta del log con rotación (por defecto ``logs/bascula.log``).
    nivel        : nivel de logging (por defecto ``INFO`` o ``$BASCULA_LOG_NIVEL``).
    consola      : añade un handler de consola (desactivado por defecto para no
                   duplicar la salida de los scripts, que ya imprimen con color).
    forzar       : reconfigura aunque ya estuviera configurado.
    """
    global _configurado, _ruta_actual

    logger = logging.getLogger(NOMBRE_LOGGER)
    ruta = _resolver_ruta(ruta_archivo)

    if _configurado and not forzar and ruta == _ruta_actual:
        return logger

    logger.setLevel(_resolver_nivel(nivel))
    logger.propagate = False

    for manejador in list(logger.handlers):
        logger.removeHandler(manejador)
        try:
            manejador.close()
        except Exception:  # noqa: BLE001 - cierre best-effort
            pass

    formato = logging.Formatter(FORMATO, datefmt=FORMATO_FECHA)

    try:
        directorio = os.path.dirname(ruta)
        if directorio:
            os.makedirs(directorio, exist_ok=True)
        manejador_archivo = RotatingFileHandler(
            ruta,
            maxBytes=MAX_BYTES,
            backupCount=COPIAS_RESPALDO,
            encoding="utf-8",
        )
        manejador_archivo.setFormatter(formato)
        logger.addHandler(manejador_archivo)
        _ruta_actual = ruta
    except OSError:
        # Sin permisos para escribir el log: continuamos solo con consola.
        _ruta_actual = None

    if consola or _ruta_actual is None:
        manejador_consola = _HandlerConsola(sys.stdout)
        manejador_consola.setFormatter(formato)
        logger.addHandler(manejador_consola)

    _configurado = True
    return logger


def obtener_logger(nombre: str = "app", **kwargs) -> logging.Logger:
    """Devuelve un logger hijo ya configurado (archivo global + rotación)."""
    configurar(**kwargs)
    return logging.getLogger(NOMBRE_LOGGER).getChild(nombre)


if __name__ == "__main__":  # pragma: no cover - utilidad de diagnóstico
    log = obtener_logger("prueba", consola=True, forzar=True)
    log.info("Registro de prueba. Archivo: %s", _ruta_actual or "(sin archivo)")
    print(f"Log configurado en: {_ruta_actual or '(sin archivo)'}")

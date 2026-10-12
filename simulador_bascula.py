#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [DEMO] Simulador de báscula VIRTUAL (emula una báscula por software).
#        SOLO para demostraciones a clientes. NO genera peso legal.
#        DESCARTAR en producción: sustituir por báscula física + servicio de
#        báscula (ver guia_integracion_bascula_produccion.txt). Lo marcado
#        [CONSERVAR] (trama, apertura de puerto) se reutiliza en producción.
# =============================================================================
"""
simulador_bascula.py
=====================

Simulador de una báscula comercial conectada por puerto serial (RS232).

Emula el protocolo típico de una báscula de mostrador / balanza industrial,
enviando tramas de peso con el formato:

    015.45 KG\\r\\n

donde los 6 primeros caracteres son el peso con 2 decimales y relleno de
ceros a la izquierda, seguidos de la unidad y el terminador CRLF.

Modos de operación
------------------
1. Automático  (--modo auto):
   Genera fluctuaciones de peso realistas que suben y bajan gradualmente,
   simulando el comportamiento de un artículo colocado sobre la plataforma
   (ruido de medición, estabilización, etc.).

2. Interactivo (--modo manual):
   Permite al usuario escribir un peso por teclado. La báscula virtual
   enviará esa lectura de forma constante hasta que se introduzca un nuevo
   valor.

Uso
---
    python simulador_bascula.py --puerto COM10 --baudrate 9600 --modo auto
    python simulador_bascula.py --puerto /dev/pts/5 --modo manual

El script admite (y documenta) los siguientes parámetros de configuración
mediante variables de entorno si no se pasan argumentos de línea de comandos:

    BASCULA_PUERTO      -> puerto serial a abrir
    BASCULA_BAUDRATE    -> velocidad en baudios (por defecto 9600)
    BASCULA_MODO        -> 'auto' o 'manual'

Autor: (proyecto de simulación POS)
Licencia: MIT
"""

from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import threading
import time
from typing import Optional

import serial
from serial import SerialException
from serial.tools import list_ports

from registro_bascula import obtener_logger

_log = obtener_logger("simulador_bascula")


# ---------------------------------------------------------------------------
# Constantes de configuración
# [CONSERVAR] BAUDRATE/byte size/paridad/timeout y formato de trama se
# reutilizan tal cual para leer (o emitir a) una báscula real con el mismo
# protocolo "PPP.PP KG\r\n".
# ---------------------------------------------------------------------------

BAUDRATE_POR_DEFECTO = 9600
BYTESIZE_POR_DEFECTO = serial.EIGHTBITS
PARITY_POR_DEFECTO = serial.PARITY_NONE
STOPBITS_POR_DEFECTO = serial.STOPBITS_ONE
TIMEOUT_ESCRITURA = 1  # segundos

# Formato de la trama: 6 caracteres (000.00 a 999.99) + espacio + unidad + CRLF
FORMATO_PESO = "{:06.2f}"
UNIDAD_POR_DEFECTO = "KG"
TERMINADOR = "\r\n"

# Parámetros del generador automático
PESO_MAXIMO_KG = 60.0
PASO_MAXIMO_KG = 1.2       # variación máxima por tick
INTERVALO_TICK_S = 0.25    # cada cuánto se recalcula el peso
INTERVALO_ENVIO_S = 0.5    # cada cuánto se envía una trama por el puerto

# Colores ANSI para la consola (se desactivan si no hay terminal interactiva,
# p. ej. en un .exe empaquetado con --windowed, donde sys.stdout es None).
def _terminal_es_tty() -> bool:
    flujo = sys.stdout
    try:
        return flujo is not None and flujo.isatty()
    except (AttributeError, ValueError):
        return False


_USAR_COLOR = _terminal_es_tty()


def _c(texto: str, codigo: str) -> str:
    """Envuelve *texto* con un código de color ANSI si la terminal lo soporta."""
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


# ---------------------------------------------------------------------------
# Generador de peso
# ---------------------------------------------------------------------------

class GeneradorPeso:
    """Encapsula la lógica de generación de lecturas de peso.

    [DEMO] Genera pesos ALEATORIOS/simulados. En producción lo sustituye la
    lectura real de la báscula (streaming o bajo demanda).
    """

    def __init__(self, maximo: float = PESO_MAXIMO_KG) -> None:
        self.maximo = maximo
        self.peso_actual = 0.0
        self.objetivo = 0.0
        self.estable = False
        self._decidir_nuevo_objetivo()

    def _decidir_nuevo_objetivo(self) -> None:
        """Elige un nuevo peso objetivo al que tender gradualmente."""
        # Con cierta probabilidad la báscula queda vacía (peso ~ 0).
        if random.random() < 0.25:
            self.objetivo = 0.0
        else:
            self.objetivo = round(random.uniform(0.2, self.maximo), 2)
        self.estable = False

    def siguiente(self) -> float:
        """Avanza un tick y devuelve el peso simulado actual."""
        diferencia = self.objetivo - self.peso_actual

        if abs(diferencia) <= 0.05:
            # Ya estamos en el objetivo: añadimos ruido de medición.
            self.peso_actual = self.objetivo + random.uniform(-0.02, 0.02)
            self.estable = True
            # Ocasionalmente cambiamos de objetivo para simular retirada/colocación.
            if random.random() < 0.05:
                self._decidir_nuevo_objetivo()
        else:
            # Aproximación gradual hacia el objetivo.
            paso = max(-PASO_MAXIMO_KG, min(PASO_MAXIMO_KG, diferencia * 0.3))
            self.peso_actual += paso

        self.peso_actual = max(0.0, round(self.peso_actual, 2))
        return self.peso_actual


def construir_trama(peso: float, unidad: str = UNIDAD_POR_DEFECTO) -> bytes:
    """Construye la trama binaria que se envía por el puerto serial.

    Formato: ``PPP.PP KG\\r\\n`` (peso con 2 decimales, relleno a ceros).

    [CONSERVAR] Si la báscula real usa este mismo protocolo, esta función
    se reutiliza sin cambios para interpretar/generar tramas en producción.
    """
    texto = f"{FORMATO_PESO.format(peso)} {unidad}{TERMINADOR}"
    return texto.encode("ascii", errors="strict")


# ---------------------------------------------------------------------------
# Entrada interactiva (modo manual)
# ---------------------------------------------------------------------------

class PesoManual:
    """Hilo que lee pesos introducidos por el usuario por stdin.

    El valor leído se guarda de forma thread-safe en ``self.peso`` y se
    consume desde el bucle de envío principal.
    """

    def __init__(self) -> None:
        self.peso: float = 0.0
        self._lock = threading.Lock()
        self._detener = threading.Event()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)

    def iniciar(self) -> None:
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()
        if self._hilo.is_alive():
            self._hilo.join(timeout=1.0)

    def obtener(self) -> float:
        with self._lock:
            return self.peso

    def _bucle(self) -> None:
        while not self._detener.is_set():
            try:
                entrada = input()
            except (EOFError, KeyboardInterrupt):
                # stdin cerrado o Ctrl+C mientras se esperaba entrada.
                self._detener.set()
                break

            entrada = entrada.strip().replace(",", ".")
            if not entrada:
                continue

            try:
                valor = float(entrada)
            except ValueError:
                advertir(f"Valor no válido: '{entrada}'. Introduce un número (ej. 15.45).")
                continue

            if valor < 0:
                advertir("El peso no puede ser negativo. Se ignora.")
                continue

            with self._lock:
                self.peso = round(valor, 2)
            ok(f"Peso manual fijado en {self.peso:.2f} {UNIDAD_POR_DEFECTO}")


# ---------------------------------------------------------------------------
# Apertura y validación del puerto
# ---------------------------------------------------------------------------

def puerto_disponible(nombre: str) -> bool:
    """Comprueba si *nombre* aparece entre los puertos serie detectados."""
    return any(p.device == nombre for p in list_ports.comports())


def abrir_puerto(puerto: str, baudrate: int) -> serial.Serial:
    """Abre el puerto serial con manejo explícito de errores.

    Lanza ``SerialException`` con un mensaje claro si el puerto está ocupado,
    no existe o no se puede configurar.
    """
    if not puerto_disponible(puerto):
        advertir(
            f"El puerto '{puerto}' no aparece en la lista del sistema. "
            "Se intentará abrir de todos modos."
        )

    try:
        conexion = serial.Serial(
            port=puerto,
            baudrate=baudrate,
            bytesize=BYTESIZE_POR_DEFECTO,
            parity=PARITY_POR_DEFECTO,
            stopbits=STOPBITS_POR_DEFECTO,
            timeout=0.1,
            write_timeout=TIMEOUT_ESCRITURA,
        )
    except SerialException as exc:
        mensaje = str(exc).lower()
        if "access is denied" in mensaje or "permission" in mensaje:
            raise SerialException(
                f"No se pudo abrir '{puerto}': el puerto está ocupado por otro "
                "proceso. Cierra el programa que lo esté usando e inténtalo de nuevo."
            ) from exc
        if "could not open" in mensaje or "no such file" in mensaje:
            raise SerialException(
                f"No se pudo abrir '{puerto}': el puerto no existe. Verifica el "
                "nombre (COM10 en Windows, /dev/pts/N en Linux)."
            ) from exc
        raise

    return conexion


# ---------------------------------------------------------------------------
# Bucle principal del simulador
# ---------------------------------------------------------------------------

def ejecutar_simulador(conexion: serial.Serial, modo: str) -> None:
    """Envía tramas de peso por *conexion* hasta recibir una señal de parada.

    [DEMO] Bucle de emisión simulada (auto/manual). En producción se reemplaza
    por el bucle de LECTURA de la báscula real dentro del servicio de báscula.
    """
    detener = threading.Event()

    def _manejador_senal(signum, frame):  # noqa: ANN001 - firma exigida por signal
        advertir("Señal de interrupción recibida. Cerrando de forma ordenada...")
        detener.set()

    for senal in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(senal, _manejador_senal)
        except (ValueError, AttributeError, OSError):
            # En algunos entornos (p.ej. hilos, Windows sin consola) no aplica.
            pass

    generador = GeneradorPeso()
    manual: Optional[PesoManual] = None

    if modo == "manual":
        manual = PesoManual()
        manual.iniciar()

    info(
        f"Simulador iniciado en modo '{modo}'. "
        f"Puerto={conexion.port} @ {conexion.baudrate} bps."
    )
    if modo == "manual":
        info("Escribe un peso y pulsa ENTER (ej. 15.45). Ctrl+C para salir.")
    else:
        info("Generando fluctuaciones automáticas. Ctrl+C para salir.")

    ultimo_error: Optional[Exception] = None
    try:
        while not detener.is_set():
            try:
                if modo == "manual":
                    peso = manual.obtener() if manual else 0.0
                else:
                    peso = generador.siguiente()

                trama = construir_trama(peso)
                conexion.write(trama)
                conexion.flush()

                print(
                    _c(f"  -> TX  {peso:06.2f} {UNIDAD_POR_DEFECTO}", "90"),
                    end="\r",
                )

                # En modo automático esperamos un poco entre tramas; en modo
                # manual enviamos la lectura de forma continua.
                espera = INTERVALO_ENVIO_S if modo == "auto" else 0.2
                if detener.wait(espera):
                    break

            except serial.SerialTimeoutException:
                advertir("Tiempo de escritura agotado (el receptor no lee). Reintentando...")
                time.sleep(0.5)
            except SerialException as exc:
                error(f"Error de comunicación serial: {exc}")
                ultimo_error = exc
                break

    finally:
        print()  # Salta la línea del indicador \r
        if manual is not None:
            manual.detener()
        ok("Simulador detenido correctamente.")

    if ultimo_error is not None:
        error("El simulador termino debido a un error de comunicación.")
        sys.exit(2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parsear_argumentos(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulador de báscula comercial por puerto serial (RS232).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--puerto",
        default=os.environ.get("BASCULA_PUERTO", "COM1"),
        help="Puerto serial a usar (ej. COM10 o /dev/pts/5).",
    )
    parser.add_argument(
        "-b", "--baudrate",
        type=int,
        default=int(os.environ.get("BASCULA_BAUDRATE", BAUDRATE_POR_DEFECTO)),
        help="Velocidad en baudios.",
    )
    parser.add_argument(
        "-m", "--modo",
        choices=("auto", "manual"),
        default=os.environ.get("BASCULA_MODO", "auto"),
        help="Modo de simulación: 'auto' (fluctuaciones) o 'manual' (peso fijo).",
    )
    parser.add_argument(
        "-l", "--listar",
        action="store_true",
        help="Lista los puertos seriales disponibles y sale.",
    )
    return parser.parse_args(argv)


def listar_puertos() -> None:
    puertos = list(list_ports.comports())
    if not puertos:
        advertir("No se han detectado puertos seriales en el sistema.")
        return
    print("Puertos seriales detectados:")
    for p in puertos:
        print(f"  - {p.device:<12} {p.description}")


def main(argv: Optional[list[str]] = None) -> int:
    args = parsear_argumentos(argv)

    if args.listar:
        listar_puertos()
        return 0

    try:
        conexion = abrir_puerto(args.puerto, args.baudrate)
    except SerialException as exc:
        error(str(exc))
        return 1

    try:
        ejecutar_simulador(conexion, args.modo)
    finally:
        try:
            if conexion.is_open:
                conexion.close()
        except SerialException:
            pass

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Cierre limpio si el Ctrl+C llega antes de instalar el manejador.
        print()
        advertir("Interrupción por teclado. Saliendo.")
        sys.exit(130)

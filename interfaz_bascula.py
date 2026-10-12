#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [DEMO] Interfaz de pesajes rápidos para SIMULAR una báscula comercial.
#        SOLO para demostraciones a clientes. NO genera peso legal.
#        DESCARTAR en producción: sustituir por la báscula física + servicio.
#        Ver guia_integracion_bascula_produccion.txt.
# =============================================================================
"""
interfaz_bascula.py
====================

Interfaz gráfica ligera (Tkinter, incluida en Python) para el simulador de
báscula comercial. Permite al operador enviar **pesajes predefinidos** de
1 kg, 500 g y 200 g (y vaciar la báscula) por el mismo puerto serie y con el
mismo protocolo que usa ``simulador_bascula.py``.

Reutiliza las utilidades del código generado anteriormente:

    - ``abrir_puerto``     -> apertura del puerto con manejo de errores.
    - ``construir_trama``  -> formato de trama ``PPP.PP KG\\r\\n``.
    - ``listar_puertos``   -> detección de puertos disponibles.

Características
---------------
- Conexión/desconexión del puerto desde la propia ventana.
- Botones grandes para 1 kg, 500 g y 200 g (más "Vaciar" = 0 g).
- Posibilidad de reenviar la lectura de forma continua (modo báscula real).
- Pantalla grande con el peso que se está transmitiendo.
- Registro (log) de actividad y mensajes de estado.
- Cierre limpio al pulsar la X de la ventana.

Uso
---
    python interfaz_bascula.py --puerto COM10 --baudrate 9600
    python interfaz_bascula.py --puerto /tmp/ttyBASCULA
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Optional

from serial import SerialException

from simulador_bascula import (
    BAUDRATE_POR_DEFECTO,
    UNIDAD_POR_DEFECTO,
    abrir_puerto,
    construir_trama,
    listar_puertos,
)

# Pesajes predefinidos: (etiqueta, peso en kg)
PESAJES_PREDEFINIDOS = (
    ("1 kg", 1.00),
    ("500 g", 0.50),
    ("200 g", 0.20),
)

INTERVALO_REENVIO_MS = 300  # cada cuánto se reenvía la lectura seleccionada


class InterfazBascula(tk.Tk):
    """Ventana principal de la interfaz de pesajes rápidos."""

    def __init__(self, puerto: str, baudrate: int) -> None:
        super().__init__()
        self.title("Simulador de Báscula - Pesajes Rápidos")
        self.minsize(420, 520)
        self.resizable(False, False)

        # --- Estado interno -------------------------------------------------
        self.conexion = None                 # serial.Serial | None
        self.peso_actual: float = 0.0
        self._lock = threading.Lock()        # protege la escritura al puerto
        self._reenviando = tk.BooleanVar(value=True)
        self._job_reenvio: Optional[str] = None

        # --- Variables de interfaz -----------------------------------------
        self.var_puerto = tk.StringVar(value=puerto)
        self.var_baudrate = tk.StringVar(value=str(baudrate))
        self.var_pantalla = tk.StringVar(value=self._texto_peso(0.0))
        self.var_estado = tk.StringVar(value="Desconectado")

        self._construir_widgets()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _construir_widgets(self) -> None:
        estilo = ttk.Style(self)
        try:
            estilo.theme_use("clam")
        except tk.TclError:
            pass

        contenedor = ttk.Frame(self, padding=16)
        contenedor.pack(fill="both", expand=True)

        # --- Conexión ------------------------------------------------------
        marco_conexion = ttk.LabelFrame(contenedor, text="Conexión", padding=10)
        marco_conexion.pack(fill="x")

        ttk.Label(marco_conexion, text="Puerto:").grid(row=0, column=0, sticky="w")
        entrada_puerto = ttk.Entry(marco_conexion, textvariable=self.var_puerto, width=16)
        entrada_puerto.grid(row=0, column=1, padx=(4, 12))

        ttk.Label(marco_conexion, text="Baudios:").grid(row=0, column=2, sticky="w")
        ttk.Entry(marco_conexion, textvariable=self.var_baudrate, width=8).grid(
            row=0, column=3, padx=(4, 12)
        )

        self.boton_conectar = ttk.Button(
            marco_conexion, text="Conectar", command=self._alternar_conexion
        )
        self.boton_conectar.grid(row=0, column=4)

        ttk.Button(
            marco_conexion, text="Listar", command=self._listar_puertos
        ).grid(row=0, column=5, padx=(6, 0))

        # --- Pantalla del peso --------------------------------------------
        marco_pantalla = ttk.LabelFrame(contenedor, text="Peso transmitido", padding=10)
        marco_pantalla.pack(fill="x", pady=(12, 0))

        self.etiqueta_peso = tk.Label(
            marco_pantalla,
            textvariable=self.var_pantalla,
            font=("Consolas", 40, "bold"),
            fg="#0b6623",
            bg="#0a0a0a",
            width=12,
            pady=8,
        )
        self.etiqueta_peso.pack()

        # --- Botones de pesajes predefinidos ------------------------------
        marco_botones = ttk.LabelFrame(
            contenedor, text="Pesajes predefinidos", padding=10
        )
        marco_botones.pack(fill="x", pady=(12, 0))

        for columna, (etiqueta, peso) in enumerate(PESAJES_PREDEFINIDOS):
            ttk.Button(
                marco_botones,
                text=etiqueta,
                width=10,
                command=lambda p=peso: self._enviar_peso(p),
            ).grid(row=0, column=columna, padx=4, pady=4)

        ttk.Button(
            marco_botones,
            text="Vaciar (0)",
            width=10,
            command=lambda: self._enviar_peso(0.0),
        ).grid(row=0, column=len(PESAJES_PREDEFINIDOS), padx=4, pady=4)

        ttk.Checkbutton(
            marco_botones,
            text=f"Reenviar cada {INTERVALO_REENVIO_MS} ms (lectura continua)",
            variable=self._reenviando,
            command=self._actualizar_reenvio,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        # --- Registro de actividad ----------------------------------------
        marco_log = ttk.LabelFrame(contenedor, text="Actividad", padding=6)
        marco_log.pack(fill="both", expand=True, pady=(12, 0))

        self.texto_log = tk.Text(marco_log, height=6, width=52, state="disabled")
        self.texto_log.pack(side="left", fill="both", expand=True)
        barra = ttk.Scrollbar(marco_log, command=self.texto_log.yview)
        barra.pack(side="right", fill="y")
        self.texto_log.configure(yscrollcommand=barra.set)

        # --- Barra de estado ----------------------------------------------
        ttk.Label(
            contenedor, textvariable=self.var_estado, relief="sunken", anchor="w"
        ).pack(fill="x", pady=(10, 0))

    # ------------------------------------------------------------------
    # Utilidades internas
    # ------------------------------------------------------------------
    @staticmethod
    def _texto_peso(peso: float) -> str:
        return f"{peso:06.2f} {UNIDAD_POR_DEFECTO}"

    def _registrar(self, mensaje: str) -> None:
        marca = datetime.now().strftime("%H:%M:%S")
        self.texto_log.configure(state="normal")
        self.texto_log.insert("end", f"[{marca}] {mensaje}\n")
        self.texto_log.see("end")
        self.texto_log.configure(state="disabled")

    def _listar_puertos(self) -> None:
        puertos = list(list_ports.comports())
        if not puertos:
            self._registrar("No se detectaron puertos seriales.")
            return
        for p in puertos:
            self._registrar(f"Puerto disponible: {p.device} - {p.description}")

    # ------------------------------------------------------------------
    # Gestión de la conexión
    # ------------------------------------------------------------------
    def _alternar_conexion(self) -> None:
        if self.conexion is None:
            self._conectar()
        else:
            self._desconectar()

    def _conectar(self) -> None:
        puerto = self.var_puerto.get().strip()
        try:
            baudrate = int(self.var_baudrate.get().strip())
        except ValueError:
            messagebox.showerror("Baudios inválidos", "El baud rate debe ser un número entero.")
            return

        try:
            self.conexion = abrir_puerto(puerto, baudrate)
        except SerialException as exc:
            messagebox.showerror("Error al conectar", str(exc))
            self._registrar(f"ERROR: {exc}")
            self.conexion = None
            return

        self.boton_conectar.configure(text="Desconectar")
        self.var_estado.set(f"Conectado a {puerto} @ {baudrate} bps")
        self._registrar(f"Conectado a {puerto} @ {baudrate} bps")
        if self._reenviando.get():
            self._programar_reenvio()

    def _desconectar(self) -> None:
        self._cancelar_reenvio()
        with self._lock:
            if self.conexion is not None:
                try:
                    if self.conexion.is_open:
                        self.conexion.close()
                except SerialException:
                    pass
                self.conexion = None
        self.boton_conectar.configure(text="Conectar")
        self.var_estado.set("Desconectado")
        self._registrar("Puerto cerrado.")

    # ------------------------------------------------------------------
    # Envío de pesos
    # ------------------------------------------------------------------
    def _enviar_peso(self, peso: float) -> None:
        """Selecciona *peso* y lo transmite (una vez o de forma continua)."""
        with self._lock:
            self.peso_actual = round(peso, 2)
        self.var_pantalla.set(self._texto_peso(peso))
        self._registrar(f"Pesaje seleccionado: {self._texto_peso(peso)}")
        self._transmitir()

    def _transmitir(self) -> None:
        """Escribe la trama del peso actual en el puerto, si está conectado."""
        if self.conexion is None or not self.conexion.is_open:
            self.var_estado.set("Selecciona un puerto y pulsa Conectar.")
            return
        try:
            with self._lock:
                trama = construir_trama(self.peso_actual)
                self.conexion.write(trama)
                self.conexion.flush()
            self.var_estado.set(f"TX {self._texto_peso(self.peso_actual)}")
        except SerialException as exc:
            self._registrar(f"ERROR de escritura: {exc}")
            self.var_estado.set("Error de comunicación. Reconecta el puerto.")
            self._desconectar()

    def _programar_reenvio(self) -> None:
        self._cancelar_reenvio()
        if self.conexion is not None:
            self._transmitir()
            self._job_reenvio = self.after(INTERVALO_REENVIO_MS, self._programar_reenvio)

    def _cancelar_reenvio(self) -> None:
        if self._job_reenvio is not None:
            try:
                self.after_cancel(self._job_reenvio)
            except tk.TclError:
                pass
            self._job_reenvio = None

    def _actualizar_reenvio(self) -> None:
        if self._reenviando.get():
            self._registrar("Reenvío continuo ACTIVADO.")
            self._programar_reenvio()
        else:
            self._registrar("Reenvío continuo DESACTIVADO.")
            self._cancelar_reenvio()

    # ------------------------------------------------------------------
    # Cierre
    # ------------------------------------------------------------------
    def _al_cerrar(self) -> None:
        self._cancelar_reenvio()
        self._desconectar()
        self.destroy()


def parsear_argumentos(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interfaz gráfica de pesajes rápidos para el simulador de báscula.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--puerto",
        default=os.environ.get("BASCULA_PUERTO", "COM1"),
        help="Puerto serial a usar (ej. COM10 o /tmp/ttyBASCULA).",
    )
    parser.add_argument(
        "-b", "--baudrate",
        type=int,
        default=int(os.environ.get("BASCULA_BAUDRATE", BAUDRATE_POR_DEFECTO)),
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
        listar_puertos()
        return 0

    try:
        app = InterfazBascula(args.puerto, args.baudrate)
    except tk.TclError as exc:
        print(
            "No se pudo iniciar la interfaz gráfica (¿hay entorno gráfico?). "
            f"Detalle: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        app.mainloop()
    except KeyboardInterrupt:
        app._al_cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())

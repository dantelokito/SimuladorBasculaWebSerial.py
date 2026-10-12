#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [DEMO] Simulador de báscula con GUI (Tkinter) + envío serial simulado.
#        SOLO para demostraciones a clientes. NO genera peso legal.
#        DESCARTAR en producción: sustituir por la báscula física + servicio
#        de báscula (ver guia_integracion_bascula_produccion.txt). El modo de
#        registro [CONSERVAR] (ring buffer + log rotativo) se reutiliza.
# =============================================================================
"""
simulador_gui.py
=================

Simulador de báscula comercial con **interfaz gráfica** (Tkinter) y envío
continuo por **puerto serial** (pyserial).

Pensado para probar software de Punto de Venta (POS) sin el hardware físico.
Mientras la báscula está conectada, un **hilo en segundo plano** transmite cada
500 ms la trama del peso actual con el formato típico:

    001.00 KG\\r\\n       (1 kg)
    000.50 KG\\r\\n       (500 g)
    000.20 KG\\r\\n       (200 g)
    000.00 KG\\r\\n       (tara / vacío)

Características de la interfaz
------------------------------
- Configuración de **Puerto Serial** y **Baud Rate** con botón Conectar/Desconectar.
- Indicador **grande** del "Peso Actual" que se está simulando.
- Botones de acceso rápido: **1 kg**, **500 g**, **200 g** y **Tara**.
- Campo de texto + botón para fijar un **peso personalizado**.
- Registro de actividad y barra de estado.
- Cierre seguro de la ventana: detiene el hilo y libera el puerto.

Uso
---
    python simulador_gui.py --puerto COM3 --baudrate 9600
    python simulador_gui.py --puerto /dev/pts/1

Reutiliza utilidades del proyecto:

    from simulador_bascula import abrir_puerto, construir_trama, listar_puertos
"""

from __future__ import annotations

import argparse
import os
import queue
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
from registro_bascula import obtener_logger

PERIODO_ENVIO_S = 0.5  # cada cuánto se reenvía la trama en segundo plano
MAX_LINEAS_LOG = 500   # límite del registro en memoria (ring buffer) de la GUI


class SimuladorBasculaGUI(tk.Tk):
    """Ventana principal del simulador de báscula."""

    def __init__(self, puerto: str, baudrate: int) -> None:
        super().__init__()
        self.title("Simulador de Báscula POS")
        self.minsize(460, 560)
        self.resizable(False, False)

        # --- Estado interno -------------------------------------------------
        self.conexion = None                 # serial.Serial | None
        self._lock = threading.Lock()        # protege peso + puerto
        self._peso_actual: float = 0.0
        self._detener = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self._cola_ui: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._logger = obtener_logger("simulador_gui")

        # --- Variables de UI ------------------------------------------------
        self.var_puerto = tk.StringVar(value=puerto)
        self.var_baudrate = tk.StringVar(value=str(baudrate))
        self.var_pantalla = tk.StringVar(value=self._texto_peso(0.0))
        self.var_estado = tk.StringVar(value="Desconectado")
        self.var_custom = tk.StringVar(value="")

        self._construir_widgets()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        self.after(100, self._procesar_cola_ui)

    # ==================================================================
    # Construcción de la interfaz
    # ==================================================================
    def _construir_widgets(self) -> None:
        try:
            ttk.Style(self).theme_use("clam")
        except tk.TclError:
            pass

        raiz = ttk.Frame(self, padding=16)
        raiz.pack(fill="both", expand=True)

        # -- Conexión ------------------------------------------------------
        marco = ttk.LabelFrame(raiz, text="Configuración del puerto", padding=10)
        marco.pack(fill="x")

        ttk.Label(marco, text="Puerto:").grid(row=0, column=0, sticky="w")
        ttk.Entry(marco, textvariable=self.var_puerto, width=16).grid(
            row=0, column=1, padx=(4, 12)
        )
        ttk.Label(marco, text="Baud Rate:").grid(row=0, column=2, sticky="w")
        ttk.Entry(marco, textvariable=self.var_baudrate, width=8).grid(
            row=0, column=3, padx=(4, 12)
        )
        self.boton_conectar = ttk.Button(
            marco, text="Conectar", command=self._alternar_conexion
        )
        self.boton_conectar.grid(row=0, column=4)
        ttk.Button(marco, text="Listar", command=self._listar).grid(
            row=0, column=5, padx=(6, 0)
        )

        # -- Peso actual ---------------------------------------------------
        marco_peso = ttk.LabelFrame(raiz, text="Peso actual", padding=10)
        marco_peso.pack(fill="x", pady=(12, 0))
        tk.Label(
            marco_peso,
            textvariable=self.var_pantalla,
            font=("Consolas", 44, "bold"),
            fg="#0b6623",
            bg="#0a0a0a",
            width=12,
            pady=10,
        ).pack()

        # -- Accesos rápidos ------------------------------------------------
        marco_rapido = ttk.LabelFrame(raiz, text="Acceso rápido", padding=10)
        marco_rapido.pack(fill="x", pady=(12, 0))

        botones = (
            ("1 Kg", 1.00),
            ("500 g", 0.50),
            ("200 g", 0.20),
            ("Tara", 0.00),
        )
        for col, (texto, peso) in enumerate(botones):
            ttk.Button(
                marco_rapido,
                text=texto,
                width=10,
                command=lambda p=peso: self._fijar_peso(p),
            ).grid(row=0, column=col, padx=4, pady=2)

        # -- Peso personalizado --------------------------------------------
        marco_custom = ttk.LabelFrame(raiz, text="Peso personalizado (kg)", padding=10)
        marco_custom.pack(fill="x", pady=(12, 0))
        entrada = ttk.Entry(marco_custom, textvariable=self.var_custom, width=14)
        entrada.grid(row=0, column=0, padx=(0, 8))
        entrada.bind("<Return>", lambda _e: self._aplicar_custom())
        ttk.Button(
            marco_custom, text="Fijar peso", command=self._aplicar_custom
        ).grid(row=0, column=1)

        # -- Registro -------------------------------------------------------
        marco_log = ttk.LabelFrame(raiz, text="Actividad", padding=6)
        marco_log.pack(fill="both", expand=True, pady=(12, 0))
        self.texto_log = tk.Text(marco_log, height=6, width=56, state="disabled")
        self.texto_log.pack(side="left", fill="both", expand=True)
        barra = ttk.Scrollbar(marco_log, command=self.texto_log.yview)
        barra.pack(side="right", fill="y")
        self.texto_log.configure(yscrollcommand=barra.set)

        # -- Estado ---------------------------------------------------------
        ttk.Label(
            raiz, textvariable=self.var_estado, relief="sunken", anchor="w"
        ).pack(fill="x", pady=(10, 0))

    # ==================================================================
    # Utilidades
    # ==================================================================
    @staticmethod
    def _texto_peso(peso: float) -> str:
        return f"{peso:06.2f} {UNIDAD_POR_DEFECTO}"

    def _log(self, mensaje: str) -> None:
        """Encola un mensaje para mostrarlo en el registro (seguro entre hilos)."""
        self._cola_ui.put(("log", mensaje))
        self._logger.info(mensaje)

    def _set_estado(self, mensaje: str) -> None:
        self._cola_ui.put(("estado", mensaje))

    def _procesar_cola_ui(self) -> None:
        """Vuelca la cola en los widgets desde el hilo principal de Tk."""
        try:
            while True:
                tipo, valor = self._cola_ui.get_nowait()
                if tipo == "log":
                    marca = datetime.now().strftime("%H:%M:%S")
                    self.texto_log.configure(state="normal")
                    self.texto_log.insert("end", f"[{marca}] {valor}\n")
                    # Ring buffer: descarta las líneas más antiguas.
                    total = int(self.texto_log.index("end-1c").split(".")[0])
                    if total > MAX_LINEAS_LOG:
                        self.texto_log.delete("1.0", f"{total - MAX_LINEAS_LOG}.0")
                    self.texto_log.see("end")
                    self.texto_log.configure(state="disabled")
                elif tipo == "estado":
                    self.var_estado.set(valor)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._procesar_cola_ui)

    def _listar(self) -> None:
        puertos = list(list_ports.comports())
        if not puertos:
            self._log("No se detectaron puertos seriales.")
            return
        for p in puertos:
            self._log(f"Disponible: {p.device} - {p.description}")

    # ==================================================================
    # Fijar peso
    # ==================================================================
    def _fijar_peso(self, peso: float) -> None:
        peso = round(max(0.0, peso), 2)
        with self._lock:
            self._peso_actual = peso
        self.var_pantalla.set(self._texto_peso(peso))
        self._log(f"Peso fijado: {self._texto_peso(peso)}")

    def _aplicar_custom(self) -> None:
        crudo = self.var_custom.get().strip().replace(",", ".")
        if not crudo:
            return
        try:
            peso = float(crudo)
        except ValueError:
            messagebox.showerror("Valor inválido", "Introduce un número, por ej. 1.75")
            return
        if peso < 0:
            messagebox.showerror("Valor inválido", "El peso no puede ser negativo.")
            return
        self._fijar_peso(peso)
        self.var_custom.set("")

    # ==================================================================
    # Conexión / hilo de envío
    # ==================================================================
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
            messagebox.showerror("Baud Rate inválido", "Debe ser un número entero (ej. 9600).")
            return

        try:
            self.conexion = abrir_puerto(puerto, baudrate)
        except SerialException as exc:
            messagebox.showerror("Error al conectar", str(exc))
            self._log(f"ERROR: {exc}")
            self.conexion = None
            return

        self.boton_conectar.configure(text="Desconectar")
        self._log(f"Conectado a {puerto} @ {baudrate} bps")
        self._set_estado(f"Transmitiendo en {puerto} @ {baudrate} bps")

        self._detener.clear()
        self._hilo = threading.Thread(
            target=self._bucle_envio, name="envio-serial", daemon=True
        )
        self._hilo.start()

    def _desconectar(self) -> None:
        self._detener.set()
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
            self._hilo = None
        with self._lock:
            if self.conexion is not None:
                try:
                    if self.conexion.is_open:
                        self.conexion.close()
                except SerialException:
                    pass
                self.conexion = None
        self.boton_conectar.configure(text="Conectar")
        self._set_estado("Desconectado")
        self._log("Puerto cerrado.")

    def _bucle_envio(self) -> None:
        """Hilo en segundo plano: envía la trama cada PERIODO_ENVIO_S segundos.

        [DEMO] Emite el peso SIMULADO fijado por el operador. En producción se
        sustituye por la lectura de la báscula real en el servicio de báscula.
        """
        while not self._detener.is_set():
            try:
                with self._lock:
                    conexion = self.conexion
                    peso = self._peso_actual
                if conexion is not None and conexion.is_open:
                    conexion.write(construir_trama(peso))
                    conexion.flush()
            except SerialException as exc:
                self._log(f"ERROR de escritura: {exc}")
                self._set_estado("Error de comunicación. Reconecta el puerto.")
                self._detener.set()
                break
            # Espera interrumpible (responde rápido al cierre).
            if self._detener.wait(PERIODO_ENVIO_S):
                break

    # ==================================================================
    # Cierre seguro
    # ==================================================================
    def _al_cerrar(self) -> None:
        try:
            self._desconectar()
        finally:
            self.destroy()


def parsear_argumentos(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulador de báscula POS con interfaz gráfica.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--puerto",
        default=os.environ.get("BASCULA_PUERTO", "COM1"),
        help="Puerto serial (ej. COM3 o /dev/pts/1).",
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
        app = SimuladorBasculaGUI(args.puerto, args.baudrate)
    except tk.TclError as exc:
        print(f"No se pudo iniciar la GUI (¿entorno gráfico?): {exc}", file=sys.stderr)
        return 1

    try:
        app.mainloop()
    except KeyboardInterrupt:
        app._al_cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())

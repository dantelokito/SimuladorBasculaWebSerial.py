# Simulador de Báscula Comercial (RS232) + POS Web

> ## ⚠️ Demo para clientes
>
> Este repositorio es una **DEMOSTRACIÓN comercial**: todo el "peso" es
> **simulado por software** y **no tiene validez legal** (no sirve para
> facturación ni metrología). Cada pieza descartable está marcada en el código
> con `[DEMO]`, y lo reutilizable en producción con `[CONSERVAR]`.
>
> Documentación de paso a producción:
> - `guia_integracion_bascula_produccion.txt` — módulo báscula (hardware,
>   servicio, despliegue, QA, tabla de descarte).
> - `guia_pos_completo_produccion.txt` — POS completo (catálogo, caja, ticket,
>   metrología legal, criterio de descarte).
>
> **Convención de marcas:** `[DEMO]` = se elimina/sustituye en producción ·
> `[CONSERVAR]` = se reutiliza tal cual.

Entorno de simulación local para probar software de Punto de Venta (POS) sin el
hardware físico. Se crean dos **puertos seriales virtuales interconectados**
(par *null-modem*) y se conecta en un extremo un **simulador de báscula** y en el
otro un **consumidor** (lector de consola o POS Web).

```
 ┌──────────────────────────┐                         ┌──────────────────────────┐
 │  SIMULADOR (báscula)     │      par null-modem      │  CONSUMIDOR (POS)         │
 │  simulador_bascula.py    │                         │  lector_bascula.py        │
 │  simulador_gui.py        │ <== /dev/pts/5 <==> /dev/pts/6 ==> │  pos_web/ (Web Serial)    │
 │  interfaz_bascula.py     │      COM10  <==>  COM11  │                          │
 └──────────────────────────┘                         └──────────────────────────┘
```

Componentes:

| Archivo                     | Rol                                                          |
|-----------------------------|--------------------------------------------------------------|
| `simulador_bascula.py`      | Simulador por **consola** (modo auto/manual).                |
| `simulador_gui.py`          | Simulador con **interfaz gráfica** (Tkinter) + hilo de envío.|
| `interfaz_bascula.py`       | Interfaz gráfica ligera de pesajes rápidos (variante).       |
| `lector_bascula.py`         | Lector de consola que simula el **POS** en Python.           |
| `pos_web/`                  | **POS Web** (HTML/CSS/JS) con **Web Serial API**.            |
| `auditor_serial.py`         | Diagnóstico/auditoría de puertos seriales.                   |
| `par_pty.py`                | (Opcional) par de pty enlazados sin `socat` (Linux).         |

## Protocolo simulado

Cada lectura se envía como texto ASCII de **ancho fijo** terminado en CRLF:

```
PPP.PP KG\r\n
```

| Campo    | Descripción                                   |
|----------|-----------------------------------------------|
| `PPP.PP` | Peso con 2 decimales y ceros a la izquierda.  |
| ` `      | Separador.                                     |
| `KG`     | Unidad.                                        |
| `\r\n`   | Terminador CRLF.                               |

Ejemplos: `001.00 KG\r\n` (1 kg), `000.50 KG\r\n` (500 g), `000.20 KG\r\n` (200 g).

---

## 1. Requisitos

- **Python 3.8+** con `pyserial` (los simuladores GUI usan Tkinter, incluido en Python).
- Navegador **Chrome o Edge** para el POS Web (Web Serial API).

```bash
pip install -r requirements.txt
```

Contenido de `requirements.txt`:

```
pyserial>=3.5
```

Listar puertos disponibles en cualquier momento:

```bash
python simulador_gui.py --listar
python lector_bascula.py --listar
python auditor_serial.py --listar
```

---

## 2. Crear el par de puertos virtuales (null-modem)

### Linux (Ubuntu / Arch) con `socat`

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y socat

# Arch / Manjaro
sudo pacman -S --needed socat
```

Crea el par (déjalo corriendo en su propia terminal):

```bash
socat -d -d pty,raw,echo=0,link=/tmp/ttyBASCULA pty,raw,echo=0,link=/tmp/ttyPOS
```

Crea enlaces estables: `/tmp/ttyBASCULA` (báscula) y `/tmp/ttyPOS` (POS).
Si tu versión de `socat` no soporta `link=`, usará las rutas reales `/dev/pts/N`.

#### Alternativa con Python puro (sin `socat`)

```bash
python par_pty.py
```

Genera `/tmp/ttyBASCULA` y `/tmp/ttyPOS` y puentea los datos entre ambos.

### Windows con `com0com`

1. Descarga e instala **com0com**: <https://sourceforge.net/projects/com0com/>
2. Abre **Setup Command Prompt** (como administrador) y crea un par:

```bat
install PortName=COM10 PortName=COM11
```

3. Verifica / elimina pares:

```bat
list
remove 0
```

> Los números de COM deben estar libres (Administrador de dispositivos → Puertos COM y LPT).

---

## 3. Simulador con interfaz gráfica — `simulador_gui.py`

Ventana Tkinter con configuración de puerto/baudios, indicador **grande** del peso,
botones de **acceso rápido** (1 kg, 500 g, 200 g, Tara) y **peso personalizado**.
Mientras está conectado, un **hilo en segundo plano** envía la trama cada 500 ms.

```
+----------------------------------------------------+
| Configuración  [COM3] [9600] [Conectar] [Listar]   |
| Peso actual                                        |
|           +------------------+                     |
|           |    001.00 KG     |                     |
|           +------------------+                     |
| Acceso rápido  [1 Kg][500 g][200 g][Tara]          |
| Peso personalizado (kg) [ 1.75 ] [Fijar peso]      |
| Actividad ...                                      |
+----------------------------------------------------+
```

```bash
# Windows
python simulador_gui.py --puerto COM10 --baudrate 9600
# Linux
python simulador_gui.py --puerto /tmp/ttyBASCULA --baudrate 9600
```

Al cerrar la ventana se detiene el hilo y se libera el puerto de forma segura.

### 3.1. Empaquetado como ejecutable `.exe` (PyInstaller)

Se incluye `build_exe.bat`, que genera un **único .exe autocontenido** y **sin
ventana de consola** (modo *windowed*):

```bat
build_exe.bat
```

El script instala PyInstaller si falta y ejecuta el comando equivalente a:

```bat
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name simulador_gui ^
    --icon assets\bascula.ico ^
    --collect-submodules serial ^
    simulador_gui.py
```

Claves del comando:

| Flag                          | Para qué sirve                                            |
|-------------------------------|-----------------------------------------------------------|
| `--onefile`                   | Un solo `.exe` autocontenido.                             |
| `--windowed` / `--noconsole`  | **Oculta la consola de fondo** (aplicación GUI).          |
| `--name simulador_gui`        | Nombre del ejecutable.                                    |
| `--icon assets\bascula.ico`   | Ícono opcional (colócalo en `assets\bascula.ico`).        |
| `--collect-submodules serial` | Incluye los backends de `pyserial` (`list_ports`, etc.).  |
| `--clean`                     | Limpia caché de builds anteriores.                        |

Resultado: `dist\simulador_gui.exe`. Ejecución:

```bat
dist\simulador_gui.exe --puerto COM10
```

> **Sin icono / sin `.bat`:** basta con ejecutar el comando `python -m PyInstaller ...`
> de arriba omitiendo `--icon`. Para depurar mostrando la consola, usa `--console`
> en lugar de `--windowed`.

---

## 4. Simulador por consola — `simulador_bascula.py`

### Modo automático (`--modo auto`)

Fluctuaciones de peso realistas que tienden a un objetivo aleatorio, con ruido al
estabilizarse y "vaciados" ocasionales.

```bash
python simulador_bascula.py --puerto COM10 --modo auto
```

### Modo interactivo / manual (`--modo manual`)

```bash
python simulador_bascula.py --puerto COM10 --modo manual
# Escribe:  15.45 <ENTER>  ->  envía "015.45 KG" repetidamente
```

> También existe `interfaz_bascula.py`, una interfaz gráfica ligera equivalente
> para enviar 1 kg / 500 g / 200 g / Vaciar (`python interfaz_bascula.py --puerto COM10`).

---

## 5. Lector de consola — `lector_bascula.py`

Se conecta al extremo opuesto, decodifica las tramas y las imprime como el POS:

```bash
# Windows (extremo opuesto)
python lector_bascula.py --puerto COM11 --baudrate 9600
# Linux
python lector_bascula.py --puerto /tmp/ttyPOS --baudrate 9600
```

Salida de ejemplo:

```
[INFO] POS conectado a /tmp/ttyPOS @ 9600 bps. Esperando tramas...
[ OK ] [12:04:01] PESO LEÍDO:     1.00 KG
[ OK ] [12:04:03] PESO LEÍDO:     0.50 KG
[12:04:04] (sin cambios)     0.50 KG
```

---

## 6. POS Web (Web Serial API) — `pos_web/`

Interfaz HTML/CSS/JS que lee la báscula directamente desde el navegador y calcula
el **precio total** en tiempo real.

**Requisito crítico:** la Web Serial API solo funciona en **contexto seguro**
(`http://localhost` o `https://`), **no** abriendo el `.html` con `file://`, y en
navegadores Chromium.

### Servir localmente

```bash
# Opción A: servidor integrado de Python (desde la raíz del proyecto)
python -m http.server 8000
# Abre: http://localhost:8000/pos_web/

# Opción B: extensión "Live Server" de VS Code -> "Go Live".
```

### Uso

1. Escribe el **Precio por Kg** (p. ej. `50.00`) y elige el **Baud Rate**.
2. Pulsa **Conectar Báscula** (el navegador pedirá autorizar el puerto; elige el
   extremo del POS, p. ej. `COM11` o `/dev/pts/6`).
3. Al llegar tramas como `000.50 KG`, el panel **Peso Actual** y el **Precio Total**
   se actualizan al instante. Si cambias el precio por kg, el total se recalcula
   con el último peso recibido.

**Puertos autorizados y auto-conexión:**

- El selector **Puerto autorizado** lista los puertos ya concedidos
  (`navigator.serial.getPorts()`). La Web Serial API recuerda el permiso, por lo
  que **si solo hay un puerto autorizado la app se conecta sola al cargar** (sin
  volver a pedir permiso). Con varios puertos, elige uno en la lista (conectarás
  al seleccionarlo) o pulsa **Conectar Báscula**.
- **Buscar otro puerto** vuelve a abrir el diálogo del navegador para autorizar
  un puerto distinto (por si tienes varias básculas o cambias de adaptador).
- El botón principal alterna **Conectar Báscula / Desconectar** según el estado
  real; ya **no** hace falta el ciclo manual desconectar→conectar para reconectar.

**Registro del frontend:** el panel *Registro de conexión* usa un **ring buffer**
(máx. `300` líneas) y **deduplica** las tramas repetidas en reposo (la báscula
reenvía `000.00 KG` cada 500 ms); así no crece sin límite en sesiones largas.
El botón **Descargar** exporta el registro a un `.txt`.

### Manejo de errores en el frontend

| Error               | Causa                                      | Acción en la UI                          |
|---------------------|--------------------------------------------|------------------------------------------|
| `SecurityError`     | Permisos denegados / falta gesto de usuario | Mensaje: usa localhost/HTTPS y el botón. |
| `NotFoundError`     | Se cerró el selector sin elegir puerto      | Aviso; vuelve a pulsar Conectar.         |
| `InvalidStateError` | El puerto ya está abierto                   | Detecta el estado y evita reabrir.       |
| `NetworkError`      | Dispositivo/cable desconectado              | **Reintentos automáticos** con backoff.  |
| `NotAllowedError`   | Falta interacción del usuario              | Pide pulsar el botón.                    |

Los reintentos usan `navigator.serial.getPorts()` (puertos ya autorizados) y
reintentan con espera exponencial hasta **5 intentos**.

---

## 7. Auditoría de puertos — `auditor_serial.py`

Diagnostica el entorno y el puerto destino del POS comprobando:

1. **Existencia** del puerto (lista del sistema).
2. **Bloqueo** por otro proceso (*Resource busy* / *Access denied*).
3. **Permisos** (p. ej. no pertenecer al grupo `dialout` en Linux).

```bash
python auditor_serial.py --puerto COM11
python auditor_serial.py --puerto /dev/pts/6
python auditor_serial.py --listar
```

Reporte de ejemplo:

```
== REPORTE Y SUGERENCIAS ==
  Estado: Faltan permisos para acceder al puerto.
  Comandos sugeridos:
          $ sudo usermod -aG dialout tuusuario
          $ newgrp dialout
          $ groups
```

Códigos de salida: `0` puerto libre, `1` existe pero no abrible, `2` no existe,
`3` error de entorno.

---

## 8. Opciones de línea de comandos

| Argumento          | Descripción                                    | Por defecto |
|--------------------|------------------------------------------------|-------------|
| `-p, --puerto`     | Puerto serial (COM10, /dev/pts/5, ...).        | `COM1`      |
| `-b, --baudrate`   | Velocidad en baudios.                          | `9600`      |
| `-m, --modo`       | `auto` o `manual` (solo `simulador_bascula`).  | `auto`      |
| `-l, --listar`     | Lista puertos seriales y sale.                 | —           |

Variables de entorno:

```bash
export BASCULA_PUERTO=/tmp/ttyBASCULA
export BASCULA_BAUDRATE=9600
export BASCULA_MODO=auto
export LECTOR_PUERTO=/tmp/ttyPOS
export AUDITOR_PUERTO=/dev/pts/6
```

---

## 9. Cierre y manejo de errores

- **Ctrl+C** en consola detiene de forma limpia (manejador `SIGINT`/`SIGTERM` +
  cierre del puerto en `finally`).
- **GUI:** al cerrar la ventana se detiene el hilo de envío (`threading.Event`) y
  se cierra el puerto.
- **Puerto ocupado / sin permisos / inexistente:** mensajes claros con la causa.
- **Desconexión:** el lector finaliza y el POS Web reintenta automáticamente.
- Los lectores usan **buffer** para reensamblar tramas que llegan partidas.

---

## 10. Pruebas E2E con Playwright (QA)

Suite End-to-End del POS Web en `pos_web/tests/pos.spec.js`. Como el navegador
no permite automatizar el diálogo nativo de permisos de la Web Serial API, la
prueba **mockea `navigator.serial`** con `page.addInitScript()` (antes de que
cargue `app.js`) y expone `window.__inyectarPeso()` para empujar tramas falsas.

Casos verificados por la suite:

1. Cálculo del **Precio Total = `$50.00`** al recibir `002.50 KG` con `$20.00/kg`.
2. **Recálculo instantáneo** del total al cambiar el precio por kg.
3. **Auto-conexión** al cargar cuando ya hay un puerto autorizado (`getPorts`).
4. **Reconexión sin recargar** (desconectar → conectar de nuevo).
5. **Deduplicación** del registro con tramas de cero repetidas.

### 10.1. Instalación (desde la carpeta `pos_web/`)

```bash
cd pos_web
npm init -y                          # crea package.json (ya incluido en el repo)
npm install -D @playwright/test      # instala el runner de Playwright
npx playwright install                # descarga navegadores (Chromium, etc.)
```

### 10.2. Ejecutar las pruebas y ver el reporte

```bash
npx playwright test          # ejecuta la suite (levanta el servidor solo)
npx playwright test --headed # viendo el navegador
npx playwright test --ui     # modo UI interactivo
npx playwright show-report   # abre el reporte HTML (playwright-report/)
```

> El `playwright.config.js` arranca automáticamente `python -m http.server 8000`
> para servir `pos_web/` en `http://127.0.0.1:8000` (contexto seguro que exige la
> Web Serial API). En CI usa `python3` si `python` no está disponible.

---

## 11. Registro de actividad (logging)

Todos los componentes Python escriben en un **único archivo global con rotación**
gestionado por `registro_bascula.py`:

- Ruta: `logs/bascula.log` (sobreescribible con `$BASCULA_LOG`).
- Rotación: `1 MiB` por archivo, `3` copias de respaldo (`bascula.log.1`, ...).
- Nivel: `INFO` por defecto (ajustable con `$BASCULA_LOG_NIVEL`).
- La salida de consola se mantiene (con color) además del archivo.

```bash
python simulador_gui.py --puerto COM4   # genera logs/bascula.log
python registro_bascula.py              # prueba rápida del logging
```

En el **POS Web** no hay escritura a disco silenciosa (los navegadores no lo
permiten): el registro vive en memoria acotada y se puede **Descargar** como
`.txt`.

> **Por qué no se deja de transmitir en cero:** el `000.00` es información
> semántica (báscula vacía). Si se dejara de enviar, el POS conservaría el último
> peso y mostraría un total incorrecto. Por eso se transmite de forma continua y
> solo se **deduplica el log** en reposo.

---

## 12. Estructura del proyecto

```
.
├── simulador_bascula.py   # Simulador por consola (auto/manual)
├── simulador_gui.py       # Simulador con GUI + hilo de envío
├── interfaz_bascula.py    # GUI ligera de pesajes rápidos
├── lector_bascula.py      # Lector POS de consola
├── auditor_serial.py      # Auditoría/diagnóstico de puertos seriales
├── par_pty.py             # Par de pty enlazados sin socat (Linux)
├── registro_bascula.py    # Logging global con rotación (logs/bascula.log)
├── build_exe.bat          # Empaquetado del .exe (PyInstaller, windowed)
├── pos_web/
│   ├── index.html         # POS Web (Web Serial API)
│   ├── style.css          # Estilos
│   ├── app.js             # Lógica de conexión y cálculo
│   ├── package.json       # Proyecto Node para QA
│   ├── playwright.config.js
│   └── tests/
│       └── pos.spec.js    # Pruebas E2E (mock de navigator.serial)
├── requirements.txt       # Dependencias (pyserial)
└── README.md              # Este archivo
```

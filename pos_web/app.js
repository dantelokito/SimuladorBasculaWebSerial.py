/* =====================================================================
   POS Web - Lógica de lectura de báscula por Web Serial API
   - Conexión al puerto serial virtual (selector de puertos autorizados).
   - Auto-conexión al cargar si solo hay un puerto autorizado (getPorts()).
   - Bucle de lectura asíncrono con TextDecoderStream + separador de líneas.
   - Cálculo de precio en tiempo real (peso x precio por kg).
   - Registro acotado (ring buffer) con deduplicación de tramas repetidas.
   - Manejo y clasificación de errores + reintentos automáticos.

   Requiere un navegador basado en Chromium (Chrome/Edge) y un
   contexto seguro (http://localhost o https://).
   ===================================================================== */

/* =====================================================================
   [DEMO] POS Web de DEMOSTRACIÓN a clientes.
   - Usa Web Serial API en el NAVEGADOR: solo válido para la demo. En
     producción el POS NO abre el puerto serial; consume un SERVICIO de
     báscula por REST/WebSocket (ver guia_integracion_bascula_produccion.txt
     y guia_pos_completo_produccion.txt).
   - [DEMO]   = código a descartar/sustituir en producción:
               requestPort/getPorts, selector de puertos, el "abrirPuerto".
   - [CONSERVAR] = patrones reutilizables: máquina de estados, dedupe de
               log, ring buffer, parseo de trama y cálculo del total.
   ===================================================================== */

'use strict';

/* ----------------------------- Referencias DOM ---------------------- */
const $ = (sel) => document.querySelector(sel);

const precioKgEl     = $('#precioKg');
const baudRateEl     = $('#baudRate');
const puertoSelectEl = $('#puertoSelect');
const btnConectarEl  = $('#btnConectar');
const btnBuscarEl    = $('#btnBuscar');
const btnLimpiarEl   = $('#btnLimpiar');
const btnDescargarEl = $('#btnDescargar');
const pesoActualEl   = $('#pesoActual');
const precioTotalEl  = $('#precioTotal');
const detalleEl      = $('#detalleCalculo');
const estadoBadgeEl  = $('#estadoBadge');
const logEl          = $('#log');
const soporteEl      = $('#soporte');

/* ----------------------------- Estado ------------------------------- */
let puerto = null;             // SerialPort actual
let lector = null;             // ReadableStreamDefaultReader
let leyendo = false;           // ¿bucle de lectura activo?
let bufferTexto = '';          // acumulador de líneas parciales
let ultimoPeso = 0;            // último peso válido (kg)
let ultimoLogPeso = null;      // dedupe del registro (no repetir tramas iguales)
let puertosAutorizados = [];   // SerialPort[] ya autorizados (getPorts)
let intentosReconexion = 0;
const MAX_REINTENTOS = 5;
const MAX_LOG_LINEAS = 300;    // ring buffer del registro

// Máquina de estados: desconectado | conectando | conectado | error
// [CONSERVAR] patrón de estados reutilizable en el backend/servicio.
let estado = 'desconectado';

/* Formato de trama esperado: "001.00 KG\r\n"
   [CONSERVAR] Si la báscula real usa el mismo protocolo, se reutiliza. */
const PATRON_PESO = /^([+-]?\d{1,4}(?:[.,]\d{1,2})?)\s*(KG|LB|G|OZ)?$/i;

/* ----------------------------- Utilidades --------------------------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function log(mensaje, tipo = 'info') {
  const li = document.createElement('li');
  li.className = tipo;
  const hora = new Date().toLocaleTimeString('es-ES');
  li.textContent = `[${hora}] ${mensaje}`;
  logEl.appendChild(li);
  // Ring buffer: descarta las entradas más antiguas para no crecer sin límite.
  while (logEl.children.length > MAX_LOG_LINEAS) {
    logEl.removeChild(logEl.firstElementChild);
  }
  logEl.scrollTop = logEl.scrollHeight;
}

function setEstado(estadoBadge, texto) {
  estadoBadgeEl.dataset.estado = estadoBadge;
  estadoBadgeEl.textContent = texto;
}

function formatearMoneda(valor) {
  // Locale fijo (en-US) para un formato determinista "$50.00", estable en CI.
  return valor.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
  });
}

function soportado() {
  return 'serial' in navigator;
}

/* ----------------------------- Cálculo ------------------------------ */
function actualizarCalculo(peso) {
  const precio = parseFloat(precioKgEl.value) || 0;
  const total = peso * precio;
  precioTotalEl.textContent = formatearMoneda(total);
  detalleEl.textContent =
    `${peso.toFixed(2)} kg × ${formatearMoneda(precio)} = ${formatearMoneda(total)}`;
}

/* ----------------------------- UI: estado --------------------------- */
function actualizarBoton() {
  const conectado = estado === 'conectado';
  btnConectarEl.textContent = conectado ? 'Desconectar' : 'Conectar Báscula';
  btnConectarEl.dataset.conectado = conectado ? 'true' : 'false';
  btnConectarEl.disabled = estado === 'conectando';
}

function cambiarEstado(nuevo, texto) {
  estado = nuevo;
  actualizarBoton();
  if (estadoBadgeEl) setEstado(nuevo, texto);
}

/* ----------------------------- UI: puertos -------------------------- */
function etiquetaPuerto(p, indice) {
  let extra = '';
  try {
    const info = typeof p.getInfo === 'function' ? p.getInfo() : null;
    if (info) {
      const vid = info.usbVendorId != null ? info.usbVendorId : '—';
      const pid = info.usbProductId != null ? info.usbProductId : '—';
      extra = ` (VID:${vid} PID:${pid})`;
    }
  } catch (_) { /* getInfo no siempre está disponible */ }
  return `Puerto autorizado #${indice + 1}${extra}`;
}

function puertoSeleccionado() {
  const indice = parseInt(puertoSelectEl.value, 10);
  if (Number.isInteger(indice) && puertosAutorizados[indice]) {
    return puertosAutorizados[indice];
  }
  return null;
}

function actualizarListaPuertos() {
  const valorPrevio = puertoSelectEl.value;
  puertoSelectEl.innerHTML = '';

  if (puertosAutorizados.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '— Sin puertos autorizados —';
    puertoSelectEl.appendChild(opt);
    puertoSelectEl.disabled = true;
    return;
  }

  puertoSelectEl.disabled = false;
  puertosAutorizados.forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = etiquetaPuerto(p, i);
    puertoSelectEl.appendChild(opt);
  });

  if (valorPrevio && puertosAutorizados[Number(valorPrevio)]) {
    puertoSelectEl.value = valorPrevio;
  }
}

async function refrescarPuertosAutorizados() {
  // [DEMO] getPorts()/selector de puertos: específico de Web Serial API.
  try {
    puertosAutorizados = await navigator.serial.getPorts();
  } catch (_) {
    puertosAutorizados = [];
  }
  actualizarListaPuertos();
  return puertosAutorizados;
}

function seleccionarPuertoEnLista(p) {
  const indice = puertosAutorizados.indexOf(p);
  if (indice >= 0) puertoSelectEl.value = String(indice);
}

/* ----------------------------- Conexión ----------------------------- */
// [DEMO] Toda esta sección depende de Web Serial API. En producción se
// sustituye por un cliente del servicio de báscula (fetch/WebSocket a).
async function conectar() {
  if (!soportado()) {
    log('Este navegador no soporta Web Serial API. Usa Chrome o Edge.', 'error');
    cambiarEstado('error', 'Navegador no compatible');
    return;
  }

  const seleccionado = puertoSeleccionado();

  try {
    if (seleccionado) {
      cambiarEstado('conectando', 'Conectando...');
      log('Abriendo puerto autorizado...');
      await abrirPuerto(seleccionado);
      return;
    }

    cambiarEstado('conectando', 'Selecciona el puerto...');
    log('Solicitando puerto serial al usuario...');
    // requestPort() DEBE llamarse desde un gesto del usuario (click).
    const p = await navigator.serial.requestPort();
    await refrescarPuertosAutorizados();
    await abrirPuerto(p);
  } catch (error) {
    manejarError(error);
  }
}

async function buscarOtroPuerto() {
  if (!soportado()) {
    manejarError(new DOMException('Web Serial API no disponible.', 'SecurityError'));
    return;
  }
  try {
    cambiarEstado('conectando', 'Selecciona el puerto...');
    const p = await navigator.serial.requestPort();
    await refrescarPuertosAutorizados();
    if (estado === 'conectado') {
      await desconectar(true);
    }
    await abrirPuerto(p);
  } catch (error) {
    manejarError(error);
  }
}

async function abrirPuerto(p) {
  const baudRate = parseInt(baudRateEl.value, 10);

  if (p.readable || p.writable) {
    throw new DOMException('El puerto ya está abierto.', 'InvalidStateError');
  }

  try {
    await p.open({ baudRate });
  } catch (error) {
    puerto = null;
    throw error;
  }

  puerto = p;
  leyendo = true;
  bufferTexto = '';
  ultimoLogPeso = null;
  intentosReconexion = 0;
  seleccionarPuertoEnLista(p);

  cambiarEstado('conectado', `Conectado @ ${baudRate} bps`);
  log(`Puerto abierto correctamente a ${baudRate} bps.`, 'ok');

  leerBucle(); // no se hace await: vive mientras la conexión esté activa
}

async function desconectar(silencioso = false) {
  leyendo = false;

  // 1) Cancelar el lector y liberar el lock del stream.
  if (lector) {
    try { await lector.cancel(); } catch (_) { /* ignore */ }
    try { lector.releaseLock(); } catch (_) { /* ignore */ }
    lector = null;
  }

  // 2) Cerrar el puerto si sigue abierto.
  if (puerto) {
    try {
      if (puerto.readable || puerto.writable) {
        await puerto.close();
      }
    } catch (error) {
      console.warn('Error al cerrar el puerto:', error);
    }
  }

  // 3) Resetear el estado (evita el bug de no poder reconectar).
  puerto = null;
  ultimoLogPeso = null;
  cambiarEstado('desconectado', 'Desconectado');

  if (!silencioso) {
    log('Puerto cerrado.');
  }
}

async function reconectar(p) {
  await desconectar(true);
  log('Cambiando de puerto...');
  await abrirPuerto(p);
}

/* ----------------------------- Bucle de lectura --------------------- */
async function leerBucle() {
  const decodificador = new TextDecoderStream();
  const flujoCerrado = puerto.readable
    .pipeTo(decodificador.writable)
    .catch(() => { /* el cierre se gestiona abajo */ });

  lector = decodificador.readable.getReader();

  try {
    while (leyendo) {
      const { value, done } = await lector.read();
      if (done) break;
      if (value) procesarTexto(value);
    }
  } catch (error) {
    if (leyendo) {
      manejarError(error);
      // Si se perdió la conexión física, intentamos reconectar solo.
      if (error.name === 'NetworkError') {
        await limpiarTrasDesconexion();
        intentarReconexionAutomatica();
      }
    }
  } finally {
    try { lector && lector.releaseLock(); } catch (_) { /* ignore */ }
    lector = null;
    await flujoCerrado;
  }
}

async function limpiarTrasDesconexion() {
  leyendo = false;
  if (lector) {
    try { lector.releaseLock(); } catch (_) { /* ignore */ }
    lector = null;
  }
  if (puerto) {
    try {
      if (puerto.readable || puerto.writable) await puerto.close();
    } catch (_) { /* ignore */ }
  }
  puerto = null;
  cambiarEstado('desconectado', 'Desconectado');
}

/* ----------------------------- Procesado de texto ------------------- */
function procesarTexto(texto) {
  bufferTexto += texto;
  let indice;
  while ((indice = bufferTexto.indexOf('\n')) >= 0) {
    const linea = bufferTexto.slice(0, indice).replace(/\r/g, '').trim();
    bufferTexto = bufferTexto.slice(indice + 1);
    if (linea) procesarLinea(linea);
  }
}

function parsearPeso(linea) {
  const coincidencia = PATRON_PESO.exec(linea);
  if (!coincidencia) return null;
  const peso = parseFloat(coincidencia[1].replace(',', '.'));
  if (!Number.isFinite(peso)) return null;
  return { peso, unidad: (coincidencia[2] || 'KG').toUpperCase() };
}

function procesarLinea(linea) {
  const lectura = parsearPeso(linea);
  if (!lectura) {
    log(`Trama no reconocida: "${linea}"`, 'error');
    return;
  }

  ultimoPeso = lectura.peso;
  pesoActualEl.innerHTML = `${lectura.peso.toFixed(2)} <span>${lectura.unidad}</span>`;
  actualizarCalculo(lectura.peso);

  // Dedupe: en reposo la báscula reenvía la misma trama cada 500 ms; no
  // ensuciamos el registro con cientos de líneas idénticas.
  // [CONSERVAR] patrón reutilizable (también aplicable al servicio/backend).
  const clave = `${lectura.peso.toFixed(2)} ${lectura.unidad}`;
  if (clave !== ultimoLogPeso) {
    if (lectura.peso === 0) {
      log(`Báscula en cero (${lectura.unidad}).`);
    } else {
      log(`Peso recibido: ${clave}`, 'ok');
    }
    ultimoLogPeso = clave;
  }
}

/* ----------------------------- Reintentos --------------------------- */
async function intentarReconexionAutomatica() {
  if (intentosReconexion >= MAX_REINTENTOS) {
    cambiarEstado('error', 'Reintentos agotados. Pulsa "Conectar Báscula".');
    log('Se agotaron los reintentos automáticos.', 'error');
    return;
  }

  const puertos = await refrescarPuertosAutorizados();
  if (puertos.length === 0) {
    cambiarEstado('error', 'Pulsa "Conectar Báscula" para autorizar un puerto.');
    return;
  }

  intentosReconexion += 1;
  const espera = Math.min(500 * 2 ** intentosReconexion, 5000);
  cambiarEstado('conectando', `Reintento ${intentosReconexion}/${MAX_REINTENTOS}...`);
  log(`Reintentando en ${(espera / 1000).toFixed(1)} s...`, 'info');

  await sleep(espera);

  try {
    await abrirPuerto(puertos[0]);
    log('Reconexión exitosa.', 'ok');
  } catch (error) {
    manejarError(error);
    await limpiarTrasDesconexion();
    intentarReconexionAutomatica();
  }
}

/* ----------------------------- Manejo de errores -------------------- */
function manejarError(error) {
  const nombre = error && error.name ? error.name : 'Error';
  let mensaje;

  switch (nombre) {
    case 'SecurityError':
      mensaje = 'Permisos denegados. La Web Serial API exige un gesto del ' +
                'usuario y un contexto seguro (localhost o HTTPS).';
      break;
    case 'NotFoundError':
      mensaje = 'No seleccionaste ningún puerto (el diálogo se cerró sin elegir).';
      break;
    case 'InvalidStateError':
      mensaje = 'El puerto ya está abierto o en un estado no válido.';
      break;
    case 'NetworkError':
      mensaje = 'Se perdió la conexión con el dispositivo (cable/adaptador desconectado).';
      break;
    case 'NotAllowedError':
      mensaje = 'El navegador bloqueó la operación por falta de interacción del usuario.';
      break;
    case 'TypeError':
      mensaje = 'Operación no válida. Asegúrate de pulsar el botón "Conectar Báscula".';
      break;
    default:
      mensaje = (error && error.message) ? error.message : 'Error desconocido.';
  }

  console.error(`[${nombre}]`, error);
  log(`${nombre}: ${mensaje}`, 'error');
  cambiarEstado('error', `${nombre}: ${mensaje}`);
}

/* ----------------------------- Registro: descarga ------------------- */
// [CONSERVAR] exportación del registro; reutilizable en la UI real.
function descargarLog() {
  const lineas = Array.from(logEl.querySelectorAll('li'))
    .map((li) => li.textContent)
    .join('\n');
  const contenido = lineas + (lineas ? '\n' : '');
  const marca = new Date().toISOString().replace(/[:.]/g, '-');
  const blob = new Blob([contenido], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const enlace = document.createElement('a');
  enlace.href = url;
  enlace.download = `bascula-log-${marca}.txt`;
  document.body.appendChild(enlace);
  enlace.click();
  document.body.removeChild(enlace);
  URL.revokeObjectURL(url);
  log('Registro descargado.', 'ok');
}

/* ----------------------------- Eventos ------------------------------ */
btnConectarEl.addEventListener('click', () => {
  if (estado === 'conectado' || lector) {
    desconectar();
  } else {
    conectar();
  }
});

btnBuscarEl.addEventListener('click', () => { buscarOtroPuerto(); });

puertoSelectEl.addEventListener('change', () => {
  const p = puertoSeleccionado();
  if (!p) return;
  if (estado === 'conectado') {
    reconectar(p);
  } else {
    abrirPuerto(p).catch((error) => manejarError(error));
  }
});

// Recalcular el total al instante si cambia el precio por kg.
precioKgEl.addEventListener('input', () => actualizarCalculo(ultimoPeso));

btnLimpiarEl.addEventListener('click', () => { logEl.innerHTML = ''; });

btnDescargarEl.addEventListener('click', () => { descargarLog(); });

// Detectar conexión/desconexión física del dispositivo.
navigator.serial?.addEventListener('connect', () => {
  log('Nuevo dispositivo serial detectado.');
  refrescarPuertosAutorizados();
});

navigator.serial?.addEventListener('disconnect', (evento) => {
  if (evento.target === puerto) {
    log('Dispositivo desconectado (evento "disconnect").', 'error');
    leyendo = false;
    puerto = null;
    cambiarEstado('error', 'Dispositivo desconectado');
  }
});

/* ----------------------------- Arranque ----------------------------- */
(async function init() {
  actualizarBoton();
  actualizarListaPuertos();

  if (!soportado()) {
    soporteEl.textContent =
      'Web Serial API NO disponible. Usa Chrome/Edge sobre localhost o HTTPS.';
    cambiarEstado('error', 'Navegador no compatible');
    log('Tu navegador no soporta Web Serial API.', 'error');
    actualizarCalculo(0);
    return;
  }

  soporteEl.textContent = 'Web Serial API disponible. Listo para conectar.';
  cambiarEstado('desconectado', 'Desconectado');
  actualizarCalculo(0);

  // Auto-conexión: la Web Serial API recuerda los puertos ya autorizados,
  // así que si solo hay uno lo abrimos sin pedir permiso de nuevo.
  try {
    await refrescarPuertosAutorizados();
    if (puertosAutorizados.length === 1) {
      log('Puerto autorizado detectado. Conectando automáticamente...');
      await abrirPuerto(puertosAutorizados[0]);
    } else if (puertosAutorizados.length > 1) {
      log(`${puertosAutorizados.length} puertos autorizados. Elige uno en la lista.`);
    }
  } catch (_) {
    // getPorts() puede no estar disponible en algunos contextos.
  }
})();

// tests/pos.spec.js
// =====================================================================
// [DEMO] Pruebas E2E del POS web de demostración.
//        El mock de navigator.serial SOLO existe porque la demo usa Web
//        Serial API en el navegador. En producción se hacen pruebas
//        hardware-in-the-loop contra el servicio de báscula real
//        (ver guia_integracion_bascula_produccion.txt, sección QA).
// =====================================================================
// Pruebas End-to-End (E2E) del POS Web con Playwright.
//
// Reto: las pruebas automatizadas NO pueden pulsar el diálogo nativo de
// permisos de la Web Serial API. Solución: "mockear" navigator.serial
// ANTES de que cargue app.js mediante page.addInitScript(), de modo que
// requestPort()/getPorts() devuelvan un puerto falso controlable.
//
// Casos cubiertos:
//   1. Cálculo del total al recibir 002.50 KG con $20.00/kg  -> $50.00
//   2. Recalculo instantáneo al cambiar el precio por Kg.
//   3. Auto-conexión al cargar cuando ya hay 1 puerto autorizado (getPorts).
//   4. Reconexión sin recargar (desconectar -> conectar de nuevo).
//   5. Deduplicación del registro con tramas de cero repetidas.
// =====================================================================

const { test, expect } = require('@playwright/test');

/**
 * Instala un mock de navigator.serial en la página.
 *
 * @param {import('@playwright/test').Page} page
 * @param {{autorizados?: number}} [opciones]
 *        autorizados: nº de puertos ya autorizados antes de cargar la app
 *        (0 => no hay auto-conexión; 1 => la app se conecta sola).
 *
 * Expone:
 *   window.__inyectarPeso(texto)      -> empuja una trama al stream de lectura.
 *   window.__emitirSerial('disconnect') -> dispara un evento serial.
 */
async function instalarMockSerial(page, opciones = {}) {
  const { autorizados = 0 } = opciones;

  await page.addInitScript(({ autorizados }) => {
    const encoder = new TextEncoder();
    let controladorActivo = null;
    let autorizado = autorizados > 0;
    const oyentes = { connect: [], disconnect: [] };

    // Puerto serial falso que imita la forma de un SerialPort real.
    const puertoFalso = {
      readable: null,
      writable: null,
      connected: false,

      getInfo() {
        return { usbVendorId: 0x1234, usbProductId: 0x5678 };
      },

      async open() {
        // app.js comprueba (readable || writable) para detectar
        // "InvalidStateError", por lo que deben empezar en null.
        const stream = new ReadableStream({
          start(controller) {
            controladorActivo = controller;
          },
          cancel() {
            controladorActivo = null;
          },
        });
        this.readable = stream;
        this.writable = new WritableStream();
        this.connected = true;
      },

      async close() {
        try { controladorActivo && controladorActivo.close(); } catch (_) { /* noop */ }
        controladorActivo = null;
        this.readable = null;
        this.writable = null;
        this.connected = false;
      },
    };

    const serialFalso = {
      // Simula la concesión de permiso: a partir de aquí queda "autorizado".
      requestPort: async () => {
        autorizado = true;
        return puertoFalso;
      },
      // La Web Serial API solo devuelve puertos ya autorizados.
      getPorts: async () => (autorizado ? [puertoFalso] : []),
      addEventListener: (tipo, fn) => {
        if (oyentes[tipo]) oyentes[tipo].push(fn);
      },
      removeEventListener: (tipo, fn) => {
        if (oyentes[tipo]) {
          oyentes[tipo] = oyentes[tipo].filter((f) => f !== fn);
        }
      },
    };

    Object.defineProperty(navigator, 'serial', {
      configurable: true,
      get: () => serialFalso,
    });

    // Empuja una trama (string) al lado legible del puerto falso.
    window.__inyectarPeso = (texto) => {
      if (!controladorActivo) {
        throw new Error('El puerto mock no está abierto (llama antes a Conectar).');
      }
      controladorActivo.enqueue(encoder.encode(texto));
    };

    // Dispara un evento 'connect' o 'disconnect' del mock.
    window.__emitirSerial = (tipo) => {
      (oyentes[tipo] || []).forEach((fn) => {
        try { fn({ target: puertoFalso, type: tipo }); } catch (_) { /* noop */ }
      });
    };
  }, { autorizados });
}

test.describe('POS Web · integración con báscula (Web Serial API mockeada)', () => {

  test('calcula el precio total ($50.00) al recibir 002.50 KG con $20.00/kg', async ({ page }) => {
    // --- Given: mock instalado antes de cargar la app -----------------
    await instalarMockSerial(page);
    await page.goto('/index.html');

    // La app detecta el soporte de la Web Serial (mockeada).
    await expect(page.locator('#soporte')).toContainText('Web Serial API disponible');
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'desconectado');

    // --- When: conectar la báscula ------------------------------------
    await page.getByRole('button', { name: 'Conectar Báscula' }).click();
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'conectado');
    await expect(page.getByRole('button', { name: 'Desconectar' })).toBeVisible();

    // --- When: fijar el precio por Kg = $20.00 ------------------------
    await page.fill('#precioKg', '20.00');

    // --- When: inyectar el peso 002.50 KG desde la báscula simulada ---
    await page.evaluate(() => window.__inyectarPeso('002.50 KG\r\n'));

    // --- Then: el DOM refleja peso y total calculados -----------------
    await expect(page.locator('#pesoActual')).toContainText('2.50');
    await expect(page.locator('#pesoActual')).toContainText('KG');
    await expect(page.locator('#precioTotal')).toHaveText('$50.00');
    await expect(page.locator('#detalleCalculo')).toContainText('$50.00');

    // El registro de conexión confirma la trama recibida.
    await expect(page.locator('#log')).toContainText('Peso recibido: 2.50 KG');
  });

  test('recalcula el total al instante si cambia el precio por Kg', async ({ page }) => {
    await instalarMockSerial(page);
    await page.goto('/index.html');

    await page.getByRole('button', { name: 'Conectar Báscula' }).click();
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'conectado');

    await page.fill('#precioKg', '20.00');
    await page.evaluate(() => window.__inyectarPeso('002.50 KG\r\n'));
    await expect(page.locator('#precioTotal')).toHaveText('$50.00');

    // Cambiar el precio recalcula con el último peso recibido (2.50 kg).
    await page.fill('#precioKg', '10.00');
    await expect(page.locator('#precioTotal')).toHaveText('$25.00');
  });

  test('se auto-conecta al cargar si ya hay un puerto autorizado (getPorts)', async ({ page }) => {
    await instalarMockSerial(page, { autorizados: 1 });
    await page.goto('/index.html');

    // Sin pulsar nada: la app reconoce el puerto guardado y conecta sola.
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'conectado');
    await expect(page.getByRole('button', { name: 'Desconectar' })).toBeVisible();
    await expect(page.locator('#log')).toContainText('Conectando automáticamente');
  });

  test('permite reconectar sin recargar la página (desconectar -> conectar)', async ({ page }) => {
    await instalarMockSerial(page);
    await page.goto('/index.html');

    // 1er ciclo: conectar y desconectar.
    await page.getByRole('button', { name: 'Conectar Báscula' }).click();
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'conectado');
    await page.getByRole('button', { name: 'Desconectar' }).click();
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'desconectado');

    // 2º ciclo: debe volver a conectar con un solo clic (bug corregido).
    await page.getByRole('button', { name: 'Conectar Báscula' }).click();
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'conectado');
    await expect(page.getByRole('button', { name: 'Desconectar' })).toBeVisible();
  });

  test('deduplica el registro con tramas de cero repetidas', async ({ page }) => {
    await instalarMockSerial(page);
    await page.goto('/index.html');

    await page.getByRole('button', { name: 'Conectar Báscula' }).click();
    await expect(page.locator('#estadoBadge')).toHaveAttribute('data-estado', 'conectado');

    // Tres tramas idénticas en cero (como en reposo a 2 Hz).
    await page.evaluate(() => window.__inyectarPeso('000.00 KG\r\n000.00 KG\r\n000.00 KG\r\n'));

    // Solo se registra una vez.
    await expect(
      page.locator('#log li', { hasText: 'Báscula en cero' })
    ).toHaveCount(1);
  });
});

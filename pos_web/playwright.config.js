// playwright.config.js
// [DEMO] Config de QA de la demo (POS web con Web Serial mockeada).
//        En producción la suite E2E apunta al POS real y al servicio de
//        báscula (ver guias .txt).
// Configuración de Playwright para las pruebas E2E del POS Web.
// Contexto seguro garantizado: se sirve por http://127.0.0.1 (localhost),
// requisito de la Web Serial API.

const { defineConfig, devices } = require('@playwright/test');

const PUERTO = 8000;
const BASE_URL = `http://127.0.0.1:${PUERTO}`;

module.exports = defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],

  use: {
    baseURL: BASE_URL,
    headless: true,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],

  // Levanta automáticamente el servidor estático que sirve pos_web/.
  webServer: {
    command: `python -m http.server ${PUERTO} --bind 127.0.0.1`,
    url: `${BASE_URL}/index.html`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});

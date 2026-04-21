import { defineConfig } from '@playwright/test';

const DEMO_URL = '/plugin/tests/record/demo.html';

export default defineConfig({
  testDir: '.',
  fullyParallel: false,
  retries: 0,
  timeout: 120_000,

  use: {
    baseURL: 'http://localhost:8765',
  },

  projects: [
    {
      name: 'desktop-16x9',
      use: {
        viewport: { width: 1920, height: 1080 },
        video:    { mode: 'on', size: { width: 1920, height: 1080 } },
      },
    },
    {
      name: 'mobile-9x16',
      use: {
        viewport: { width: 540, height: 960 },
        video:    { mode: 'on', size: { width: 540, height: 960 } },
      },
    },
  ],

  webServer: {
    command: 'python3 -m http.server 8765',
    cwd: '../../..',
    port: 8765,
    reuseExistingServer: true,
    stdout: 'ignore',
    stderr: 'pipe',
  },

  outputDir: './videos',
});

export { DEMO_URL };

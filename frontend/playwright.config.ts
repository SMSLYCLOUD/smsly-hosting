import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3000',
    headless: true,
    ignoreHTTPSErrors: true,
  },
  reporter: [
    ['list'],
    ['html', { open: 'never' }],
  ],
});

import { defineConfig } from '@playwright/test';

const browserChannel = process.env.TORQUE_PLAYWRIGHT_CHANNEL;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.TORQUE_UI_BASE_URL || 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    ...(browserChannel ? { channel: browserChannel } : {}),
  },
});

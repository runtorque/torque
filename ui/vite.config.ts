import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const PROXY_PATHS = [
  '/ws',
  '/api',
  '/events',
  '/mcp',
  '/attachments',
  '/legacy',
  '/logs',
  '/static',
] as const;

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'TORQUE_');
  const daemonOrigin =
    env.TORQUE_UI_DAEMON_ORIGIN ||
    process.env.TORQUE_UI_DAEMON_ORIGIN ||
    `http://127.0.0.1:${process.env.TORQUE_PORT || '18932'}`;

  return {
    base: './',
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: Object.fromEntries(
        PROXY_PATHS.map((path) => [
          path,
          {
            target: daemonOrigin,
            changeOrigin: false,
            ws: path === '/ws',
          },
        ]),
      ),
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      manifest: true,
      sourcemap: false,
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
      restoreMocks: true,
      clearMocks: true,
      coverage: {
        reporter: ['text', 'html'],
      },
    },
  };
});

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Proxy target for /api and /mcp. Defaults to localhost:8000 for host-side dev
// (`pnpm dev`); the docker-compose frontend service overrides it to the in-network
// api hostname (http://api:8000), since `localhost` inside the container is the
// frontend container itself.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': apiProxyTarget,
      '/mcp': apiProxyTarget,
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      thresholds: {
        lines: 0, // raised to 80 in Week 8
      },
    },
  },
});

import { configDefaults, defineConfig } from 'vitest/config';
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
  build: {
    rollupOptions: {
      output: {
        // Split the heavy, rarely-changing vendors into their own long-cache
        // chunks so the app chunk stays small and an app change doesn't bust the
        // whole vendor bundle. Routes are additionally lazy-split (App.tsx).
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('/antd/') || id.includes('@ant-design/')) return 'antd';
          if (id.includes('@azure/')) return 'msal';
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/react-router') ||
            id.includes('/scheduler/')
          ) {
            return 'react';
          }
          return 'vendor';
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    // Playwright specs live in e2e/ and run under `pnpm e2e`, not Vitest — they
    // import @playwright/test and drive a browser, so keep Vitest out of them.
    exclude: [...configDefaults.exclude, 'e2e/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      thresholds: {
        lines: 0, // raised to 80 in Week 8
      },
    },
  },
});

// Dev/build stub for runtime app config (ADR 0028).
//
// Intentionally does NOT set window.__DATAQ_CONFIG__ — in `pnpm dev` that makes
// src/auth/config.ts fall back to the build-time VITE_* env. In the container the
// nginx image serves /config.js dynamically from DATAQ_AUTH_* env (an exact-match
// location that shadows this static file), so this copy is never served there.

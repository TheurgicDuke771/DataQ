// Build the read-only static demo (#2419): the real SPA, compiled with VITE_DEMO so the API
// client answers from the recorded responses in demo-fixtures.json (written by the
// `demo-capture` Playwright project). Output: dist-demo/, servable from DEMO_BASE.
import { copyFileSync, existsSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.DEMO_BASE || '/DataQ/demo/';
const fixtures = resolve(root, 'demo-fixtures.json');
const outDir = resolve(root, 'dist-demo');

if (!/^\/([\w.-]+\/)*$/.test(base)) {
  throw new Error(`DEMO_BASE must be an absolute path ending in "/" (got ${base})`);
}
if (!existsSync(fixtures)) {
  throw new Error('demo-fixtures.json is missing — record it first: pnpm demo:capture');
}

process.env.VITE_DEMO = 'true';
await build({ root, base, build: { outDir, emptyOutDir: true } });

// The runtime config the nginx image would render (ADR 0028): no IdP. The second half undoes
// the redirect a static host's 404 page makes for a deep link (marketing/404.html).
writeFileSync(
  resolve(outDir, 'config.js'),
  `window.__DATAQ_CONFIG__ = { auth: { mode: 'bypass' } };
(function () {
  var route = new URLSearchParams(location.search).get('__route');
  if (route && route.charAt(0) === '/' && route.charAt(1) !== '/') {
    history.replaceState(null, '', ${JSON.stringify(base.replace(/\/$/, ''))} + route);
  }
})();
`,
);
copyFileSync(fixtures, resolve(outDir, 'demo-fixtures.json'));
console.log(`demo built for ${base} → ${outDir}`);

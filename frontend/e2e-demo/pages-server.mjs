// A static server that behaves like the published GitHub Pages site (#2419): marketing/ at the
// site root, dist-demo/ under DEMO_BASE, and — as Pages does — the root 404.html with a 404
// status for any path that is not a file. `vite preview` would serve index.html for a deep
// link, which hides exactly the redirect the smoke test is there to prove.
import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const demoBase = process.env.DEMO_BASE || '/DataQ/demo/';
const siteBase = demoBase.replace(/demo\/$/, '');
const marketing = resolve(here, '../../marketing');
const demo = resolve(here, '../dist-demo');
const port = Number(process.env.E2E_DEMO_PORT || 4173);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
  '.mp4': 'video/mp4',
};

function fileFor(pathname) {
  let root;
  let rest;
  if (pathname.startsWith(demoBase)) [root, rest] = [demo, pathname.slice(demoBase.length)];
  else if (pathname.startsWith(siteBase))
    [root, rest] = [marketing, pathname.slice(siteBase.length)];
  else return null;
  const target = join(root, normalize(rest));
  if (!target.startsWith(root)) return null;
  const file =
    existsSync(target) && statSync(target).isDirectory() ? join(target, 'index.html') : target;
  return existsSync(file) && statSync(file).isFile() ? file : null;
}

createServer((req, res) => {
  const { pathname } = new URL(req.url, 'http://localhost');
  const found = fileFor(decodeURIComponent(pathname));
  const file = found ?? join(marketing, '404.html');
  res.writeHead(found ? 200 : 404, {
    'content-type': TYPES[extname(file)] ?? 'application/octet-stream',
  });
  createReadStream(file).pipe(res);
}).listen(port, '127.0.0.1', () => console.log(`pages-like server on :${port}${siteBase}`));

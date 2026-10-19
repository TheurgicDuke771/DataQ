// Render THIRD-PARTY-NOTICES.md for the frontend image from `pnpm licenses list --json --prod`:
// the runtime dependencies the Vite bundle ships, each with its licence text (and any NOTICE)
// as found in node_modules. Rendering only — the allowlist gate is scripts/check-licenses.py
// (run in CI over the whole lockfile), which is why an unknown licence here is a hard error
// rather than a policy decision. Usage: node scripts/third-party-notices.mjs OUT.md
import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = process.argv[2];
if (!out) throw new Error('usage: third-party-notices.mjs OUT.md');

const raw = execFileSync('pnpm', ['licenses', 'list', '--json', '--prod'], {
  cwd: root,
  encoding: 'utf8',
  stdio: ['ignore', 'pipe', 'inherit'],
});
const packages = Object.values(JSON.parse(raw))
  .flat()
  .sort((a, b) => a.name.localeCompare(b.name));

const TEXT_FILE = /^(licen[cs]e|copying|notice)/i;
function texts(dir) {
  return readdirSync(dir)
    .filter((f) => TEXT_FILE.test(f) && statSync(join(dir, f)).isFile())
    .sort()
    .map((f) => [f, readFileSync(join(dir, f), 'utf8')]);
}
// One entry per installed copy: a package present in two versions may carry two licence texts.
function copies(p) {
  const seen = new Map();
  for (const dir of p.paths) {
    const { version } = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
    if (!seen.has(version)) seen.set(version, texts(dir));
  }
  return [...seen.entries()].sort(([a], [b]) => a.localeCompare(b));
}
const cell = (v) => String(v ?? '').replace(/\|/g, '\\|');

const unknown = packages.filter((p) => !p.license || /unknown/i.test(p.license));
if (unknown.length) {
  throw new Error(`no licence declared: ${unknown.map((p) => p.name).join(', ')}`);
}

const lines = [
  '# Third-party notices — DataQ frontend',
  '',
  'DataQ is MIT-licensed. The web bundle served by this image includes the following',
  'third-party packages under their own licences; the licence texts and NOTICE contents',
  'shipped with each package are reproduced below. Generated at build time by',
  '`frontend/scripts/third-party-notices.mjs`.',
  '',
  `${packages.length} packages.`,
  '',
  '| Package | Version | Licence | Homepage |',
  '|---|---|---|---|',
];
for (const p of packages) {
  lines.push(
    `| ${cell(p.name)} | ${cell(p.versions.join(', '))} | ${cell(p.license)} | ${cell(p.homepage)} |`,
  );
}
lines.push('', '## Licence texts', '');
for (const p of packages) {
  lines.push(`### ${p.name} ${p.versions.join(', ')} — ${p.license}`, '');
  if (p.author) lines.push(`Author: ${p.author}`, '');
  for (const [version, files] of copies(p)) {
    if (!files.length) {
      lines.push(
        `No licence text is shipped in ${p.name}@${version}; see ${p.homepage || "the project's repository"}.`,
        '',
      );
    }
    for (const [name, text] of files) {
      lines.push(`#### ${name} (${version})`, '', '```text', text.replace(/\n+$/, ''), '```', '');
    }
  }
}
writeFileSync(out, lines.join('\n') + '\n');
console.log(`wrote ${out} (${packages.length} packages)`);

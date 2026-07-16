#!/usr/bin/env node
/**
 * Dependency CVE audit over the npm BULK advisory endpoint (#877).
 *
 * npm retired the legacy audit endpoints on 2026-07-15 (HTTP 410), which broke
 * `pnpm audit` — and no *released* pnpm speaks the bulk endpoint yet (verified
 * against pnpm 11.13.1; pnpm/pnpm#11265). This script keeps the same gate over
 * the same advisory database and the same LOCKED dependency graph:
 *
 *   1. Parse every `name@version` out of pnpm-lock.yaml's `packages:` section
 *      (the resolved graph — no install needed).
 *   2. POST { name: [versions] } to /-/npm/v1/security/advisories/bulk.
 *   3. Match each advisory's `vulnerable_versions` range against our resolved
 *      versions with semver, exactly as `npm audit` does client-side.
 *   4. Exit 1 when anything at or above the threshold (default high) matches.
 *
 * Delete this and revert CI to `pnpm audit --audit-level=high` once a pnpm
 * release queries the bulk endpoint (tracked in #877).
 *
 * `semver` is installed next to this script by the CI step (npm --prefix
 * scripts/) so the pnpm-managed node_modules is never touched.
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const semver = require('semver');

const BULK_URL = 'https://registry.npmjs.org/-/npm/v1/security/advisories/bulk';
const SEVERITY_RANK = { info: 0, low: 1, moderate: 2, high: 3, critical: 4 };

function parseArgs(argv) {
  const levelArg = argv.find((a) => a.startsWith('--audit-level='));
  const level = levelArg ? levelArg.split('=')[1] : 'high';
  if (!(level in SEVERITY_RANK)) {
    console.error(`unknown --audit-level=${level}`);
    process.exit(2);
  }
  return { level };
}

/**
 * Extract {name: Set(versions)} from pnpm-lock.yaml (lockfile v9).
 *
 * Keys under `packages:` look like `  name@1.2.3:`, `  '@scope/name@1.2.3':`,
 * with an optional `(peer@x)` suffix on snapshot-style keys. Tolerant,
 * line-based parse — the lockfile is machine-written, and anything that
 * doesn't look like a package key is skipped.
 */
function packagesFromLockfile(lockfilePath) {
  const text = fs.readFileSync(lockfilePath, 'utf8');
  const lines = text.split('\n');
  const packages = new Map();
  let inPackages = false;
  let keyCount = 0; // every 2-space-indented key in the section, parseable or not
  for (const line of lines) {
    if (/^packages:\s*$/.test(line)) {
      inPackages = true;
      continue;
    }
    if (inPackages && /^\S/.test(line)) inPackages = false; // next top-level section
    if (!inPackages) continue;
    if (/^ {2}\S.*:\s*$/.test(line)) keyCount += 1;
    const match = line.match(/^ {2}'?((?:@[^/'@]+\/)?[^/'@]+)@([^'():]+)(?:\([^)]*\))?'?:\s*$/);
    if (!match) continue;
    const [, name, version] = match;
    if (!semver.valid(version)) continue; // links, urls, odd specifiers — not registry packages
    if (!packages.has(name)) packages.set(name, new Set());
    packages.get(name).add(version);
  }
  return { packages, keyCount };
}

async function fetchAdvisories(packages) {
  const body = {};
  for (const [name, versions] of packages) body[name] = [...versions];
  const res = await fetch(BULK_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    console.error(`bulk advisory endpoint responded ${res.status}: ${await res.text()}`);
    process.exit(2); // endpoint failure = audit did not run; never a silent pass
  }
  return res.json();
}

async function main() {
  const { level } = parseArgs(process.argv.slice(2));
  const lockfileArg = process.argv.find((a) => a.startsWith('--lockfile='));
  const lockfile = lockfileArg
    ? path.resolve(lockfileArg.split('=')[1])
    : path.resolve(__dirname, '..', 'pnpm-lock.yaml');
  const { packages, keyCount } = packagesFromLockfile(lockfile);
  const parsedCount = [...packages.values()].reduce((n, s) => n + s.size, 0);
  if (packages.size === 0 || parsedCount !== keyCount) {
    // A lockfile-format change that breaks the regex for SOME keys must never
    // shrink the audited graph silently — parsed entries must account for every
    // key in the packages: section.
    console.error(
      `parsed ${parsedCount} of ${keyCount} package keys from ${lockfile} — ` +
        'refusing to audit a truncated graph (lockfile format drift?)',
    );
    process.exit(2);
  }

  const advisories = await fetchAdvisories(packages);
  const failing = [];
  let reported = 0;
  for (const [name, entries] of Object.entries(advisories)) {
    const ourVersions = [...(packages.get(name) ?? [])];
    for (const advisory of entries) {
      // Fail-closed on contract drift: semver.satisfies returns FALSE (never
      // throws) for a range it can't parse, which would silently skip the
      // advisory — the same class of quiet breakage that killed `pnpm audit`.
      const range = advisory.vulnerable_versions ?? '*';
      if (semver.validRange(range) === null) {
        console.error(
          `unparseable vulnerable_versions ${JSON.stringify(range)} on ${name} ` +
            `advisory ${advisory.url ?? advisory.id} — refusing to skip it`,
        );
        process.exit(2);
      }
      const hit = ourVersions.filter((v) =>
        semver.satisfies(v, range, { includePrerelease: true }),
      );
      if (hit.length === 0) continue;
      reported += 1;
      const severity = String(advisory.severity ?? '').toLowerCase();
      const line = `${severity.toUpperCase().padEnd(8)} ${name}@${hit.join(',')} — ${advisory.title} (${advisory.url})`;
      console.log(line);
      if ((SEVERITY_RANK[severity] ?? SEVERITY_RANK.critical) >= SEVERITY_RANK[level]) {
        failing.push(line);
      }
    }
  }

  console.log(
    `\naudited ${packages.size} packages from pnpm-lock.yaml — ` +
      `${reported} advisories matched, ${failing.length} at ${level}+`,
  );
  if (failing.length > 0) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(2); // any unexpected failure = the audit did not run; fail closed
});

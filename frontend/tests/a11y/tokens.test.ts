import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// axe cannot judge these: fills live in SVG and <Progress> strokes, and it silently skips some
// inline-coloured text. So the token VALUES are held to the WCAG ratios directly.
const css = readFileSync(resolve(__dirname, '../../src/styles.css'), 'utf8');

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no ${selector} block in styles.css`);
  const body = css.slice(start, css.indexOf('\n}', start));
  return Object.fromEntries(
    [...body.matchAll(/(--dq-[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1], m[2]]),
  );
}

const light = block(':root');
const dark = { ...light, ...block(":root[data-theme='dark']") };

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const FILLS = ['good', 'warning', 'bad', 'neutral'].map((k) => `--dq-severity-${k}`);
const GRAPHICS = [...FILLS, '--dq-critical', '--dq-error'];
const TEXT = [...FILLS.map((k) => `${k}-text`), '--dq-muted'];

describe.each([
  ['light', light],
  ['dark', dark],
])('%s theme tokens', (_name, vars) => {
  const backgrounds = ['--dq-surface', '--dq-canvas'];

  it.each(GRAPHICS)('%s is 3:1 against every background (WCAG 1.4.11)', (token) => {
    for (const bg of backgrounds) {
      expect(contrast(vars[token], vars[bg]), `${token} on ${bg}`).toBeGreaterThanOrEqual(3);
    }
  });

  it.each(TEXT)('%s is 4.5:1 against every background (WCAG 1.4.3)', (token) => {
    for (const bg of backgrounds) {
      expect(contrast(vars[token], vars[bg]), `${token} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    }
  });
});

// Every declaration in a block, not just the hex ones: the shadows and tints matter on paper too.
function declarations(body: string): Record<string, string> {
  return Object.fromEntries(
    [...body.matchAll(/(--dq-[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
  );
}

function blockBody(from: number, selector: string): string {
  const start = css.indexOf(`${selector} {`, from);
  if (start < 0) throw new Error(`no ${selector} block in styles.css`);
  return css.slice(start, css.indexOf('}', start));
}

describe('printing from the dark theme', () => {
  // Paper is white: browsers drop page backgrounds, so dark-theme ink would print faint on it.
  const printAt = css.indexOf('@media print {');
  const lightAll = declarations(blockBody(0, ':root'));
  const darkAll = declarations(blockBody(0, ":root[data-theme='dark']"));
  const printDark = declarations(blockBody(printAt, ":root[data-theme='dark']"));

  it('puts back every token the dark theme overrides', () => {
    expect(printAt).toBeGreaterThan(0);
    expect(Object.keys(printDark).sort()).toEqual(Object.keys(darkAll).sort());
  });

  it('paints the report with the token ink, not the antd text colour it would inherit', () => {
    expect(css.slice(printAt)).toMatch(/\.rd-report \{\s*color: var\(--dq-ink\);/);
  });

  it('to its light value', () => {
    for (const [token, value] of Object.entries(printDark)) {
      expect(value, token).toBe(lightAll[token]);
    }
  });
});

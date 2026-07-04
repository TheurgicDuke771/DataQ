import { describe, expect, it } from 'vitest';

import {
  activeAuthOption,
  composeSecret,
} from '../../src/components/connections/connectionFormSpec';

describe('composeSecret', () => {
  it('wraps the secret and passphrase into the combined JSON payload', () => {
    expect(composeSecret('PEM', 'pp')).toBe(
      JSON.stringify({ private_key: 'PEM', passphrase: 'pp' }),
    );
  });

  it('returns the bare secret when the passphrase is missing or empty', () => {
    expect(composeSecret('PEM')).toBe('PEM');
    expect(composeSecret('PEM', '')).toBe('PEM');
  });

  it('treats a whitespace-only passphrase as blank (stray keystroke, not a passphrase)', () => {
    expect(composeSecret('PEM', '  ')).toBe('PEM');
  });
});

describe('activeAuthOption', () => {
  it('resolves the configured auth mode', () => {
    expect(activeAuthOption('snowflake', { auth_type: 'key_pair' })?.value).toBe('key_pair');
  });

  it('falls back to the default (first) mode when config carries no auth_type', () => {
    expect(activeAuthOption('snowflake', {})?.value).toBe('password');
    expect(activeAuthOption('snowflake', undefined)?.value).toBe('password');
  });

  it('is undefined for single-secret types', () => {
    expect(activeAuthOption('s3', {})).toBeUndefined();
  });
});

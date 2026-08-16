import { describe, expect, it } from 'vitest';

import {
  activeAuthOption,
  CONNECTION_FORM_SPECS,
  composeSecret,
  initialConfigForType,
  movedDestinationFields,
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

describe('initialConfigForType', () => {
  it("seeds Iceberg's default catalog_name (#1181)", () => {
    expect(initialConfigForType('iceberg')).toEqual({ catalog_name: 'default' });
  });

  it('still seeds the default auth_type for a type that has one', () => {
    expect(initialConfigForType('snowflake')).toEqual({ auth_type: 'password' });
  });

  it('is empty for a type with neither auth modes nor defaults', () => {
    expect(initialConfigForType('s3')).toEqual({});
  });
});

describe("Iceberg's second (catalog) credential — #1181", () => {
  const spec = CONNECTION_FORM_SPECS.iceberg;

  it('declares a propertiesField and a secondSecret, unlike every other type', () => {
    expect(spec.propertiesField).toBeDefined();
    expect(spec.secondSecret).toBeDefined();
    for (const type of [
      'snowflake',
      'adls_gen2',
      's3',
      'unity_catalog',
      'adf',
      'airflow',
      'dbt',
    ] as const) {
      expect(CONNECTION_FORM_SPECS[type].propertiesField).toBeUndefined();
      expect(CONNECTION_FORM_SPECS[type].secondSecret).toBeUndefined();
    }
  });

  it('shows the catalog credential only for a sql or hive catalog_type', () => {
    if (!spec.secondSecret) throw new Error('iceberg must declare secondSecret');
    const { showWhen } = spec.secondSecret;
    expect(showWhen({ catalog_type: 'sql' })).toBe(true);
    expect(showWhen({ catalog_type: 'hive' })).toBe(true);
    expect(showWhen({ catalog_type: 'rest' })).toBe(false);
    expect(showWhen({ catalog_type: 'glue' })).toBe(false);
    expect(showWhen(undefined)).toBe(false);
    expect(showWhen({})).toBe(false);
  });
});

describe('movedDestinationFields (#1401)', () => {
  const stored = { account: 'ab12345.eu-west-1', warehouse: 'WH' };

  it('treats an unwatched config as nothing moved, not everything moved', () => {
    // The edit form seeds itself in a useEffect, so `Form.useWatch` returns
    // undefined for the first render. Reading that as "every field changed"
    // flashed the "Re-enter the credential" alert and a required credential
    // input on every edit open, asserting something untrue of the current state.
    // Invisible to an RTL test, which only observes the settled post-effect DOM.
    expect(movedDestinationFields('snowflake', undefined, stored)).toEqual([]);
  });

  it('reports a moved destination field', () => {
    expect(movedDestinationFields('snowflake', { ...stored, account: 'zz9.us' }, stored)).toEqual([
      'account',
    ]);
  });

  it('ignores a non-destination field', () => {
    expect(movedDestinationFields('snowflake', { ...stored, warehouse: 'XL' }, stored)).toEqual([]);
  });

  it('treats a cleared optional destination as a move', () => {
    // Distinct from `undefined`-the-whole-config above: the config IS known here
    // and the field was emptied, which does move an S3 connection off its
    // custom endpoint back to AWS.
    expect(movedDestinationFields('s3', {}, { endpoint_url: 'https://minio.example.com' })).toEqual(
      ['endpoint_url'],
    );
  });

  it('compares object-valued destination fields by value', () => {
    const props = { 's3.region': 'us-east-1' };
    expect(
      movedDestinationFields('iceberg', { properties: { ...props } }, { properties: props }),
    ).toEqual([]);
    expect(
      movedDestinationFields(
        'iceberg',
        { properties: { 's3.endpoint': 'https://attacker' } },
        { properties: props },
      ),
    ).toEqual(['properties']);
  });
});

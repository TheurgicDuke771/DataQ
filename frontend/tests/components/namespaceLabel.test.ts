import { describe, expect, it } from 'vitest';

import { datasourceKind, namespaceLabel } from '../../src/components/assets/namespaceLabel';

// The human label for an OL namespace (#830). The namespace stays the identity;
// this is only what we print. Two load-bearing properties:
//   1. it NEVER renders blank for any namespace — an unknown one degrades to
//      "ugly but true", never to nothing;
//   2. it never prints a credential-adjacent identifier (the backend's
//      `strip_uri_credentials` deliberately KEEPS `username@` when it strips the
//      password, so namespaces genuinely arrive carrying a DB username).
describe('namespaceLabel', () => {
  describe('recognised datasources', () => {
    it('names a Snowflake account, preserving its case', () => {
      // Account identifiers are case-significant to a reader — this is also why the
      // parser can't be `new URL()`, whose `.host` lowercases.
      expect(namespaceLabel('snowflake://PVQSOEQ-ZGB34383')).toBe('Snowflake · PVQSOEQ-ZGB34383');
    });

    it('shortens a Databricks workspace host to the workspace id', () => {
      expect(namespaceLabel('unitycatalog://dbc-4492dde4-090c.cloud.databricks.com')).toBe(
        'Databricks · dbc-4492dde4-090c',
      );
    });

    it('keeps a Databricks host with no dotted suffix to drop', () => {
      expect(namespaceLabel('unitycatalog://uc-local')).toBe('Databricks · uc-local');
    });

    it('reads an ADLS namespace as account/container', () => {
      expect(namespaceLabel('abfss://raw@dataqharness.dfs.core.windows.net')).toBe(
        'ADLS · dataqharness/raw',
      );
    });

    it('names an S3 bucket', () => {
      expect(namespaceLabel('s3://dataq-landing')).toBe('S3 · dataq-landing');
    });
  });

  describe('catalog URIs (Iceberg — any scheme, so the type is unprovable)', () => {
    it('reduces a driver DSN to the catalog database', () => {
      const dsn = 'postgresql+psycopg2://someuser@some-host:5432/iceberg_catalog?sslmode=require';
      expect(namespaceLabel(dsn)).toBe('iceberg_catalog');
    });

    it('never prints the userinfo, even when there is no database path to fall back to', () => {
      // The bug the review caught: with no path, the label fell back to the raw
      // authority — which still carried `someuser@`. The backend keeps the username
      // when it strips the password, so this shape is real, not hypothetical.
      const label = namespaceLabel('postgresql+psycopg2://someuser@some-host:5432');
      expect(label).toBe('some-host:5432');
      expect(label).not.toContain('someuser');
      expect(label).not.toContain('@');
    });

    it('never prints the userinfo when the path is empty (trailing slash)', () => {
      expect(namespaceLabel('postgresql+psycopg2://someuser@some-host:5432/')).toBe(
        'some-host:5432',
      );
    });

    it('names a REST catalog by its host, not by its API route', () => {
      // `https://host/v1` — the path is a route, not a name. Labelling by the last
      // path segment would print a meaningless `v1`, and two different REST catalogs
      // would read IDENTICALLY. For http/https/thrift/grpc the host IS the catalog.
      expect(namespaceLabel('https://rest-catalog.example.com/v1')).toBe(
        'rest-catalog.example.com',
      );
      expect(namespaceLabel('https://other-catalog.example.com/v1')).toBe(
        'other-catalog.example.com',
      );
    });

    it('distinguishes two REST catalogs that share an API route', () => {
      expect(namespaceLabel('https://a.example.com/v1')).not.toBe(
        namespaceLabel('https://b.example.com/v1'),
      );
    });

    it('names a thrift catalog by its host', () => {
      expect(namespaceLabel('thrift://hive:9083')).toBe('hive:9083');
    });

    it('names the local (file) catalog', () => {
      expect(namespaceLabel('file')).toBe('Local catalog');
    });

    it('claims no source for a URI namespace whose type it cannot prove', () => {
      // An Iceberg namespace is a bare catalog URI of ANY scheme, so a URI alone
      // proves nothing. Guessing "Iceberg" would mislabel the first datasource that
      // ships a URI namespace — and would contradict `datasourceKind`, which answers
      // `other` here for the same reason. Shorten it; don't name it.
      expect(namespaceLabel('kafka://broker:9092')).toBe('broker:9092');
      expect(datasourceKind('kafka://broker:9092')).toBe('other');
    });
  });

  describe('hostile / degenerate input', () => {
    it.each([
      ['whitespace only', '   '],
      ['a bare scheme', 's3://'],
      ['a malformed snowflake', 'snowflake://'],
      ['an ADLS with no account', 'abfss://raw@'],
      ['an ADLS with no container', 'abfss://@account.dfs.core.windows.net'],
      ['a scheme with nothing after it', 'x://'],
      ['an opaque token', 'some-opaque-namespace'],
      ['a lone separator', '://'],
      ['a leading dot host', 'unitycatalog://.example.com'],
    ])('never renders blank for %s', (_label, ns) => {
      // The one degradation the module forbids: a blank label leaves a tree root as a
      // bare icon and an asset-detail subtitle empty. Ugly-but-true always wins.
      expect(namespaceLabel(ns).length).toBeGreaterThan(0);
    });

    it('returns the raw namespace when it is only whitespace', () => {
      // `trim()` runs before the emptiness guard, so the guard must return the
      // ORIGINAL string, not the trimmed one.
      expect(namespaceLabel('   ')).toBe('   ');
    });

    it('falls back to the raw namespace rather than a dangling separator', () => {
      // "Snowflake · " is worse than useless.
      expect(namespaceLabel('snowflake://')).toBe('snowflake://');
      expect(namespaceLabel('s3://')).toBe('s3://');
    });

    it('matches schemes case-insensitively', () => {
      // Nothing emits these today, but a scheme table that only matches lowercase
      // silently drops to the generic branch — label and icon disagreeing.
      expect(namespaceLabel('SNOWFLAKE://ACCT')).toBe('Snowflake · ACCT');
      expect(datasourceKind('SNOWFLAKE://ACCT')).toBe('snowflake');
    });
  });
});

// `datasourceKind` and `namespaceLabel` are driven by ONE scheme table precisely so
// they cannot drift (they used to be two hand-synced prefix lists — add `gs://` to
// one and you get an `S3 ·` label under an `other` icon).
describe('datasourceKind', () => {
  it.each([
    ['snowflake://acct', 'snowflake'],
    ['unitycatalog://h.example.com', 'unity_catalog'],
    ['abfss://c@a.dfs.core.windows.net', 'adls_gen2'],
    ['s3://bucket', 's3'],
    ['postgresql://u@h/db', 'other'],
    ['file', 'other'],
    ['nonsense', 'other'],
  ])('%s → %s', (ns, kind) => {
    expect(datasourceKind(ns)).toBe(kind);
  });

  it('agrees with namespaceLabel about which schemes are named', () => {
    // A kind other than `other` must always come with a `Source · instance` label,
    // and vice versa — the drift this shared table exists to prevent.
    for (const ns of [
      'snowflake://acct',
      'unitycatalog://h.example.com',
      'abfss://c@a.dfs.core.windows.net',
      's3://bucket',
    ]) {
      expect(datasourceKind(ns)).not.toBe('other');
      expect(namespaceLabel(ns)).toContain(' · ');
    }
  });
});

import { describe, expect, it } from 'vitest';

import { namespaceLabel } from '../../src/components/assets/namespaceLabel';

// The human label for an OL namespace (#830). The namespace stays the identity;
// this is only what we print. The load-bearing property is that it NEVER renders
// blank — an unrecognised namespace must degrade to "ugly but true".
describe('namespaceLabel', () => {
  it('names a Snowflake account', () => {
    expect(namespaceLabel('snowflake://PVQSOEQ-ZGB34383')).toEqual({
      source: 'Snowflake',
      instance: 'PVQSOEQ-ZGB34383',
      text: 'Snowflake · PVQSOEQ-ZGB34383',
    });
  });

  it('shortens a Databricks workspace host to the workspace id', () => {
    expect(namespaceLabel('unitycatalog://dbc-4492dde4-090c.cloud.databricks.com').text).toBe(
      'Databricks · dbc-4492dde4-090c',
    );
  });

  it('keeps a Databricks host that is not a <workspace>.<domain> shape', () => {
    // Self-hosted / bare host: there is no dotted suffix to drop, so don't invent one.
    expect(namespaceLabel('unitycatalog://uc-local').text).toBe('Databricks · uc-local');
  });

  it('reads an ADLS namespace as account/container', () => {
    expect(namespaceLabel('abfss://raw@dataqharness.dfs.core.windows.net').text).toBe(
      'ADLS · dataqharness/raw',
    );
  });

  it('names an S3 bucket', () => {
    expect(namespaceLabel('s3://dataq-landing').text).toBe('S3 · dataq-landing');
  });

  it('reduces a SQL-catalog DSN to the catalog database', () => {
    // The whole point of #830: driver, user, host, port and query string are noise
    // (and the host/username are infra detail we would rather not print at a glance).
    const dsn = 'postgresql+psycopg2://someuser@some-host:5432/iceberg_catalog?sslmode=require';
    const label = namespaceLabel(dsn);
    expect(label.text).toBe('iceberg_catalog');
    expect(label.text).not.toContain('someuser');
    expect(label.text).not.toContain('some-host');
    expect(label.text).not.toContain('sslmode');
  });

  it('names a catalog-less URI by its host', () => {
    // thrift/REST catalogs have no database path — the host IS the catalog.
    expect(namespaceLabel('thrift://hive:9083').text).toBe('hive:9083');
  });

  it('names the local (file) catalog', () => {
    expect(namespaceLabel('file').text).toBe('Local catalog');
  });

  it('claims no source for a URI namespace it cannot prove the type of', () => {
    // An Iceberg namespace is a bare catalog URI of ANY scheme, so a URI alone
    // proves nothing. Guessing "Iceberg" would mislabel the first datasource that
    // ships a URI namespace — and would contradict `datasourceKind`, which answers
    // `other` here for the same reason. Shorten it; don't name it.
    expect(namespaceLabel('kafka://broker:9092')).toEqual({
      source: '',
      instance: 'broker:9092',
      text: 'broker:9092',
    });
  });

  it('falls back to the raw namespace when it recognises nothing', () => {
    // A future/unknown datasource must still be identifiable, not blank.
    expect(namespaceLabel('some-opaque-namespace').text).toBe('some-opaque-namespace');
  });

  it('falls back to the raw namespace rather than rendering a dangling separator', () => {
    // A malformed `snowflake://` has no instance — "Snowflake · " would be worse
    // than useless, so the raw string wins.
    expect(namespaceLabel('snowflake://').text).toBe('snowflake://');
  });

  it('never returns an empty label for a non-empty namespace', () => {
    for (const ns of [
      'snowflake://A',
      'unitycatalog://h.example.com',
      'abfss://c@a.dfs.core.windows.net',
      's3://b',
      'file',
      'postgresql://u@h/db',
      'weird',
    ]) {
      expect(namespaceLabel(ns).text.length).toBeGreaterThan(0);
    }
  });
});

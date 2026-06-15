import { describe, expect, it } from 'vitest';

import {
  CUSTOM_SQL_EXPECTATION_TYPE,
  isCustomSql,
  validateCustomSqlQuery,
} from '../../src/components/checks/customSql';

/**
 * Client-side mirror of the backend custom-SQL guardrail (ADR 0019). The backend
 * `validate_query` is authoritative; this is the inline editor pre-check. Mirrors
 * the backend battery's hostile cases so the two don't drift on what they reject.
 */

const VALID = [
  'SELECT * FROM {batch} WHERE amount IS NULL',
  'select count(*) from {batch}',
  'WITH t AS (SELECT * FROM {batch}) SELECT * FROM t WHERE n > 0',
  'SELECT 1 FROM {batch};', // single trailing semicolon is fine
  '(SELECT * FROM {batch}) UNION (SELECT * FROM {batch})',
  "SELECT replace(name, 'a', 'b') AS r FROM {batch} WHERE action <> 'delete'",
  "SELECT * FROM {batch} WHERE note = 'a;b'", // ';' inside a string
  'SELECT * FROM {batch} -- drop table evil\nWHERE 1 = 1', // keyword in a comment
];

const INVALID = [
  '',
  '   ',
  '-- just a comment',
  'DELETE FROM {batch}',
  'UPDATE {batch} SET x = 1',
  'DROP TABLE secrets',
  'INSERT INTO t VALUES (1)',
  'SELECT * INTO new_table FROM {batch}',
  'SELECT 1 FROM a; SELECT 2 FROM b', // two statements
  'SELECT 1 FROM {batch}; DROP TABLE x',
  'WITH t AS (INSERT INTO x VALUES (1) RETURNING *) SELECT * FROM t', // CTE DML
  "SELECT 1 FROM {batch} WHERE x = 'a--'; DROP TABLE y", // '--' in a string masks nothing
  'SELECT 1 FROM {batch} /* unterminated ; DROP TABLE y', // unterminated comment → fail closed
  "SELECT 1 FROM {batch} WHERE n = 'unterminated ; DROP", // unterminated string → fail closed
  'SELECT 1 FROM {batch} WHERE x = 1 `; DROP TABLE y; SELECT *`', // backtick is not a quote
];

describe('validateCustomSqlQuery', () => {
  it.each(VALID)('accepts %j', (q) => {
    expect(validateCustomSqlQuery(q)).toBeNull();
  });

  it.each(INVALID)('rejects %j', (q) => {
    expect(validateCustomSqlQuery(q)).not.toBeNull();
  });

  it('reports the offending keyword in the message', () => {
    expect(validateCustomSqlQuery('SELECT 1 FROM {batch} WHERE drop = 1')).toContain('drop');
  });

  it('handles a huge trailing-whitespace query without hanging', () => {
    // Guards against a polynomial-ReDoS in the trailing strip (the backend hit
    // this — str-based / anchored linear regex, not catastrophic backtracking).
    expect(validateCustomSqlQuery('SELECT 1 FROM {batch}' + '\t'.repeat(50_000))).toBeNull();
  });
});

describe('isCustomSql', () => {
  it('matches only the custom-SQL expectation type', () => {
    expect(isCustomSql(CUSTOM_SQL_EXPECTATION_TYPE)).toBe(true);
    expect(isCustomSql('unexpected_rows_expectation')).toBe(true);
    expect(isCustomSql('expect_column_values_to_not_be_null')).toBe(false);
    expect(isCustomSql(undefined)).toBe(false);
  });
});

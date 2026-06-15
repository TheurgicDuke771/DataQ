/**
 * Client-side mirror of the backend custom-SQL guardrail (ADR 0019) for inline
 * editor feedback. The backend `custom_sql.validate_query` is the authoritative
 * boundary (and runs with extra="forbid" + a least-privilege role); this is a
 * lighter, best-effort pre-check so the editor flags an obviously non-read-only
 * or multi-statement query before the save round-trips to a 422.
 *
 * Kept pure (no React) so it's unit- and mutation-testable in isolation.
 */

/** The GX expectation a custom-SQL check maps to (ADR 0019). */
export const CUSTOM_SQL_EXPECTATION_TYPE = 'unexpected_rows_expectation';
/** The GX kwarg / config key holding the user's query. */
export const CUSTOM_SQL_QUERY_KEY = 'unexpected_rows_query';

export const isCustomSql = (expectationType: string | undefined): boolean =>
  expectationType === CUSTOM_SQL_EXPECTATION_TYPE;

// State-mutating / proc-invoking statement keywords — a read-only check query
// must contain none of them (as a bareword, after strings/comments are stripped).
// `comment` / `replace` are omitted (common column name / `replace()` function),
// matching the backend set.
const FORBIDDEN_KEYWORDS = new Set([
  'insert',
  'update',
  'delete',
  'merge',
  'upsert',
  'truncate',
  'drop',
  'alter',
  'create',
  'grant',
  'revoke',
  'commit',
  'rollback',
  'into',
  'call',
  'exec',
  'execute',
  'do',
  'copy',
  'lock',
  'set',
  'reset',
  'discard',
  'prepare',
  'deallocate',
  'vacuum',
  'analyze',
  'use',
  'attach',
  'detach',
  'unload',
]);

/**
 * Replace comments and string literals with spaces in a single left-to-right pass
 * so neither can mask the other. Backtick is intentionally NOT a quote (Snowflake
 * / Unity Catalog don't delimit strings with it). Returns `null` if a string or
 * block comment is left unterminated — the caller fails closed.
 */
function stripNonCode(sql: string): string | null {
  let out = '';
  let i = 0;
  const n = sql.length;
  let wellFormed = true;
  while (i < n) {
    const pair = sql.slice(i, i + 2);
    if (pair === '--') {
      const nl = sql.indexOf('\n', i);
      i = nl === -1 ? n : nl;
      out += ' ';
    } else if (pair === '/*') {
      const end = sql.indexOf('*/', i + 2);
      if (end === -1) {
        wellFormed = false;
        i = n;
      } else {
        i = end + 2;
      }
      out += ' ';
    } else if (sql[i] === "'" || sql[i] === '"') {
      const quote = sql[i];
      i += 1;
      let closed = false;
      while (i < n) {
        if (sql[i] === quote) {
          if (sql[i + 1] === quote) {
            i += 2; // doubled quote = escaped, stay in the string
            continue;
          }
          i += 1;
          closed = true;
          break;
        }
        i += 1;
      }
      if (!closed) wellFormed = false;
      out += ' ';
    } else {
      out += sql[i];
      i += 1;
    }
  }
  return wellFormed ? out : null;
}

const TRAILING = /[\s;]+$/;
const LEADING_KEYWORD = /^[\s(]*([a-zA-Z]+)/;
const WORD = /[a-zA-Z_]+/g;

/**
 * Best-effort read-only / single-statement check. Returns an error message, or
 * `null` if the query looks acceptable. The backend is authoritative.
 */
export function validateCustomSqlQuery(query: string | undefined): string | null {
  if (!query || !query.trim()) return 'Enter a SQL query.';

  const code = stripNonCode(query);
  if (code === null) return 'Unterminated string literal or comment.';

  const analysis = code.trim().replace(TRAILING, '');
  if (!analysis) return 'Query is empty after removing comments.';
  if (analysis.includes(';')) return 'Use a single statement (no “;”-chained statements).';

  const first = LEADING_KEYWORD.exec(analysis)?.[1]?.toLowerCase();
  if (first !== 'select' && first !== 'with') {
    return 'Must be a read-only SELECT / WITH query.';
  }

  const words = analysis.toLowerCase().match(WORD) ?? [];
  const forbidden = words.find((w) => FORBIDDEN_KEYWORDS.has(w));
  if (forbidden) return `Must be read-only; remove “${forbidden}”.`;

  return null;
}

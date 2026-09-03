// ESLint flat config (eslint.config.js equivalent in CJS for compat with older tooling)
const js = require('@eslint/js');
const globals = require('globals');
const reactHooks = require('eslint-plugin-react-hooks');
// 0.5.x ships as an ESM module; the CJS require returns an interop wrapper, so
// reach the plugin (with .rules/.configs) via .default.
const reactRefresh = require('eslint-plugin-react-refresh').default;
const tseslint = require('typescript-eslint');
const prettierConfig = require('eslint-config-prettier');

module.exports = tseslint.config(
  { ignores: ['dist', 'coverage', 'node_modules', 'playwright-report', 'test-results'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.strict, prettierConfig],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      // Type-aware linting (needed for no-deprecated below, #1310): antd's own
      // .d.ts files mark retired props with @deprecated JSDoc, and this rule
      // reads that through the type checker — a plain syntax rule can't see it.
      parserOptions: {
        projectService: true,
        tsconfigRootDir: __dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      '@typescript-eslint/explicit-function-return-type': 'off', // too noisy for React components
      '@typescript-eslint/no-explicit-any': 'error',
      // #1310: PR #516 (2026-07-01) swept every deprecated antd v6 prop once,
      // and #1275/#1308 (2026-08-12) found the same pattern reintroduced —
      // nothing was catching it going forward. This flags any @deprecated
      // symbol (antd props included) at lint time instead of a manual QA pass.
      '@typescript-eslint/no-deprecated': 'error',
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', '**/*.spec.{ts,tsx}'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off', // relax in tests
    },
  },
  {
    // Playwright config + E2E specs run under Node (process.env, etc.), not the
    // browser, so give them Node globals.
    files: [
      'playwright.config.ts',
      'e2e/**/*.ts',
      'e2e-otp/**/*.ts',
      'e2e-live/**/*.ts',
      'e2e-docs/**/*.ts',
    ],
    languageOptions: {
      globals: globals.node,
    },
    rules: {
      // Playwright's fixture callbacks take a parameter conventionally named
      // `use`, and calling it is how a fixture hands its value to the test. The
      // react-hooks plugin sees `use(...)` and thinks it is React 19's `use()`
      // hook. There is no React in these files at all.
      'react-hooks/rules-of-hooks': 'off',
    },
  },
  {
    // e2e-otp/ and e2e-live/ aren't in tsconfig.json's `include` (only e2e/
    // is), so the type-aware parserOptions.projectService set above can't
    // resolve them into a TS project. Fall back to syntax-only parsing and
    // drop the type-aware rule for just these two dirs, rather than erroring
    // on every file in both.
    files: ['e2e-otp/**/*.ts', 'e2e-live/**/*.ts', 'e2e-docs/**/*.ts', 'scripts/**/*.mts'],
    languageOptions: {
      parserOptions: {
        projectService: false,
        project: false,
      },
    },
    rules: {
      '@typescript-eslint/no-deprecated': 'off',
    },
  },
);

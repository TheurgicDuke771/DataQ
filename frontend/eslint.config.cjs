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
    files: ['playwright.config.ts', 'e2e/**/*.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },
);

import Editor, { loader } from '@monaco-editor/react';
// The EDITOR API only — deliberately NOT the `monaco-editor` barrel (#976).
//
// That barrel is `esm/vs/index.js`, which imports a `register.js` for every
// language Monaco ships. Each language service Vite finds becomes its own worker
// chunk, so a build emitted five — ts (6.9 MB), css (1.1 MB), html (740 kB), json
// (430 kB) — of which `getWorker` below only ever returns the base editor one.
// ~9 MB emitted and never requested, on a component whose own docstring said only
// the base worker was needed. The intent was right; the import contradicted it.
import * as monaco from 'monaco-editor/editor/editor.api.js';
// SQL highlighting, registered on its own. The registration is lazy (`loader: ()
// => import('./sql.js')`), so the grammar is a small async chunk and no language
// SERVICE — hence no worker — comes with it.
import 'monaco-editor/languages/definitions/sql/register.js';
// monaco-editor 0.56 added an `exports` map ("./*.js": "./esm/vs/*.js"), so the
// `esm/vs/` prefix is now implicit and the old deep path no longer resolves.
import editorWorker from 'monaco-editor/editor/editor.worker.js?worker';

/**
 * Monaco SQL editor as an antd-Form-compatible controlled field (custom-SQL
 * checks, ADR 0019). Default-exported and consumed via `React.lazy`, so Monaco
 * lands in its own chunk loaded only when a custom-SQL check is authored.
 *
 * Monaco is bundled locally (not fetched from a CDN) so the app stays
 * self-contained and CSP-friendly. SQL is a basic (highlight-only) language, so
 * only the base editor worker is needed — no language-service worker. The imports
 * above are what make that true rather than merely intended (#976).
 */
self.MonacoEnvironment = { getWorker: () => new editorWorker() };
loader.config({ monaco });

export default function SqlEditorField({
  value,
  onChange,
}: {
  value?: string;
  onChange?: (value: string) => void;
}) {
  return (
    <div style={{ border: '1px solid #d9d9d9', borderRadius: 6, overflow: 'hidden' }}>
      <Editor
        height={180}
        defaultLanguage="sql"
        value={value ?? ''}
        onChange={(next) => onChange?.(next ?? '')}
        options={{
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 13,
          lineNumbers: 'on',
          automaticLayout: true,
          wordWrap: 'on',
        }}
      />
    </div>
  );
}

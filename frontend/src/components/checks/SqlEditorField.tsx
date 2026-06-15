import Editor, { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';

/**
 * Monaco SQL editor as an antd-Form-compatible controlled field (custom-SQL
 * checks, ADR 0019). Default-exported and consumed via `React.lazy`, so Monaco
 * lands in its own chunk loaded only when a custom-SQL check is authored.
 *
 * Monaco is bundled locally (not fetched from a CDN) so the app stays
 * self-contained and CSP-friendly. SQL is a basic (highlight-only) language, so
 * only the base editor worker is needed — no language-service worker.
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

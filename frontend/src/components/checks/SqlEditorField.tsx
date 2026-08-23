import Editor, { loader } from '@monaco-editor/react';
// The EDITOR API only — deliberately NOT the `monaco-editor` barrel (#976).
import * as monaco from 'monaco-editor/editor/editor.api.js';
// SQL highlighting, registered on its own.
import 'monaco-editor/languages/definitions/sql/register.js';
// monaco-editor 0.56 added an `exports` map ("./*.js": "./esm/vs/*.js"), so the
// `esm/vs/` prefix is now implicit and the old deep path no longer resolves.
import editorWorker from 'monaco-editor/editor/editor.worker.js?worker';

/** Monaco SQL editor as an antd-Form-compatible controlled field (custom-SQL checks, ADR 0019). */
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

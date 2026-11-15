import Editor, { loader } from '@monaco-editor/react';
// The EDITOR API only — deliberately NOT the `monaco-editor` barrel (#976).
import * as monaco from 'monaco-editor/editor/editor.api.js';
// SQL highlighting, registered on its own.
import 'monaco-editor/languages/definitions/sql/register.js';
// monaco-editor 0.56 added an `exports` map ("./*.js": "./esm/vs/*.js"), so the
// `esm/vs/` prefix is now implicit and the old deep path no longer resolves.
import editorWorker from 'monaco-editor/editor/editor.worker.js?worker';

import { Typography } from 'antd';
import { useId } from 'react';

import { useThemeMode } from '../../themeMode/useThemeMode';

/** Monaco SQL editor as an antd-Form-compatible controlled field (custom-SQL checks, ADR 0019).
 *
 *  Keyboard (#1672): Tab indents inside the editor, so a keyboard user would be stuck in it
 *  without knowing Monaco's toggle. The hint below says how to leave, and is tied to the
 *  editor's input, which also gets the field's name: `ariaLabel` (antd's Form.Item cannot hand
 *  its label through the lazy boundary the editor sits behind). */
self.MonacoEnvironment = { getWorker: () => new editorWorker() };
loader.config({ monaco });

export default function SqlEditorField({
  value,
  onChange,
  ariaLabel = 'SQL query',
}: {
  value?: string;
  onChange?: (value: string) => void;
  /** The field's name for assistive technology: the Form.Item label it sits under. */
  ariaLabel?: string;
}) {
  const { resolvedMode } = useThemeMode();
  const hintId = useId();
  return (
    <div>
      <div style={{ border: '1px solid var(--dq-border)', borderRadius: 6, overflow: 'hidden' }}>
        <Editor
          height={180}
          defaultLanguage="sql"
          theme={resolvedMode === 'dark' ? 'vs-dark' : 'vs'}
          value={value ?? ''}
          onChange={(next) => onChange?.(next ?? '')}
          onMount={(editor) => {
            editor
              .getDomNode()
              ?.querySelector('textarea')
              ?.setAttribute('aria-describedby', hintId);
          }}
          options={{
            ariaLabel,
            accessibilitySupport: 'auto',
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 13,
            lineNumbers: 'on',
            automaticLayout: true,
            wordWrap: 'on',
          }}
        />
      </div>
      <Typography.Text id={hintId} type="secondary" style={{ fontSize: 12 }}>
        Tab indents in the editor. To move on with Tab, press Ctrl+M first (Ctrl+Shift+M on a Mac).
      </Typography.Text>
    </div>
  );
}

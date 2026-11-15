import Editor, { loader } from '@monaco-editor/react';
import { Typography } from 'antd';
// The EDITOR API only — deliberately NOT the `monaco-editor` barrel (#976).
import * as monaco from 'monaco-editor/editor/editor.api.js';
// SQL highlighting, registered on its own.
import 'monaco-editor/languages/definitions/sql/register.js';
// Ctrl+M / Ctrl+Shift+M, "Toggle Tab Key Moves Focus". Only the full barrel registers it, and
// without it a keyboard user cannot Tab out of the editor (#1672).
import 'monaco-editor/editor/contrib/toggleTabFocusMode/browser/toggleTabFocusMode.js';
// monaco-editor 0.56 added an `exports` map ("./*.js": "./esm/vs/*.js"), so the
// `esm/vs/` prefix is now implicit and the old deep path no longer resolves.
import editorWorker from 'monaco-editor/editor/editor.worker.js?worker';
import { useEffect, useId, useState } from 'react';

import { useThemeMode } from '../../themeMode/useThemeMode';
import type { SqlEditorControlProps } from './SqlEditorControl';

/** Monaco SQL editor as an antd-Form-compatible controlled field (custom-SQL checks, ADR 0019).
 *  Rendered through `SqlEditorControl`, which a Form.Item can hand its props to.
 *
 *  Keyboard (#1672): Tab indents inside the editor, so a keyboard user would be stuck in it
 *  without knowing Monaco's toggle. The hint below says how to leave, and is tied to the
 *  element that takes focus, which also gets the field's name and the Form.Item's own help,
 *  error and required state. */
self.MonacoEnvironment = { getWorker: () => new editorWorker() };
loader.config({ monaco });

/** The element that takes focus: Chromium's native edit context, or the textarea elsewhere. Not
 *  the aria-hidden IME textarea the native mode also renders. */
const INPUT = '.native-edit-context, textarea.inputarea:not([aria-hidden="true"])';

export default function SqlEditorField({
  value,
  onChange,
  ariaLabel = 'SQL query',
  'aria-describedby': describedBy,
  'aria-invalid': invalid,
  'aria-required': required,
}: SqlEditorControlProps) {
  const { resolvedMode } = useThemeMode();
  const hintId = useId();
  const [root, setRoot] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const input = root?.querySelector(INPUT);
    if (!input) return;
    const set = (name: string, v: string | undefined) =>
      v ? input.setAttribute(name, v) : input.removeAttribute(name);
    set('aria-describedby', [hintId, describedBy].filter(Boolean).join(' '));
    set('aria-invalid', invalid === true || invalid === 'true' ? 'true' : undefined);
    set('aria-required', required === true || required === 'true' ? 'true' : undefined);
  }, [root, hintId, describedBy, invalid, required]);

  return (
    <div>
      <div style={{ border: '1px solid var(--dq-border)', borderRadius: 6, overflow: 'hidden' }}>
        <Editor
          height={180}
          defaultLanguage="sql"
          theme={resolvedMode === 'dark' ? 'vs-dark' : 'vs'}
          value={value ?? ''}
          onChange={(next) => onChange?.(next ?? '')}
          onMount={(editor) => setRoot(editor.getDomNode())}
          options={{
            ariaLabel,
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

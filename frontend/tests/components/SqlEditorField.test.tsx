import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';

// Monaco itself can't run under jsdom (canvas, workers); the mock stands in for the editor so
// what's under test is OUR glue: the antd-Form controlled-field contract (value in, onChange out,
// null coercions) and the worker/loader setup running without a CDN fetch.
const editorOptions = vi.fn<(options: Record<string, unknown>) => void>();
vi.mock('@monaco-editor/react', async () => {
  const { useEffect, useRef } = await import('react');
  return {
    default: function FakeEditor({
      value,
      onChange,
      options,
      onMount,
    }: {
      value: string;
      onChange: (v: string | undefined) => void;
      options: Record<string, unknown>;
      onMount?: (editor: { getDomNode: () => HTMLElement | null }) => void;
    }) {
      const root = useRef<HTMLDivElement>(null);
      editorOptions(options);
      useEffect(() => onMount?.({ getDomNode: () => root.current }), [onMount]);
      return (
        <div ref={root}>
          <textarea
            data-testid="editor"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onBlur={() => onChange(undefined)}
          />
        </div>
      );
    },
    loader: { config: vi.fn() },
  };
});
// These specifiers must match the module's imports EXACTLY or the mock is dead and the real Monaco
// loads underneath.
vi.mock('monaco-editor/editor/editor.api.js', () => ({}));
vi.mock('monaco-editor/languages/definitions/sql/register.js', () => ({}));
vi.mock('monaco-editor/editor/editor.worker.js?worker', () => ({
  // Worker constructor stand-in — instantiated by the module under test, never used.
  default: function FakeWorker() {},
}));

import SqlEditorField from '../../src/components/checks/SqlEditorField';
import { ThemeModeProvider } from '../../src/themeMode/ThemeModeProvider';

// The editor's `theme` prop reads useThemeMode(), so every render needs the provider.
function renderField(props: ComponentProps<typeof SqlEditorField> = {}) {
  return render(
    <ThemeModeProvider>
      <SqlEditorField {...props} />
    </ThemeModeProvider>,
  );
}

describe('SqlEditorField', () => {
  it('renders the current value and propagates edits', () => {
    const onChange = vi.fn();
    renderField({ value: 'SELECT 1', onChange });
    const editor = screen.getByTestId('editor');
    expect(editor).toHaveValue('SELECT 1');
    fireEvent.change(editor, { target: { value: 'SELECT 2' } });
    expect(onChange).toHaveBeenCalledWith('SELECT 2');
  });

  it('coerces an undefined form value to an empty editor', () => {
    renderField();
    expect(screen.getByTestId('editor')).toHaveValue('');
  });

  it("coerces Monaco's undefined (cleared buffer) to '' for the form", () => {
    const onChange = vi.fn();
    renderField({ value: 'x', onChange });
    fireEvent.blur(screen.getByTestId('editor'));
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('names the editor and tells a keyboard user how to leave it (#1672)', () => {
    renderField({ ariaLabel: 'Source query (read-only)' });
    expect(editorOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ ariaLabel: 'Source query (read-only)' }),
    );
    const hint = screen.getByText(/To move on with Tab, press Ctrl\+M first/);
    // Tied to Monaco's input, so a screen reader reads it on focus.
    expect(screen.getByTestId('editor')).toHaveAttribute('aria-describedby', hint.id);
  });

  it('has a generic name when its caller gives none', () => {
    renderField();
    expect(editorOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ ariaLabel: 'SQL query' }),
    );
  });
});

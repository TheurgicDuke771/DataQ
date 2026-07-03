import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// Monaco itself can't run under jsdom (canvas, workers); the mock stands in for
// the editor so what's under test is OUR glue: the antd-Form controlled-field
// contract (value in, onChange out, null coercions) and the worker/loader setup
// running without a CDN fetch.
vi.mock('@monaco-editor/react', () => ({
  default: ({ value, onChange }: { value: string; onChange: (v: string | undefined) => void }) => (
    <textarea
      data-testid="editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={() => onChange(undefined)}
    />
  ),
  loader: { config: vi.fn() },
}));
vi.mock('monaco-editor', () => ({}));
vi.mock('monaco-editor/esm/vs/editor/editor.worker?worker', () => ({
  // Worker constructor stand-in — instantiated by the module under test, never used.
  default: function FakeWorker() {},
}));

import SqlEditorField from '../../src/components/checks/SqlEditorField';

describe('SqlEditorField', () => {
  it('renders the current value and propagates edits', () => {
    const onChange = vi.fn();
    render(<SqlEditorField value="SELECT 1" onChange={onChange} />);
    const editor = screen.getByTestId('editor');
    expect(editor).toHaveValue('SELECT 1');
    fireEvent.change(editor, { target: { value: 'SELECT 2' } });
    expect(onChange).toHaveBeenCalledWith('SELECT 2');
  });

  it('coerces an undefined form value to an empty editor', () => {
    render(<SqlEditorField />);
    expect(screen.getByTestId('editor')).toHaveValue('');
  });

  it("coerces Monaco's undefined (cleared buffer) to '' for the form", () => {
    const onChange = vi.fn();
    render(<SqlEditorField value="x" onChange={onChange} />);
    fireEvent.blur(screen.getByTestId('editor'));
    expect(onChange).toHaveBeenCalledWith('');
  });
});

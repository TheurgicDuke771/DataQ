import { Select } from 'antd';
import { useEffect, useMemo, useState } from 'react';

import { listTags, normalizeTagName } from '../../api/tags';

/**
 * Pick tags for an entity: the workspace vocabulary as options plus free typing for a new name
 * (`Select mode="tags"`). Names are normalized as the server will store them, so `Finance` and
 * `finance` collapse to one chip before the save rather than after.
 *
 * Controlled (`value`/`onChange`) so it drops straight into an antd `Form.Item`, which also
 * supplies its label → the control's accessible name. Pass `id`/`aria-label` yourself outside
 * a form.
 */
export function TagPicker({
  value,
  onChange,
  id,
  disabled = false,
  placeholder = 'Add a tag…',
  'aria-label': ariaLabel,
}: {
  value?: string[];
  onChange?: (tags: string[]) => void;
  id?: string;
  disabled?: boolean;
  placeholder?: string;
  'aria-label'?: string;
}) {
  const [vocabulary, setVocabulary] = useState<string[] | null>(null);
  const [failed, setFailed] = useState(false);

  // Loaded once per mount; a failed load degrades to free typing, never to a dead control.
  useEffect(() => {
    let cancelled = false;
    listTags()
      .then((tags) => {
        if (!cancelled) setVocabulary(tags.map((t) => t.name));
      })
      .catch(() => {
        if (!cancelled) {
          setVocabulary([]);
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const options = useMemo(() => {
    const names = new Set([...(vocabulary ?? []), ...(value ?? [])]);
    return [...names].sort().map((name) => ({ value: name, label: name }));
  }, [vocabulary, value]);

  return (
    <Select<string[]>
      id={id}
      aria-label={ariaLabel}
      mode="tags"
      allowClear
      disabled={disabled}
      loading={vocabulary === null}
      placeholder={placeholder}
      value={value ?? []}
      options={options}
      tokenSeparators={[',']}
      // Comma-split tokens arrive here raw; normalize + dedupe so the chips match the store.
      onChange={(next) => {
        const seen = new Set<string>();
        const clean: string[] = [];
        for (const raw of next) {
          const name = normalizeTagName(raw);
          if (name && !seen.has(name)) {
            seen.add(name);
            clean.push(name);
          }
        }
        onChange?.(clean);
      }}
      notFoundContent={
        failed
          ? 'Existing tags could not be loaded — type a name to add one.'
          : 'No tags yet — type a name to create one.'
      }
      style={{ width: '100%' }}
    />
  );
}

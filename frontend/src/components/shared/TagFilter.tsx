import { Select } from 'antd';
import { useEffect, useState } from 'react';

import { listTags } from '../../api/tags';
import { Filter } from './Filter';

/**
 * The list-page tag filter: pick one or more existing tags; the list keeps rows carrying EVERY
 * one (the server's `tag=a&tag=b` AND). Labelled through `Filter`, which binds a real `<label>`
 * to the control.
 */
export function TagFilter({
  value,
  onChange,
  label = 'Tags (all of)',
}: {
  value: string[];
  onChange: (tags: string[]) => void;
  label?: string;
}) {
  const [options, setOptions] = useState<{ value: string; label: string }[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listTags()
      .then((tags) => {
        if (!cancelled) setOptions(tags.map((t) => ({ value: t.name, label: t.name })));
      })
      .catch(() => {
        // The filter is a convenience over the list; a failed vocabulary load leaves the list
        // usable and the filter honest about having nothing to offer.
        if (!cancelled) setOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Filter label={label}>
      <Select<string[]>
        mode="multiple"
        allowClear
        size="small"
        loading={options === null}
        placeholder="Any tag"
        value={value}
        options={options ?? []}
        onChange={onChange}
        notFoundContent={options?.length === 0 ? 'No tags in this workspace yet' : undefined}
        style={{ minWidth: 220 }}
      />
    </Filter>
  );
}

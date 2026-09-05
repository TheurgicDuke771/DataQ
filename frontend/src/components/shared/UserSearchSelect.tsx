import { Select, Spin } from 'antd';
import { type CSSProperties, useEffect, useRef, useState } from 'react';

import { searchUsers, type UserSummary } from '../../api/shares';

/** Debounced directory search that keeps the picked user as an object, so a later
 *  search replacing the options can never blank the selection. */
export function UserSearchSelect({
  value,
  onChange,
  exclude,
  placeholder = 'Search by email or name',
  ariaLabel,
  style,
}: {
  value: UserSummary | undefined;
  onChange: (user: UserSummary | undefined) => void;
  exclude?: (user: UserSummary) => boolean;
  placeholder?: string;
  ariaLabel?: string;
  style?: CSSProperties;
}) {
  const [options, setOptions] = useState<UserSummary[]>([]);
  const [searching, setSearching] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Last-wins token; unmount parks it on a sentinel so an in-flight response is dropped.
  const latest = useRef(0);
  useEffect(
    () => () => {
      clearTimeout(timer.current);
      latest.current = -1;
    },
    [],
  );

  const onSearch = (raw: string) => {
    const q = raw.trim();
    clearTimeout(timer.current);
    if (q.length < 2) {
      setOptions([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const token = (latest.current += 1);
    timer.current = setTimeout(() => {
      searchUsers(q)
        .then((users) => {
          if (token !== latest.current) return;
          setOptions(exclude ? users.filter((u) => !exclude(u)) : users);
        })
        .catch(() => {
          if (token === latest.current) setOptions([]);
        })
        .finally(() => {
          if (token === latest.current) setSearching(false);
        });
    }, 300);
  };

  const shown = value && !options.some((u) => u.id === value.id) ? [value, ...options] : options;
  return (
    <Select
      showSearch={{ filterOption: false, onSearch }}
      value={value?.id}
      placeholder={placeholder}
      onChange={(id: string) => onChange(shown.find((u) => u.id === id))}
      notFoundContent={searching ? <Spin size="small" /> : null}
      options={shown.map((u) => ({
        value: u.id,
        label: u.display_name ? `${u.display_name} · ${u.email}` : u.email,
      }))}
      aria-label={ariaLabel}
      style={style}
    />
  );
}

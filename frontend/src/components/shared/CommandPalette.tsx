import { SearchOutlined } from '@ant-design/icons';
import { Button, Input, type InputRef, Typography } from 'antd';
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate } from 'react-router-dom';

import {
  SEARCH_GROUPS,
  search,
  type SearchGroupName,
  type SearchHit,
  type SearchResponse,
} from '../../api/search';
import { IS_DEMO } from '../../demo/flag';
import { fetchFailure } from '../../utils/errors';
import { formatTimestamp } from '../results/resultsFormat';
import { Modal } from './Modal';

const GROUP_LABELS: Record<SearchGroupName, string> = {
  suites: 'Suites',
  checks: 'Checks',
  connections: 'Connections',
  runs: 'Runs',
};

export const DEMO_SEARCH_MESSAGE = 'Search is not part of the read-only demo.';
const DEBOUNCE_MS = 200;

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent);
/** The shortcut as the user's keyboard writes it. */
const SHORTCUT_LABEL = isMac ? '⌘K' : 'Ctrl K';

type Phase =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ok'; data: SearchResponse }
  | { kind: 'error'; message: string };

interface Option {
  id: string;
  group: SearchGroupName;
  hit: SearchHit;
}

function flatten(data: SearchResponse, prefix: string): Option[] {
  return SEARCH_GROUPS.flatMap((group) =>
    data.groups[group].map((hit) => ({ id: `${prefix}-${group}-${hit.id}`, group, hit })),
  );
}

function hitDescription(group: SearchGroupName, hit: SearchHit): string | null {
  if (group === 'runs') {
    const when = hit.when ? formatTimestamp(hit.when) : null;
    return [hit.sublabel, when].filter(Boolean).join(' · ') || null;
  }
  return hit.sublabel;
}

/**
 * Global search (⌘K / Ctrl K): the header's search button and the dialog it opens, mounted once
 * in the app shell. Typing searches suites, checks, connections and runs by name; arrow keys move
 * through the results, Enter opens the highlighted one, Escape closes. Focus returns to whatever
 * had it when the palette opened.
 */
export function CommandPalette({ compact = false }: { compact?: boolean }) {
  const navigate = useNavigate();
  const baseId = useId();
  const listId = `${baseId}-list`;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [active, setActive] = useState(0);
  const inputRef = useRef<InputRef>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const requestSeq = useRef(0);

  const openPalette = useCallback(() => {
    openerRef.current = document.activeElement as HTMLElement | null;
    setOpen(true);
  }, []);

  const closePalette = useCallback(() => {
    setOpen(false);
    requestSeq.current += 1;
    setQuery('');
    setPhase({ kind: 'idle' });
    setActive(0);
    const opener = openerRef.current;
    const target = opener && opener.isConnected && opener !== document.body ? opener : null;
    // After the dialog has let go of focus, not before.
    requestAnimationFrame(() => (target ?? triggerRef.current)?.focus());
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        if (open) closePalette();
        else openPalette();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, openPalette, closePalette]);

  // The phase follows the keystroke; the request itself is debounced and abortable, and a late
  // answer to an earlier query is dropped.
  const onQueryChange = (value: string) => {
    setQuery(value);
    requestSeq.current += 1;
    const term = value.trim();
    if (!term) setPhase({ kind: 'idle' });
    else if (IS_DEMO) setPhase({ kind: 'error', message: DEMO_SEARCH_MESSAGE });
    else setPhase({ kind: 'loading' });
  };

  useEffect(() => {
    const term = query.trim();
    if (!open || !term || IS_DEMO) return;
    const seq = requestSeq.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      search(term, controller.signal)
        .then((data) => {
          if (seq !== requestSeq.current) return;
          setPhase({ kind: 'ok', data });
          setActive(0);
        })
        .catch((err: unknown) => {
          if (seq !== requestSeq.current || controller.signal.aborted) return;
          setPhase({ kind: 'error', message: fetchFailure(err, 'Search failed').message });
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query]);

  const options = useMemo(
    () => (phase.kind === 'ok' ? flatten(phase.data, baseId) : []),
    [phase, baseId],
  );
  const activeOption = options[active];

  const choose = (option: Option) => {
    closePalette();
    navigate(option.hit.href);
  };

  const onInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' && options.length) {
      event.preventDefault();
      setActive((i) => (i + 1) % options.length);
    } else if (event.key === 'ArrowUp' && options.length) {
      event.preventDefault();
      setActive((i) => (i - 1 + options.length) % options.length);
    } else if (event.key === 'Enter' && activeOption) {
      event.preventDefault();
      choose(activeOption);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closePalette();
    }
  };

  useEffect(() => {
    if (!activeOption) return;
    document.getElementById(activeOption.id)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeOption]);

  const status = statusText(phase, query, options.length);

  return (
    <>
      <Button
        ref={triggerRef}
        type="text"
        icon={<SearchOutlined aria-hidden />}
        aria-label={compact ? `Search (${SHORTCUT_LABEL})` : undefined}
        aria-keyshortcuts={isMac ? 'Meta+K' : 'Control+K'}
        onClick={openPalette}
        style={{ color: 'var(--dq-muted)' }}
      >
        {!compact && (
          <>
            Search
            <kbd
              aria-hidden
              style={{
                marginInlineStart: 8,
                padding: '0 6px',
                borderRadius: 4,
                border: '1px solid var(--dq-border)',
                background: 'var(--dq-canvas)',
                fontSize: 12,
                fontFamily: 'inherit',
              }}
            >
              {SHORTCUT_LABEL}
            </kbd>
          </>
        )}
      </Button>
      {open && (
        <Modal
          open
          onCancel={closePalette}
          title={<span className="dq-sr-only">Search DataQ</span>}
          footer={null}
          closable={false}
          focusable={{ focusTriggerAfterClose: false }}
          width={640}
          styles={{ header: { marginBottom: 0 }, body: { paddingTop: 4 } }}
          afterOpenChange={(visible) => {
            if (visible) inputRef.current?.focus();
          }}
        >
          <Input
            ref={inputRef}
            autoFocus
            size="large"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder="Search suites, checks, connections and runs"
            prefix={<SearchOutlined aria-hidden style={{ color: 'var(--dq-muted)' }} />}
            allowClear
            autoComplete="off"
            role="combobox"
            aria-label="Search DataQ"
            aria-expanded={options.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeOption?.id}
            aria-describedby={`${baseId}-status`}
          />
          <ul
            id={listId}
            role="listbox"
            aria-label="Search results"
            style={{
              listStyle: 'none',
              margin: '12px 0 0',
              padding: 0,
              maxHeight: '50vh',
              overflowY: 'auto',
            }}
          >
            {phase.kind === 'ok' &&
              SEARCH_GROUPS.filter((group) => phase.data.groups[group].length > 0).map((group) => (
                <li key={group} role="presentation">
                  <Typography.Text
                    type="secondary"
                    role="presentation"
                    style={{
                      display: 'block',
                      fontSize: 12,
                      textTransform: 'uppercase',
                      letterSpacing: 0.4,
                      padding: '10px 8px 4px',
                    }}
                  >
                    {GROUP_LABELS[group]}
                  </Typography.Text>
                  <ul role="group" aria-label={GROUP_LABELS[group]} style={listReset}>
                    {options
                      .filter((option) => option.group === group)
                      .map((option) => {
                        const selected = option.id === activeOption?.id;
                        const description = hitDescription(group, option.hit);
                        return (
                          <li
                            key={option.id}
                            id={option.id}
                            role="option"
                            aria-selected={selected}
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => choose(option)}
                            onMouseEnter={() => setActive(options.indexOf(option))}
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              gap: 12,
                              padding: '8px 10px',
                              borderRadius: 6,
                              cursor: 'pointer',
                              background: selected ? 'var(--dq-selected-bg)' : undefined,
                              color: 'var(--dq-ink)',
                            }}
                          >
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {option.hit.label}
                            </span>
                            {description && (
                              <Typography.Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
                                {description}
                              </Typography.Text>
                            )}
                          </li>
                        );
                      })}
                  </ul>
                  {phase.data.truncated_groups.includes(group) && (
                    <Typography.Text
                      type="secondary"
                      role="presentation"
                      style={{ display: 'block', fontSize: 12, padding: '2px 10px 6px' }}
                    >
                      More {GROUP_LABELS[group].toLowerCase()} match — keep typing to narrow it.
                    </Typography.Text>
                  )}
                </li>
              ))}
          </ul>
          <div
            id={`${baseId}-status`}
            role="status"
            aria-live="polite"
            style={{ marginTop: 8, color: 'var(--dq-muted)', fontSize: 12, minHeight: 18 }}
          >
            {status}
          </div>
        </Modal>
      )}
    </>
  );
}

const listReset: CSSProperties = { listStyle: 'none', margin: 0, padding: 0 };

function statusText(phase: Phase, query: string, count: number): string {
  switch (phase.kind) {
    case 'idle':
      return query.trim() ? '' : 'Type to search. ↑↓ to move, Enter to open, Esc to close.';
    case 'loading':
      return 'Searching…';
    case 'error':
      return phase.message;
    case 'ok':
      return count === 0
        ? `No matches for “${phase.data.query}”.`
        : `${count} ${count === 1 ? 'result' : 'results'}. ↑↓ to move, Enter to open.`;
  }
}

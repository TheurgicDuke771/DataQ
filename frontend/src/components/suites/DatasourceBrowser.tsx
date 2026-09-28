import {
  DatabaseOutlined,
  FileOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { Alert, Breadcrumb, Button, Empty, Flex, Modal, Spin, Tag, Typography } from 'antd';
import axios from 'axios';
import { type ReactNode, useEffect, useState } from 'react';

import {
  browseCatalog,
  browseFiles,
  type CatalogBrowse,
  type FileBrowse,
} from '../../api/connections';
import { errorMessage } from '../../utils/errors';
import { SimpleList } from '../SimpleList';

type Load<T> =
  { status: 'loading' } | { status: 'ok'; data: T } | { status: 'error'; message: string };

/** The envelope message plus the backend's classified, secret-free `detail.reason` on a 502. */
function browseErrorMessage(err: unknown): string {
  const reason = axios.isAxiosError(err)
    ? (err.response?.data as { error?: { detail?: { reason?: unknown } } } | undefined)?.error
        ?.detail?.reason
    : undefined;
  return [errorMessage(err), typeof reason === 'string' ? reason : ''].filter(Boolean).join(' ');
}

/**
 * Fetch `load(signal)` whenever `key` changes while `open`, aborting the superseded request — a
 * fast click-through must not leave listings running against the datasource.
 */
function useListing<T>(open: boolean, key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{ key: string; load: Load<T> } | null>(null);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ key, load: { status: 'ok', data } });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setState({ key, load: { status: 'error', message: browseErrorMessage(err) } });
        }
      });
    return () => controller.abort();
    // `load` is rebuilt every render; `key` is its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, key]);
  // A stored answer for an older key is never shown under the new one — so a key change reads as
  // loading without a synchronous reset. Callers fold an open-generation into `key`.
  return state && state.key === key ? state.load : ({ status: 'loading' } as Load<T>);
}

function TruncatedNote({ limit, noun }: { limit: number; noun: string }) {
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 8 }}
      title={`Showing the first ${limit} ${noun} — there are more at this level.`}
      description="If yours isn't listed, close this and type it into the field instead."
    />
  );
}

function ListingBody<T>({ load, render }: { load: Load<T>; render: (data: T) => ReactNode }) {
  if (load.status === 'loading') {
    return (
      <Flex justify="center" style={{ padding: 24 }}>
        <Spin aria-label="Loading listing" />
      </Flex>
    );
  }
  if (load.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        title="Couldn't list the datasource"
        description={load.message}
      />
    );
  }
  return <>{render(load.data)}</>;
}

/** A breadcrumb step you can go back to — a real button, so it is keyboard-reachable. */
function Crumb({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button type="link" size="small" onClick={onClick} style={{ padding: 0, height: 'auto' }}>
      {label}
    </Button>
  );
}

const UNSELECTABLE_HINT =
  "Can't be picked — DataQ targets only plain names (letters, digits, _ and $; not starting with a digit).";

export interface PickedTable {
  /** Absent on a schema-rooted tree (a generic SQL connection pins its database). */
  catalog?: string;
  schema: string;
  table: string;
}

type Level = 'catalog' | 'schema' | 'table';

const LEVEL_NOUN: Record<Level, string> = {
  catalog: 'catalogs',
  schema: 'schemas',
  table: 'tables',
};

const LEVEL_ICON: Record<Level, ReactNode> = {
  catalog: <DatabaseOutlined />,
  schema: <FolderOutlined />,
  table: <TableOutlined />,
};

/**
 * Run-target picker, one level per request: catalogs → schemas → tables on Unity Catalog, or
 * schemas → tables (`root="schema"`) on a generic SQL connection such as PostgreSQL, whose
 * connection pins one database. The typed fields stay the source of truth — picking a table
 * only fills them.
 */
export function CatalogBrowserButton({
  connectionId,
  onPick,
  root = 'catalog',
}: {
  connectionId: string;
  onPick: (picked: PickedTable) => void;
  root?: 'catalog' | 'schema';
}) {
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [path, setPath] = useState<string[]>([]);
  const levels: Level[] = root === 'catalog' ? ['catalog', 'schema', 'table'] : ['schema', 'table'];
  const level = levels[path.length];
  const catalog = root === 'catalog' ? path[0] : undefined;
  const schema = root === 'catalog' ? path[1] : path[0];
  const key = JSON.stringify([generation, connectionId, root, ...path]);
  const load = useListing<CatalogBrowse>(open, key, (signal) =>
    browseCatalog(connectionId, { catalog, schema }, signal),
  );

  const noun = LEVEL_NOUN[level];
  const icon = LEVEL_ICON[level];
  const within = path.join('.');
  const emptyText = within
    ? `No ${noun} in ${within} are visible to this connection's credential.`
    : `No ${noun} are visible to this connection's credential.`;
  const rootLabel = root === 'catalog' ? 'Catalogs' : 'Schemas';

  const choose = (name: string) => {
    if (level !== 'table') {
      setPath([...path, name]);
      return;
    }
    onPick({
      ...(catalog !== undefined ? { catalog } : {}),
      schema: schema as string,
      table: name,
    });
    setOpen(false);
  };

  return (
    <>
      <Button
        size="small"
        icon={<FolderOpenOutlined />}
        onClick={() => {
          setPath([]);
          setGeneration((g) => g + 1);
          setOpen(true);
        }}
        style={{ marginBottom: 12 }}
      >
        {root === 'catalog' ? 'Browse catalog…' : 'Browse schemas…'}
      </Button>
      <Modal
        title="Pick a table"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Breadcrumb
          style={{ marginBottom: 12 }}
          items={[
            {
              title: path.length ? (
                <Crumb label={rootLabel} onClick={() => setPath([])} />
              ) : (
                rootLabel
              ),
            },
            ...path.map((part, i) => ({
              title:
                i < path.length - 1 ? (
                  <Crumb label={part} onClick={() => setPath(path.slice(0, i + 1))} />
                ) : (
                  part
                ),
            })),
          ]}
        />
        <ListingBody
          load={load}
          render={(data) => (
            <>
              {data.truncated && <TruncatedNote limit={data.limit} noun={noun} />}
              {data.entries.length === 0 && <Empty description={emptyText} />}
              <SimpleList
                size="small"
                dataSource={data.entries}
                rowKey="name"
                style={{ maxHeight: 360, overflowY: 'auto' }}
                renderItem={(entry) => (
                  <SimpleList.Item>
                    {entry.selectable ? (
                      <Flex align="center" gap={8}>
                        <Button
                          type="link"
                          icon={icon}
                          onClick={() => choose(entry.name)}
                          style={{ padding: 0 }}
                        >
                          {entry.name}
                        </Button>
                        <ObjectTypeTag type={entry.object_type} />
                      </Flex>
                    ) : (
                      <Flex vertical>
                        <Typography.Text type="secondary">
                          <span style={{ marginRight: 8 }}>{icon}</span>
                          {entry.name}
                        </Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {UNSELECTABLE_HINT}
                        </Typography.Text>
                      </Flex>
                    )}
                  </SimpleList.Item>
                )}
              />
            </>
          )}
        />
      </Modal>
    </>
  );
}

/** `raw/2026/` → `['raw', '2026']`. */
function prefixSegments(prefix: string): string[] {
  return prefix.split('/').filter(Boolean);
}

/** The last path segment of a key, for display inside its folder. */
function leaf(key: string): string {
  const parts = key.split('/').filter(Boolean);
  return parts[parts.length - 1] ?? key;
}

/**
 * Whether the backend will list under `prefix` — mirrors `browse_service.validate_prefix`: no
 * empty/`.`/`..` segment (a leading `/` is an empty first one), no backslash or control character.
 * A store can hold a key like `exports//x.csv`, whose `exports//` folder would otherwise read as
 * `exports/` and 422.
 */
function isBrowsablePrefix(prefix: string): boolean {
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\\]/.test(prefix)) return false;
  const segments = prefix.split('/');
  const inner = segments[segments.length - 1] === '' ? segments.slice(0, -1) : segments;
  return inner.every((seg) => seg !== '' && seg !== '.' && seg !== '..');
}

const UNBROWSABLE_HINT =
  "Can't be opened here — its path starts with '/' or has an empty, '.' or '..' part.";

function formatSize(bytes: number | null): string {
  if (bytes === null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * ADLS Gen2 / S3 browser over the connection's one container/bucket. `mode="file"` picks a file
 * (single-file target); `mode="folder"` picks the current folder (a batch target's prefix).
 */
export function FileBrowserButton({
  connectionId,
  rootLabel,
  mode,
  onPick,
}: {
  connectionId: string;
  /** The connection's container/bucket name, for the breadcrumb's first step. */
  rootLabel: string;
  mode: 'file' | 'folder';
  onPick: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [prefix, setPrefix] = useState('');
  const key = JSON.stringify([generation, connectionId, prefix]);
  const load = useListing<FileBrowse>(open, key, (signal) =>
    browseFiles(connectionId, { prefix }, signal),
  );
  const segments = prefixSegments(prefix);
  const pick = (path: string) => {
    onPick(path);
    setOpen(false);
  };

  return (
    <>
      <Button
        size="small"
        icon={<FolderOpenOutlined />}
        onClick={() => {
          setPrefix('');
          setGeneration((g) => g + 1);
          setOpen(true);
        }}
        style={{ marginBottom: 12 }}
      >
        {mode === 'file' ? 'Browse files…' : 'Browse folders…'}
      </Button>
      <Modal
        title={mode === 'file' ? 'Pick a file' : 'Pick a folder'}
        open={open}
        onCancel={() => setOpen(false)}
        destroyOnHidden
        footer={
          mode === 'folder' ? (
            <Button type="primary" onClick={() => pick(prefix)}>
              {prefix ? `Use ${prefix}` : 'Use the top level (no prefix)'}
            </Button>
          ) : null
        }
      >
        <Breadcrumb
          style={{ marginBottom: 12 }}
          items={[
            {
              title: segments.length ? (
                <Crumb label={rootLabel} onClick={() => setPrefix('')} />
              ) : (
                rootLabel
              ),
            },
            ...segments.map((part, i) => ({
              title:
                i < segments.length - 1 ? (
                  <Crumb
                    label={part}
                    onClick={() => setPrefix(`${segments.slice(0, i + 1).join('/')}/`)}
                  />
                ) : (
                  part
                ),
            })),
          ]}
        />
        <ListingBody
          load={load}
          render={(data) => {
            const rows = [
              ...data.folders.map((folder) => ({ key: folder, folder: true, size: null })),
              ...data.files.map((file) => ({ key: file.path, folder: false, size: file.size })),
            ];
            return (
              <>
                {data.truncated && <TruncatedNote limit={data.limit} noun="entries" />}
                {rows.length === 0 && <Empty description="Nothing under this folder." />}
                <SimpleList
                  size="small"
                  dataSource={rows}
                  rowKey="key"
                  style={{ maxHeight: 360, overflowY: 'auto' }}
                  renderItem={(row) => (
                    <SimpleList.Item
                      actions={
                        row.folder
                          ? undefined
                          : [
                              <Typography.Text key="size" type="secondary" style={{ fontSize: 12 }}>
                                {formatSize(row.size)}
                              </Typography.Text>,
                            ]
                      }
                    >
                      {row.folder && !isBrowsablePrefix(row.key) ? (
                        <Flex vertical>
                          <Typography.Text type="secondary">
                            <FolderOutlined style={{ marginRight: 8 }} />
                            {row.key}
                          </Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {UNBROWSABLE_HINT}
                          </Typography.Text>
                        </Flex>
                      ) : row.folder ? (
                        <Button
                          type="link"
                          icon={<FolderOutlined />}
                          onClick={() => setPrefix(row.key)}
                          style={{ padding: 0 }}
                        >
                          {leaf(row.key)}/
                        </Button>
                      ) : mode === 'file' ? (
                        <Button
                          type="link"
                          icon={<FileOutlined />}
                          onClick={() => pick(row.key)}
                          style={{ padding: 0 }}
                        >
                          {leaf(row.key)}
                        </Button>
                      ) : (
                        <Typography.Text type="secondary">
                          <FileOutlined style={{ marginRight: 8 }} />
                          {leaf(row.key)}
                        </Typography.Text>
                      )}
                    </SimpleList.Item>
                  )}
                />
              </>
            );
          }}
        />
      </Modal>
    </>
  );
}

const OBJECT_TYPE_LABELS: Record<string, string> = {
  view: 'View',
  materialized_view: 'Materialized view',
  dynamic_table: 'Dynamic table',
  streaming_table: 'Streaming table',
};

/** Marks anything that is not a plain table, so a view is never picked by mistake. */
function ObjectTypeTag({ type }: { type?: string | null }) {
  const label = type ? OBJECT_TYPE_LABELS[type] : undefined;
  return label ? <Tag>{label}</Tag> : null;
}

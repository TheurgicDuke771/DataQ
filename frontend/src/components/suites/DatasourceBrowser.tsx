import {
  DatabaseOutlined,
  FileOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { Alert, Breadcrumb, Button, Empty, Flex, Modal, Spin, Typography } from 'antd';
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
  catalog: string;
  schema: string;
  table: string;
}

/**
 * Unity Catalog run-target picker: catalogs → schemas → tables, one level per request. The typed
 * fields stay the source of truth — picking a table only fills them.
 */
export function CatalogBrowserButton({
  connectionId,
  onPick,
}: {
  connectionId: string;
  onPick: (picked: PickedTable) => void;
}) {
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [path, setPath] = useState<string[]>([]);
  const [catalog, schema] = path;
  const key = JSON.stringify([generation, connectionId, ...path]);
  const load = useListing<CatalogBrowse>(open, key, (signal) =>
    browseCatalog(connectionId, { catalog, schema }, signal),
  );

  const noun = path.length === 0 ? 'catalogs' : path.length === 1 ? 'schemas' : 'tables';
  const icon =
    path.length === 0 ? (
      <DatabaseOutlined />
    ) : path.length === 1 ? (
      <FolderOutlined />
    ) : (
      <TableOutlined />
    );
  const emptyText =
    path.length === 0
      ? "No catalogs are visible to this connection's credential."
      : path.length === 1
        ? `No schemas in ${catalog} are visible to this connection's credential.`
        : `No tables in ${catalog}.${schema} are visible to this connection's credential.`;

  const choose = (name: string) => {
    if (path.length < 2) {
      setPath([...path, name]);
      return;
    }
    onPick({ catalog: catalog as string, schema: schema as string, table: name });
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
        Browse catalog…
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
                <Crumb label="Catalogs" onClick={() => setPath([])} />
              ) : (
                'Catalogs'
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
                      <Button
                        type="link"
                        icon={icon}
                        onClick={() => choose(entry.name)}
                        style={{ padding: 0 }}
                      >
                        {entry.name}
                      </Button>
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
              {prefix ? `Use ${prefix}` : 'Use the whole container'}
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
                      {row.folder ? (
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

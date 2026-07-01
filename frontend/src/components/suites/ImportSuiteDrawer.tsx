import { InboxOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Descriptions,
  Drawer,
  Flex,
  Form,
  Select,
  Typography,
  Upload,
} from 'antd';
import type { UploadFile } from 'antd';
import { useEffect, useRef, useState } from 'react';

import {
  CONNECTION_KIND,
  CONNECTION_TYPE_LABELS,
  type Connection,
  envLabel,
} from '../../api/connections';
import { importSuite, type Suite, type SuiteDocument } from '../../api/suites';

/**
 * Import a portable suite document (the JSON produced by "Export") onto a chosen
 * connection. The file is parsed + shape-checked client-side, then handed back to
 * the backend unchanged (`POST /suites/import`) so thresholds/config round-trip
 * exactly — the connection is the only thing the importer supplies. The new suite
 * is owned by the importing user, like create.
 */
export function ImportSuiteDrawer({
  open,
  connections,
  onClose,
  onImported,
}: {
  open: boolean;
  /** Datasource connections the document can be imported onto. */
  connections: Connection[];
  onClose: () => void;
  onImported: (suite: Suite) => void;
}) {
  const { message } = App.useApp();
  // A suite imports onto a datasource — orchestration providers (ADF/Airflow)
  // are never queryable, so they can't back a suite (CLAUDE.md §4, #242).
  const datasourceConnections = connections.filter((c) => CONNECTION_KIND[c.type] === 'datasource');
  const [connectionId, setConnectionId] = useState<string>();
  const [doc, setDoc] = useState<SuiteDocument | null>(null);
  const [fileName, setFileName] = useState<string>();
  const [parseError, setParseError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  // Monotonic token so a slow earlier file read can't overwrite a newer pick's
  // result (last-wins). Bumped on every pick and on remove; the effect below
  // bumps it on open/close so an in-flight parse from a prior open can't land in
  // a freshly-reopened drawer. Ref writes aren't allowed during render, hence the
  // effect rather than the render-phase reset block.
  const latestFile = useRef(0);
  useEffect(() => {
    latestFile.current += 1;
  }, [open]);

  // Reset everything when the drawer (re)opens — a stale document/connection from
  // a previous (possibly cancelled) import must not leak into the next one. The
  // Drawer host stays mounted across open/close (it isn't destroyed), so this is
  // a render-phase "adjust state when a prop changes" reset, matching the editor
  // panels — not an effect.
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setConnectionId(undefined);
      setDoc(null);
      setFileName(undefined);
      setParseError(undefined);
    }
  }

  // Parse + shape-check the dropped file; return false so antd never uploads it.
  const onFile = (file: File) => {
    const token = (latestFile.current += 1);
    file
      .text()
      .then((text) => {
        if (token !== latestFile.current) return; // superseded by a newer pick
        const parsed = parseSuiteDocument(text);
        setDoc(parsed);
        setFileName(file.name);
        setParseError(undefined);
      })
      .catch((err: unknown) => {
        if (token !== latestFile.current) return; // superseded by a newer pick
        setDoc(null);
        setFileName(file.name);
        setParseError(err instanceof Error ? err.message : 'Could not read the file.');
      });
    return false;
  };

  const onSubmit = async () => {
    if (!doc || !connectionId) return;
    setSubmitting(true);
    try {
      const suite = await importSuite({ connection_id: connectionId, document: doc });
      message.success(`${suite.name}: imported`);
      onImported(suite);
    } catch (err) {
      message.error(`Import failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  const fileList: UploadFile[] = fileName
    ? [{ uid: '1', name: fileName, status: parseError ? 'error' : 'done' }]
    : [];

  return (
    <Drawer
      title="Import suite"
      open={open}
      onClose={onClose}
      size={480}
      destroyOnHidden
      extra={
        <Flex gap={8}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            type="primary"
            loading={submitting}
            disabled={!doc || !connectionId}
            onClick={onSubmit}
          >
            Import
          </Button>
        </Flex>
      }
    >
      <Flex vertical gap={16}>
        <Upload.Dragger
          accept="application/json,.json"
          multiple={false}
          maxCount={1}
          beforeUpload={onFile}
          fileList={fileList}
          onRemove={() => {
            latestFile.current += 1; // drop any in-flight parse for the removed file
            setDoc(null);
            setFileName(undefined);
            setParseError(undefined);
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">Click or drag a suite export (.json) here</p>
          <p className="ant-upload-hint">
            A document produced by “Export” on any suite — checks, thresholds and config.
          </p>
        </Upload.Dragger>

        {parseError && (
          <Alert type="error" showIcon title="Invalid document" description={parseError} />
        )}

        {doc && (
          <Descriptions size="small" column={1} bordered styles={{ label: { width: 120 } }}>
            <Descriptions.Item label="Name">{doc.name}</Descriptions.Item>
            <Descriptions.Item label="Checks">{doc.checks.length}</Descriptions.Item>
            {doc.description && (
              <Descriptions.Item label="Description">{doc.description}</Descriptions.Item>
            )}
          </Descriptions>
        )}

        <Form layout="vertical">
          <Form.Item
            label="Import onto connection"
            required
            extra="The imported suite runs against this connection’s datasource."
          >
            <Select
              value={connectionId}
              onChange={setConnectionId}
              placeholder="Select a datasource connection"
              options={datasourceConnections.map((c) => ({
                value: c.id,
                label: `${c.name} · ${CONNECTION_TYPE_LABELS[c.type]} · ${envLabel(c.env)}`,
              }))}
            />
          </Form.Item>
        </Form>

        {datasourceConnections.length === 0 && (
          <Typography.Text type="secondary">
            Create a datasource connection first — a suite must import onto one.
          </Typography.Text>
        )}
      </Flex>
    </Drawer>
  );
}

/**
 * Parse the uploaded text as a suite export document, validating just enough of
 * the shape to fail fast on the wrong file (a random JSON, a half-document). The
 * object is otherwise passed through untouched so thresholds/config round-trip
 * exactly on import. Throws an `Error` with a user-facing message on any miss.
 */
function parseSuiteDocument(text: string): SuiteDocument {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error('Not valid JSON.');
  }
  if (!parsed || typeof parsed !== 'object') {
    throw new Error('Expected a suite document object.');
  }
  const obj = parsed as Record<string, unknown>;
  if (typeof obj.name !== 'string' || !obj.name) {
    throw new Error('Missing a suite “name” — is this a suite export?');
  }
  if (!Array.isArray(obj.checks)) {
    throw new Error('Missing a “checks” list — is this a suite export?');
  }
  return parsed as SuiteDocument;
}

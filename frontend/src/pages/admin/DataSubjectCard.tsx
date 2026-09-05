import { Alert, Button, Flex, Input, Modal, Typography } from 'antd';
import { useState } from 'react';

import {
  type DataSubjectErasure,
  type DataSubjectExport,
  eraseDataSubject,
  exportDataSubject,
} from '../../api/admin';
import { downloadJson, toFilenameStem } from '../../utils/download';
import { type FetchFailure, fetchFailure } from '../../utils/errors';
import { Filter } from '../../components/shared/Filter';
import { Section } from './parts';

type Receipt =
  { kind: 'export'; data: DataSubjectExport } | { kind: 'erase'; data: DataSubjectErasure };

const RECEIPT_TITLE = { export: 'Export receipt', erase: 'Erasure receipt' } as const;

/** Access/export (GDPR Art 15/20) and erasure (Art 17 / CCPA delete) over the
 *  sample data DataQ has captured. A subject is a `(column, value)` pair —
 *  DataQ has no people-table, so this IS the subject identifier. */
export function DataSubjectCard() {
  const [column, setColumn] = useState('');
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState<'export' | 'erase' | null>(null);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [typedConfirmation, setTypedConfirmation] = useState('');

  const closeConfirm = () => {
    setConfirming(false);
    setTypedConfirmation('');
  };

  const trimmedColumn = column.trim();
  const trimmedValue = value.trim();
  const ready = trimmedColumn !== '' && trimmedValue !== '';

  const run = async <T,>(kind: 'export' | 'erase', call: () => Promise<T>) => {
    setBusy(kind);
    setFailure(null);
    try {
      const data = await call();
      setReceipt({ kind, data } as Receipt);
    } catch (err) {
      setFailure(fetchFailure(err));
    } finally {
      setBusy(null);
    }
  };

  const onErase = () => {
    closeConfirm();
    void run('erase', () => eraseDataSubject(trimmedColumn, trimmedValue));
  };

  return (
    <Section title="Data-subject rights (GDPR / CCPA)">
      <Typography.Text type="secondary">
        Search every suite in the workspace for captured sample data naming a subject, then export
        or erase it. A subject is identified the way your warehouse identifies them — an identifier
        column and its value — because DataQ stores no people-table of its own. This does not touch
        your warehouse, only the samples DataQ has captured.
      </Typography.Text>

      <Flex gap={12} wrap align="flex-end">
        <Filter label="Identifier column">
          <Input
            style={{ width: 200 }}
            placeholder="e.g. email"
            value={column}
            onChange={(e) => setColumn(e.target.value)}
          />
        </Filter>
        <Filter label="Value">
          <Input
            style={{ width: 280 }}
            placeholder="e.g. alice@example.com"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </Filter>
        <Button
          type="primary"
          disabled={!ready}
          loading={busy === 'export'}
          onClick={() => void run('export', () => exportDataSubject(trimmedColumn, trimmedValue))}
        >
          Export data
        </Button>
        <Button
          danger
          disabled={!ready}
          loading={busy === 'erase'}
          onClick={() => setConfirming(true)}
        >
          Erase subject
        </Button>
      </Flex>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Both actions are audited and produce a receipt — they appear in the audit log above as{' '}
        <Typography.Text code>data_subject_request.export</Typography.Text> and{' '}
        <Typography.Text code>data_subject_request.erase</Typography.Text>. An export returns the
        matched data unredacted, which is the subject&apos;s own access right; erasure removes only
        the matching row or cell and cannot be undone.
      </Typography.Text>

      {failure && (
        <Alert
          type="error"
          showIcon
          title="The request failed"
          description={
            <Flex vertical gap={4}>
              <span>{failure.message}</span>
              {failure.requestId && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Request ID: <Typography.Text code>{failure.requestId}</Typography.Text>
                </Typography.Text>
              )}
            </Flex>
          }
        />
      )}

      <EraseConfirmModal
        open={confirming}
        column={trimmedColumn}
        value={trimmedValue}
        typed={typedConfirmation}
        onTyped={setTypedConfirmation}
        onCancel={closeConfirm}
        onConfirm={onErase}
      />
      <ReceiptModal receipt={receipt} onClose={() => setReceipt(null)} />
    </Section>
  );
}

/** Erasure is irreversible and workspace-wide, so it is gated on re-typing the
 *  subject value exactly — a mis-pasted value erases someone else's data. */
function EraseConfirmModal({
  open,
  column,
  value,
  typed,
  onTyped,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  column: string;
  value: string;
  /** Owned by the card, not the modal, so opening it always starts from empty —
   *  a confirmation typed for one subject can never arm the erase for the next. */
  typed: string;
  onTyped: (next: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const matches = typed === value;

  return (
    <Modal
      title="Erase this subject's captured data?"
      open={open}
      onCancel={onCancel}
      okText="Erase permanently"
      okButtonProps={{ danger: true, disabled: !matches }}
      onOk={onConfirm}
    >
      <Flex vertical gap={12}>
        <Typography.Paragraph>
          Every captured sample cell across every suite where{' '}
          <Typography.Text code>{column}</Typography.Text> is{' '}
          <Typography.Text code>{value}</Typography.Text> will be scrubbed from results and stored
          incident evidence. Other rows, other subjects and the surrounding check data are left
          intact. This cannot be undone.
        </Typography.Paragraph>
        <Typography.Text>Type the value to confirm:</Typography.Text>
        <Input
          aria-label="Type the subject value to confirm"
          placeholder={value}
          value={typed}
          onChange={(e) => onTyped(e.target.value)}
        />
      </Flex>
    </Modal>
  );
}

/** The receipt is rendered here rather than only downloaded — a browser or an
 *  embedding sandbox may refuse the download outright. */
function ReceiptModal({ receipt, onClose }: { receipt: Receipt | null; onClose: () => void }) {
  if (!receipt) return null;
  const json = JSON.stringify(receipt.data, null, 2);
  const filename = () =>
    `dataq-${receipt.kind}-${toFilenameStem(receipt.data.column, 'subject')}-${Date.now()}.json`;

  return (
    <Modal
      title={RECEIPT_TITLE[receipt.kind]}
      open
      onCancel={onClose}
      footer={[
        <Button key="download" onClick={() => downloadJson(filename(), receipt.data)}>
          Download JSON
        </Button>,
        <Button key="close" type="primary" onClick={onClose}>
          Close
        </Button>,
      ]}
      width={720}
    >
      <Flex vertical gap={12}>
        <ReceiptSummary receipt={receipt} />
        <Typography.Text copyable={{ text: json }}>Copy the receipt</Typography.Text>
        <pre
          style={{
            maxHeight: 360,
            overflow: 'auto',
            background: 'rgba(0,0,0,0.03)',
            padding: 12,
            margin: 0,
            fontSize: 12,
          }}
        >
          {json}
        </pre>
      </Flex>
    </Modal>
  );
}

function ReceiptSummary({ receipt }: { receipt: Receipt }) {
  if (receipt.kind === 'export') {
    const { match_count, incident_match_count, matches } = receipt.data;
    if (match_count === 0) {
      return (
        <Alert
          type="info"
          showIcon
          title="No captured data matches this subject"
          description="DataQ has stored no sample cell naming that column and value. This covers DataQ's own captured samples only — your warehouse is unaffected and unexamined."
        />
      );
    }
    return (
      <Alert
        type="success"
        showIcon
        title={`${match_count} match(es): ${matches.length} in results, ${incident_match_count} in stored incident evidence`}
      />
    );
  }
  const d = receipt.data;
  return (
    <Alert
      type={d.matched_count === d.erased_count ? 'success' : 'warning'}
      showIcon
      title={`Erased ${d.erased_count} of ${d.matched_count} match(es)`}
      description={`Results: ${d.erased_result_count} of ${d.matched_result_count}. Incident evidence: ${d.erased_incident_count} of ${d.matched_incident_count}.${
        d.matched_count === d.erased_count
          ? ''
          : ' Some matches had no scrub path and were left in place — re-run the export to see what remains.'
      }`}
    />
  );
}

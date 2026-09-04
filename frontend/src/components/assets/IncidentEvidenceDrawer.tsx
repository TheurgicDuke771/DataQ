import { Descriptions, Drawer, Empty, Flex, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { ReactNode } from 'react';

import {
  type EvidenceAssetLayer,
  type EvidenceCheckLayer,
  type EvidenceFailingResultLayer,
  type EvidenceSiblingCheck,
  type EvidenceTrendPoint,
  type EvidenceUpstreamPipelineRun,
  getIncident,
} from '../../api/incidents';
import type { ResultStatus } from '../../api/runs';
import { useAsyncData } from '../../hooks/useAsyncData';
import { AsyncBody } from '../AsyncBody';
import {
  formatDurationMs,
  formatScalar,
  formatTimestamp,
  RESULT_STATUS_COLORS,
} from '../results/resultsFormat';
import { AssetLink } from './AssetLink';
import { IncidentNarrativeSection } from './IncidentNarrativeSection';

/**
 * The layer-1 evidence card (`services/incident_evidence.py`, ADR 0034 decision 4; #1634 —
 * previously reachable only via `GET /incidents/{id}` and the MCP `get_incident` tool). Every
 * layer degrades to `null` independently on the backend rather than poisoning the whole card, so a
 * `null` layer renders here as an explicit "Not available" state — never blank — mirroring ADR
 * 0038's unclassified-dimension NULL rule.
 */
export function IncidentEvidenceDrawer({
  incidentId,
  onClose,
}: {
  /** The incident to show evidence for; `null` keeps the drawer closed. */
  incidentId: string | null;
  onClose: () => void;
}) {
  return (
    <Drawer
      title="Incident evidence"
      open={incidentId !== null}
      onClose={onClose}
      size={640}
      destroyOnHidden
    >
      {/* Keyed by id so switching incidents (drawer stays open) remounts and refetches. */}
      {incidentId && <EvidenceBody key={incidentId} incidentId={incidentId} />}
    </Drawer>
  );
}

function statusColor(status: string): string {
  return RESULT_STATUS_COLORS[status as ResultStatus] ?? 'default';
}

function NotAvailable({ reason }: { reason?: string }) {
  return (
    <Typography.Text type="secondary" italic>
      Not available{reason ? ` — ${reason}` : ''}
    </Typography.Text>
  );
}

function EvidenceBody({ incidentId }: { incidentId: string }) {
  const { state } = useAsyncData(() => getIncident(incidentId));
  return (
    <AsyncBody
      state={state}
      loadingText="Loading evidence…"
      errorTitle="Failed to load incident evidence"
    >
      {(detail) => {
        const evidence = detail.evidence;
        if (!evidence) {
          return (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="No evidence card recorded for this incident."
            />
          );
        }
        return (
          <Flex vertical gap={20}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Captured {formatTimestamp(evidence.generated_at)}
            </Typography.Text>
            <IncidentNarrativeSection incidentId={incidentId} />
            <CheckAssetSection check={evidence.check} asset={evidence.asset} />
            <FailingResultSection result={evidence.failing_result} />
            <MetricTrendSection trend={evidence.metric_trend} />
            <SiblingChecksSection siblings={evidence.sibling_checks} />
            <UpstreamPipelineSection pipeline={evidence.upstream_pipeline_run} />
            <BlastRadiusSection assets={evidence.downstream_blast_radius} />
            <ProfileDiffSection diff={evidence.profile_diff} />
          </Flex>
        );
      }}
    </AsyncBody>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <Typography.Title level={5} style={{ marginTop: 0, marginBottom: 8 }}>
        {title}
      </Typography.Title>
      {children}
    </div>
  );
}

/** The `<Descriptions>` shell shared by every layer that renders as a label/value grid — one label
 *  width so it can't drift per-section the way 140-vs-160 already had. */
function EvidenceDescriptions({ children }: { children: ReactNode }) {
  return (
    <Descriptions size="small" column={1} bordered styles={{ label: { width: 160 } }}>
      {children}
    </Descriptions>
  );
}

/** An asset reference — identifying text plus a navigable link, everywhere the card carries one. */
function AssetRef({ asset }: { asset: EvidenceAssetLayer }) {
  return (
    <Flex align="center" gap={8}>
      <Typography.Text>
        {asset.namespace}.{asset.name}{' '}
        <Typography.Text type="secondary">({asset.env})</Typography.Text>
      </Typography.Text>
      <AssetLink assetId={asset.id} />
    </Flex>
  );
}

function CheckAssetSection({
  check,
  asset,
}: {
  check: EvidenceCheckLayer | null;
  asset: EvidenceAssetLayer | null;
}) {
  return (
    <Section title="Check & asset">
      {!check && !asset ? (
        <NotAvailable />
      ) : (
        <EvidenceDescriptions>
          <Descriptions.Item label="Check">
            {check ? (check.name ?? check.id) : <NotAvailable />}
          </Descriptions.Item>
          <Descriptions.Item label="Expectation">
            {check?.expectation_type ?? <NotAvailable />}
          </Descriptions.Item>
          <Descriptions.Item label="Kind">{check?.kind ?? <NotAvailable />}</Descriptions.Item>
          <Descriptions.Item label="Asset">
            {asset ? <AssetRef asset={asset} /> : <NotAvailable />}
          </Descriptions.Item>
        </EvidenceDescriptions>
      )}
    </Section>
  );
}

function FailingResultSection({ result }: { result: EvidenceFailingResultLayer | null }) {
  return (
    <Section title="Failing result">
      {result ? (
        <EvidenceDescriptions>
          <Descriptions.Item label="Status">
            <Tag color={statusColor(result.status)}>{result.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Metric">{formatScalar(result.metric_value)}</Descriptions.Item>
          <Descriptions.Item label="Observed">
            {formatScalar(result.observed_value)}
          </Descriptions.Item>
          <Descriptions.Item label="Expected">
            {formatScalar(result.expected_value)}
          </Descriptions.Item>
        </EvidenceDescriptions>
      ) : (
        <NotAvailable />
      )}
    </Section>
  );
}

function MetricTrendSection({ trend }: { trend: EvidenceTrendPoint[] | null }) {
  const columns: ColumnsType<EvidenceTrendPoint> = [
    { title: 'When', dataIndex: 'created_at', render: (v: string | null) => formatTimestamp(v) },
    {
      title: 'Status',
      dataIndex: 'status',
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    {
      title: 'Metric',
      dataIndex: 'metric_value',
      render: (v: number | null) => formatScalar(v),
    },
  ];
  return (
    <Section title="Metric trend (last 10 readings)">
      {trend === null ? (
        <NotAvailable />
      ) : trend.length === 0 ? (
        <Typography.Text type="secondary">
          No prior readings recorded for this check.
        </Typography.Text>
      ) : (
        <Table<EvidenceTrendPoint>
          scroll={{ x: 'max-content' }}
          size="small"
          rowKey="run_id"
          pagination={false}
          columns={columns}
          dataSource={trend}
        />
      )}
    </Section>
  );
}

function SiblingChecksSection({ siblings }: { siblings: EvidenceSiblingCheck[] | null }) {
  const columns: ColumnsType<EvidenceSiblingCheck> = [
    { title: 'Check', dataIndex: 'check_name', render: (n: string | null) => formatScalar(n) },
    {
      title: 'Status',
      dataIndex: 'status',
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
  ];
  return (
    <Section title="Other checks in this run">
      {siblings === null ? (
        <NotAvailable />
      ) : siblings.length === 0 ? (
        <Typography.Text type="secondary">No other checks ran alongside this one.</Typography.Text>
      ) : (
        <Table<EvidenceSiblingCheck>
          scroll={{ x: 'max-content' }}
          size="small"
          rowKey={(_, index) => String(index)}
          pagination={false}
          columns={columns}
          dataSource={siblings}
        />
      )}
    </Section>
  );
}

function UpstreamPipelineSection({ pipeline }: { pipeline: EvidenceUpstreamPipelineRun | null }) {
  return (
    <Section title="Upstream pipeline run">
      {pipeline ? (
        <EvidenceDescriptions>
          <Descriptions.Item label="Provider">{pipeline.provider}</Descriptions.Item>
          <Descriptions.Item label="Pipeline / DAG">
            {pipeline.pipeline_or_dag_id}
          </Descriptions.Item>
          <Descriptions.Item label="Provider run">{pipeline.provider_run_id}</Descriptions.Item>
          <Descriptions.Item label="Status">{pipeline.status}</Descriptions.Item>
          <Descriptions.Item label="Started">
            {formatTimestamp(pipeline.started_at)}
          </Descriptions.Item>
          <Descriptions.Item label="Finished">
            {formatTimestamp(pipeline.finished_at)}
          </Descriptions.Item>
          <Descriptions.Item label="Duration">
            {pipeline.duration_seconds === null
              ? '—'
              : formatDurationMs(pipeline.duration_seconds * 1000)}
          </Descriptions.Item>
          <Descriptions.Item label="Delay vs. history">
            {pipeline.delay_seconds_vs_history === null ? (
              <NotAvailable reason="no completed prior run to compare against" />
            ) : (
              `${pipeline.delay_seconds_vs_history >= 0 ? '+' : ''}${Math.round(pipeline.delay_seconds_vs_history)}s`
            )}
          </Descriptions.Item>
        </EvidenceDescriptions>
      ) : (
        <NotAvailable reason="not triggered by a monitored pipeline, or the pipeline run couldn't be resolved" />
      )}
    </Section>
  );
}

function BlastRadiusSection({ assets }: { assets: EvidenceAssetLayer[] | null }) {
  return (
    <Section title="Downstream blast radius">
      {assets === null ? (
        <NotAvailable />
      ) : assets.length === 0 ? (
        <Typography.Text type="secondary">No downstream assets recorded.</Typography.Text>
      ) : (
        <Flex vertical gap={4}>
          {assets.map((a) => (
            <AssetRef key={a.id} asset={a} />
          ))}
        </Flex>
      )}
    </Section>
  );
}

function ProfileDiffSection({ diff }: { diff: unknown }) {
  return (
    <Section title="Profile diff">
      {diff === null || diff === undefined ? (
        <NotAvailable reason="not implemented yet — a live datasource profile diff of both batches" />
      ) : (
        <Typography.Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {JSON.stringify(diff, null, 2)}
        </Typography.Text>
      )}
    </Section>
  );
}

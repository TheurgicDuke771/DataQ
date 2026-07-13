import { ArrowRightOutlined, DatabaseOutlined } from '@ant-design/icons';
import { Card, Empty, Flex, Grid, Spin, Tag, Tooltip, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';

import { type AssetSummary, listAssets } from '../../api/assets';
import { AssetHealthTag } from '../assets/AssetHealthTag';
import { namespaceLabel } from '../assets/namespaceLabel';
import { useAsyncData } from '../../hooks/useAsyncData';
import { BRAND } from '../../theme';

/**
 * Asset-health lead (`/dashboard`, ADR 0034 navigation inversion, #773) — the
 * dashboard now *leads* with asset-level health: how many assets DataQ monitors,
 * how many need attention, how many have a run in flight. Everything is derived
 * from the existing `/assets` list read (no new endpoint) and filtered to the
 * caller's grants by the backend, so this leaks nothing.
 *
 * Click-through is preserved end to end: the tiles and "View all assets" go to
 * `/assets`; each attention row opens `/assets/:id` (→ its suites/runs).
 */

/** An asset "needs attention" when a check tier is failing (bad data) or DataQ
 *  couldn't execute against the datasource at all (#803 connection axis — a failed
 *  run, or a check the datasource threw on). An active-but-unconcluded run is *in
 *  progress*, not failing. `has_operational_error` subsumes `has_failed_run`, and
 *  additionally catches a run that *succeeded* while its checks errored — which the
 *  old run-status-only rule silently let read as healthy. */
function needsAttention(a: AssetSummary): boolean {
  return a.worst_severity !== null || a.has_operational_error;
}

/** Attention ordering: critical > fail > warn > operational run failure. The
 *  backend list arrives (namespace, name)-alphabetical, so without this a page
 *  of warns could hide a critical asset behind the "+N more" fold. */
const SEVERITY_RANK: Record<'warn' | 'fail' | 'critical', number> = {
  warn: 1,
  fail: 2,
  critical: 3,
};
function attentionRank(a: AssetSummary): number {
  return a.worst_severity ? SEVERITY_RANK[a.worst_severity] : 0;
}

/** How many attention rows to surface inline before deferring to the full list. */
const ATTENTION_PREVIEW = 5;

/** The backend caps `GET /assets` at 200 rows (its default == its max). A result
 *  exactly at the cap means the widget may be looking at a truncated slice — the
 *  counts must say so rather than silently undercount. */
const LIST_CAP = 200;

export function AssetHealthLead() {
  const navigate = useNavigate();
  const { state } = useAsyncData(() => listAssets());

  return (
    <Card
      size="small"
      styles={{ body: { paddingTop: 12 } }}
      title={
        <Flex align="center" gap={8}>
          <DatabaseOutlined style={{ color: BRAND.primary }} />
          <span>Asset health</span>
        </Flex>
      }
      extra={
        <Typography.Link onClick={() => navigate('/assets')}>
          View all assets <ArrowRightOutlined />
        </Typography.Link>
      }
    >
      {state.status === 'loading' && <Spin />}
      {state.status === 'error' && (
        <Typography.Text type="secondary">Asset health is unavailable right now.</Typography.Text>
      )}
      {state.status === 'ok' && (
        <AssetHealthBody
          assets={state.data}
          onOpenList={() => navigate('/assets')}
          onOpenAsset={(id) => navigate(`/assets/${id}`)}
        />
      )}
    </Card>
  );
}

function AssetHealthBody({
  assets,
  onOpenList,
  onOpenAsset,
}: {
  assets: AssetSummary[];
  onOpenList: () => void;
  onOpenAsset: (id: string) => void;
}) {
  const screens = Grid.useBreakpoint();
  const stacked = screens.sm === false;

  if (assets.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="No monitored assets yet — give a suite a run target and it will appear here."
      />
    );
  }

  // Worst first (stable within a tier — Array.prototype.sort is stable), so a
  // critical asset can never hide behind five alphabetically-earlier warns.
  const attention = assets
    .filter(needsAttention)
    .sort((x, y) => attentionRank(y) - attentionRank(x));
  const active = assets.filter((a) => !needsAttention(a) && a.has_active_run);
  const truncated = assets.length >= LIST_CAP;

  const tiles: { label: string; value: number; tone: string }[] = [
    { label: 'Monitored', value: assets.length, tone: BRAND.ink },
    {
      label: 'Need attention',
      value: attention.length,
      tone: attention.length ? '#cf1322' : BRAND.ink,
    },
    { label: 'In progress', value: active.length, tone: BRAND.ink },
  ];

  return (
    <Flex vertical gap={16}>
      {/* Summary strip — each tile is a click-through to the full assets list. */}
      <Flex gap={12} vertical={stacked}>
        {tiles.map((t) => (
          <Card
            key={t.label}
            size="small"
            className="dq-card--interactive"
            style={{ flex: 1, cursor: 'pointer' }}
            styles={{ body: { padding: '12px 16px' } }}
            onClick={onOpenList}
          >
            <Flex vertical gap={2}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t.label}
              </Typography.Text>
              <Typography.Text strong style={{ fontSize: 22, color: t.tone }}>
                {t.value}
              </Typography.Text>
            </Flex>
          </Card>
        ))}
      </Flex>

      {/* Honest truncation: at the cap we can't know how many assets exist
          beyond this slice, so the tiles above are a lower bound — say so
          explicitly instead of silently undercounting. */}
      {truncated && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Showing the first {LIST_CAP} assets — open Assets for the full list.
        </Typography.Text>
      )}

      {/* Attention list: the assets a data owner should look at first. When all
          is well, say so rather than showing an empty box. */}
      {attention.length === 0 ? (
        <Typography.Text type="secondary">All monitored assets are healthy.</Typography.Text>
      ) : (
        <Flex vertical gap={8}>
          <Typography.Text strong style={{ fontSize: 13 }}>
            Needs attention
          </Typography.Text>
          {attention.slice(0, ATTENTION_PREVIEW).map((a) => (
            <Flex
              key={a.id}
              className="dq-suite-row"
              justify="space-between"
              align="center"
              gap={12}
              wrap
              onClick={() => onOpenAsset(a.id)}
              style={{ cursor: 'pointer', padding: '6px 8px', borderRadius: 6 }}
            >
              <Flex vertical gap={0} style={{ minWidth: 0 }}>
                <Typography.Text strong ellipsis style={{ maxWidth: 360 }}>
                  {a.name}
                </Typography.Text>
                <Tooltip title={a.namespace}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                    {namespaceLabel(a.namespace)}
                  </Typography.Text>
                </Tooltip>
              </Flex>
              <Flex gap={8} align="center">
                {a.env && <Tag>{a.env}</Tag>}
                <AssetHealthTag summary={a} />
              </Flex>
            </Flex>
          ))}
          {attention.length > ATTENTION_PREVIEW && (
            <Typography.Link onClick={onOpenList}>
              +{attention.length - ATTENTION_PREVIEW} more
            </Typography.Link>
          )}
        </Flex>
      )}
    </Flex>
  );
}

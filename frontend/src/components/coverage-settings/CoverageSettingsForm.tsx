import { Alert, Button, Flex, InputNumber, Space, Switch, Tag, Typography } from 'antd';
import { useState } from 'react';

import type { CoverageDocument, EffectiveCoverage } from '../../api/coverage';
import {
  BASELINES,
  type CoverageDraft,
  documentOf,
  draftOf,
  invalidReason,
  isEmpty,
  LEVEL_LABEL,
  sensitivityFrom,
  shapeFrom,
  shortensWindow,
  TIERS,
} from './coverageDraft';

interface Props {
  /** What this level stores now (`null` or `{}` = nothing). */
  stored: CoverageDocument | null;
  /** What each group resolves to when this level does not set it. */
  below: EffectiveCoverage;
  /** What automatic checks run on now, with this level applied. */
  current: EffectiveCoverage;
  /** Names this level in the switches: "Set for the workspace" / "Set for this table". */
  scope: string;
  saving: boolean;
  onSave: (doc: CoverageDocument) => void;
  /** Remove everything this level sets. */
  onClear: () => void;
}

/**
 * Sensitivity for automatic baselines at one level (ADR 0047). Each group is either set here,
 * whole, or left to the level below, whose values are shown so the choice is informed.
 */
export function CoverageSettingsForm({
  stored,
  below,
  current,
  scope,
  saving,
  onSave,
  onClear,
}: Props) {
  const [edited, setEdited] = useState<CoverageDraft | null>(null);
  const saved = draftOf(stored);
  const draft = edited ?? saved;
  const reason = invalidReason(draft);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const set = (next: CoverageDraft) => setEdited(next);

  return (
    <Flex vertical gap={16}>
      {BASELINES.map(({ key, label }) => {
        const group = draft.sensitivity[key];
        const shown = group ?? below.sensitivity[key];
        return (
          <Flex key={key} vertical gap={6} data-testid={`coverage-${key}`}>
            <Flex align="center" gap={8} wrap>
              <Typography.Text strong>{label}</Typography.Text>
              <Switch
                size="small"
                checked={group !== null}
                disabled={saving}
                aria-label={`${scope}: ${label} thresholds`}
                onChange={(on) =>
                  set({
                    ...draft,
                    sensitivity: {
                      ...draft.sensitivity,
                      [key]: on ? sensitivityFrom(below, key) : null,
                    },
                  })
                }
              />
              <Typography.Text type="secondary">{scope}</Typography.Text>
              {group === null && <Tag>{LEVEL_LABEL[below.sensitivity[key].source]}</Tag>}
            </Flex>
            <Space wrap size="middle">
              {TIERS.map((tier) => (
                <Space key={tier.key} orientation="vertical" size={2}>
                  <Typography.Text type="secondary">{tier.label}</Typography.Text>
                  <InputNumber
                    aria-label={`${label} ${tier.label.toLowerCase()} deviations`}
                    min={0.1}
                    max={99}
                    step={0.5}
                    value={shown[tier.key]}
                    disabled={group === null || saving}
                    onChange={(v) =>
                      group &&
                      set({
                        ...draft,
                        sensitivity: { ...draft.sensitivity, [key]: { ...group, [tier.key]: v } },
                      })
                    }
                  />
                </Space>
              ))}
            </Space>
          </Flex>
        );
      })}

      <Flex vertical gap={6} data-testid="coverage-baseline">
        <Flex align="center" gap={8} wrap>
          <Typography.Text strong>History</Typography.Text>
          <Switch
            size="small"
            checked={draft.baseline !== null}
            disabled={saving}
            aria-label={`${scope}: history`}
            onChange={(on) => set({ ...draft, baseline: on ? shapeFrom(below) : null })}
          />
          <Typography.Text type="secondary">{scope}</Typography.Text>
          {draft.baseline === null && <Tag>{LEVEL_LABEL[below.baseline.source]}</Tag>}
        </Flex>
        <Space wrap size="middle" align="end">
          <Space orientation="vertical" size={2}>
            <Typography.Text type="secondary">Runs compared</Typography.Text>
            <InputNumber
              aria-label="Runs compared"
              min={3}
              max={90}
              precision={0}
              value={(draft.baseline ?? below.baseline).window}
              disabled={draft.baseline === null || saving}
              onChange={(v) =>
                draft.baseline && set({ ...draft, baseline: { ...draft.baseline, window: v } })
              }
            />
          </Space>
          <Space orientation="vertical" size={2}>
            <Typography.Text type="secondary">Runs before scoring</Typography.Text>
            <InputNumber
              aria-label="Runs before scoring"
              min={3}
              max={90}
              precision={0}
              value={(draft.baseline ?? below.baseline).min_points}
              disabled={draft.baseline === null || saving}
              onChange={(v) =>
                draft.baseline && set({ ...draft, baseline: { ...draft.baseline, min_points: v } })
              }
            />
          </Space>
          <Space size={8}>
            <Switch
              checked={(draft.baseline ?? below.baseline).seasonality}
              disabled={draft.baseline === null || saving}
              aria-label="Compare the same weekday only"
              onChange={(on) =>
                draft.baseline &&
                set({ ...draft, baseline: { ...draft.baseline, seasonality: on } })
              }
            />
            <Typography.Text>Compare the same weekday only</Typography.Text>
          </Space>
        </Space>
      </Flex>

      {reason && <Alert type="warning" showIcon title={reason} />}
      {!reason && dirty && shortensWindow(draft, current) && (
        <Alert
          type="warning"
          showIcon
          title="A shorter window trims history"
          description="Automatic checks drop their older observations on their next run. If the window is lengthened again later, they wait for that history to build back up before scoring."
        />
      )}
      <Space>
        <Button
          type="primary"
          loading={saving}
          disabled={!dirty || !!reason}
          onClick={() => {
            // A form that sets nothing is "remove what this level sets", not an empty save.
            if (isEmpty(draft)) onClear();
            else onSave(documentOf(draft));
            setEdited(null);
          }}
        >
          Save
        </Button>
        <Button
          disabled={isEmpty(saved) || saving}
          onClick={() => {
            onClear();
            setEdited(null);
          }}
        >
          {scope === 'Set for this table' ? 'Use the workspace setting' : 'Reset to defaults'}
        </Button>
      </Space>
    </Flex>
  );
}

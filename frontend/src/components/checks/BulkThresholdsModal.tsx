import { Alert, Flex, InputNumber, Modal, Select, Typography } from 'antd';
import { useState } from 'react';

import type { BulkThresholds } from '../../api/suites';

/**
 * Set the same severity thresholds on every selected check. Each tier is left as it is, set to
 * one number, or cleared — "leave" sends nothing for that tier, so each check keeps its own value.
 */

type Mode = 'keep' | 'set' | 'clear';
const TIERS = [
  { key: 'warn_threshold', label: 'Warn' },
  { key: 'fail_threshold', label: 'Fail' },
  { key: 'critical_threshold', label: 'Critical' },
] as const;
type TierKey = (typeof TIERS)[number]['key'];

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: 'keep', label: 'Leave as it is' },
  { value: 'set', label: 'Set to' },
  { value: 'clear', label: 'Clear' },
];

export function BulkThresholdsModal({
  open,
  count,
  onCancel,
  onApply,
}: {
  open: boolean;
  /** How many checks are selected. */
  count: number;
  onCancel: () => void;
  /** Rejects on failure so the modal stays open. */
  onApply: (thresholds: BulkThresholds) => Promise<void>;
}) {
  const [modes, setModes] = useState<Record<TierKey, Mode>>({
    warn_threshold: 'keep',
    fail_threshold: 'keep',
    critical_threshold: 'keep',
  });
  const [values, setValues] = useState<Record<TierKey, number | null>>({
    warn_threshold: null,
    fail_threshold: null,
    critical_threshold: null,
  });
  const [busy, setBusy] = useState(false);

  const changed = TIERS.filter((t) => modes[t.key] !== 'keep');
  const incomplete = TIERS.some((t) => modes[t.key] === 'set' && values[t.key] === null);
  const noun = `${count} check${count === 1 ? '' : 's'}`;

  const apply = async () => {
    const thresholds: BulkThresholds = {};
    for (const tier of changed) {
      thresholds[tier.key] = modes[tier.key] === 'clear' ? null : values[tier.key];
    }
    setBusy(true);
    try {
      await onApply(thresholds);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      title={`Set thresholds on ${noun}`}
      okText={`Apply to ${noun}`}
      okButtonProps={{ disabled: changed.length === 0 || incomplete }}
      confirmLoading={busy}
      onOk={() => void apply().catch(() => undefined)}
      onCancel={onCancel}
      destroyOnHidden
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          Every selected check gets the same value for each tier you change. A threshold is compared
          with the number that check measures, so select checks that measure the same thing.
        </Typography.Text>
        {TIERS.map((tier) => (
          <Flex key={tier.key} align="center" gap={8}>
            <Typography.Text style={{ width: 64 }} id={`bulk-${tier.key}-label`}>
              {tier.label}
            </Typography.Text>
            <Select<Mode>
              aria-labelledby={`bulk-${tier.key}-label`}
              style={{ width: 150 }}
              value={modes[tier.key]}
              options={MODE_OPTIONS}
              onChange={(mode) => setModes((prev) => ({ ...prev, [tier.key]: mode }))}
            />
            {modes[tier.key] === 'set' && (
              <InputNumber
                aria-label={`${tier.label} threshold`}
                min={0}
                value={values[tier.key]}
                onChange={(value) => setValues((prev) => ({ ...prev, [tier.key]: value }))}
              />
            )}
          </Flex>
        ))}
        <Alert
          type="info"
          showIcon
          title="All or nothing"
          description="If any selected check cannot take these thresholds, none is changed and the checks at fault are named."
        />
      </Flex>
    </Modal>
  );
}

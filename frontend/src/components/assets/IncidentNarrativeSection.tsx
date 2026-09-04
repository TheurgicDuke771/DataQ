import { Alert, Button, Flex, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';

import { getIncidentNarrative, type IncidentNarrativeRead } from '../../api/incidents';
import { generateRcaNarrative, type RcaNarrative, runLlmFeature } from '../../api/llm';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage, fetchFailure } from '../../utils/errors';
import { formatTimestamp } from '../results/resultsFormat';
import { AiCaveat } from '../shared/AiCaveat';
import SimpleList from '../SimpleList';

const CONFIDENCE_COLORS: Record<string, string> = {
  high: 'green',
  medium: 'gold',
  low: 'default',
};

type GenState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'failed'; error: string }
  | { status: 'unavailable'; reason: string };

/**
 * The root-cause narrative on an incident's evidence card (#1845, #1633). Shows the latest stored
 * narrative when this caller may read it, and lets them request a fresh one. Generation never
 * re-runs the check and never changes the suite.
 */
export function IncidentNarrativeSection({ incidentId }: { incidentId: string }) {
  const { state, reload } = useAsyncData(() => getIncidentNarrative(incidentId));
  const [fresh, setFresh] = useState<{ narrative: RcaNarrative; at: string } | null>(null);
  const [gen, setGen] = useState<GenState>({ status: 'idle' });
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const explain = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setGen({ status: 'running' });
    try {
      const row = await runLlmFeature<RcaNarrative>(() => generateRcaNarrative(incidentId), {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (row.status === 'succeeded' && row.response) {
        setFresh({ narrative: row.response, at: row.finished_at ?? row.created_at });
        setGen({ status: 'idle' });
        reload();
      } else {
        setGen({ status: 'failed', error: row.error ?? 'the narrative failed without a reason' });
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      const failure = fetchFailure(err);
      if (failure.status === 409) setGen({ status: 'unavailable', reason: failure.message });
      else setGen({ status: 'failed', error: errorMessage(err) });
    }
  };

  const stored: IncidentNarrativeRead | null = state.status === 'ok' ? state.data : null;
  const shown =
    fresh ??
    (stored?.narrative ? { narrative: stored.narrative, at: stored.generated_at ?? '' } : null);

  return (
    <div data-testid="incident-narrative">
      <Flex justify="space-between" align="center" wrap gap={8} style={{ marginBottom: 8 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          Root-cause narrative
        </Typography.Title>
        <Button
          size="small"
          type={shown ? 'default' : 'primary'}
          ghost={!shown}
          onClick={explain}
          loading={gen.status === 'running'}
          disabled={gen.status === 'unavailable'}
        >
          {shown ? 'Regenerate' : 'Explain this failure'}
        </Button>
      </Flex>
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        The model sees the stored evidence card — check and asset identifiers, statuses, metric
        values, and this check&apos;s observed/expected values after the same redaction every
        results surface applies — plus up to 180 points of result history. No column profile, no
        sample rows, and nothing is fetched fresh from the warehouse.
      </Typography.Text>
      {gen.status === 'running' && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Asking the model — a local model can take a minute…
        </Typography.Text>
      )}
      {gen.status === 'failed' && (
        <Alert type="error" showIcon title="No narrative" description={gen.error} />
      )}
      {gen.status === 'unavailable' && (
        <Alert
          type="info"
          showIcon
          title="AI narratives are not enabled on this workspace"
          description={gen.reason}
        />
      )}
      {shown ? (
        <NarrativeBody narrative={shown.narrative} at={shown.at} />
      ) : (
        gen.status === 'idle' && (
          <Typography.Text type="secondary" italic>
            {stored?.withheld_reason
              ? `A narrative exists but is not shown to you — ${stored.withheld_reason}.`
              : 'None generated yet — narratives are on demand, never automatic.'}
          </Typography.Text>
        )
      )}
    </div>
  );
}

function NarrativeBody({ narrative, at }: { narrative: RcaNarrative; at: string }) {
  return (
    <Flex vertical gap={10}>
      <Typography.Paragraph style={{ margin: 0 }}>{narrative.summary}</Typography.Paragraph>
      {narrative.ranked_hypotheses.length > 0 && (
        <div>
          <Typography.Text strong>Ranked hypotheses</Typography.Text>
          <SimpleList
            size="small"
            dataSource={narrative.ranked_hypotheses}
            renderItem={(h, i) => (
              <SimpleList.Item>
                <Flex vertical gap={4}>
                  <span>
                    {i + 1}. {h.cause}
                  </span>
                  <Flex gap={4} wrap>
                    <Tag color={CONFIDENCE_COLORS[h.confidence] ?? 'default'}>{h.confidence}</Tag>
                    {h.evidence_refs.map((ref) => (
                      <Tag key={ref}>{ref}</Tag>
                    ))}
                  </Flex>
                </Flex>
              </SimpleList.Item>
            )}
          />
        </div>
      )}
      {narrative.blind_spots.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="What this evidence could not see"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {narrative.blind_spots.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          }
        />
      )}
      {narrative.suggested_next_checks && narrative.suggested_next_checks.length > 0 && (
        <div>
          <Typography.Text strong>Suggested next steps</Typography.Text>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {narrative.suggested_next_checks.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Generated {at ? formatTimestamp(at) : '—'} · computed from the stored evidence and the
        check&apos;s result history; hypotheses cite evidence layers, blind spots are DataQ&apos;s.
      </Typography.Text>
      <AiCaveat />
    </Flex>
  );
}

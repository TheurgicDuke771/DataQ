import { Alert, Card, Empty, Flex, Progress, Space, Tag, Tooltip, Typography } from 'antd';

import type { Scorecard } from '../../api/assets';

/**
 * The asset DQ scorecard (#889) — per-dimension coverage and score.
 *
 * **Coverage is the point, not the score.** "This asset has no Timeliness checks
 * at all" is what a data lead acts on; a pass-rate never says that. So the
 * uncovered dimensions are given equal billing, visually distinct from a covered
 * dimension that is merely failing — those are different problems with different
 * fixes (write a check vs. fix the data).
 *
 * Three empty states that must never be conflated:
 *  - **no checks at all** → "no coverage", never a green 100;
 *  - **checks exist but none evaluated** (all skip/error) → "no signal", score
 *    hidden rather than shown as 0;
 *  - **checks evaluated** → a real score.
 */

const DIMENSION_HELP: Record<string, string> = {
  accuracy: 'Does the data match reality / a trusted source?',
  completeness: 'Is all the expected data present?',
  consistency: 'Do related datasets agree with each other?',
  integrity: 'Do relationships between datasets hold?',
  timeliness: 'Is the data recent enough?',
  uniqueness: 'Are there unexpected duplicates?',
  validity: 'Does the data conform to its rules and formats?',
};

const titleCase = (s: string) => `${s.charAt(0).toUpperCase()}${s.slice(1)}`;

/** ADR-0005 bands, matching the dashboard's performance states. */
function scoreColour(score: number): string {
  if (score >= 90) return '#52c41a';
  if (score >= 60) return '#faad14';
  return '#ff4d4f';
}

export function ScorecardPanel({ scorecard }: { scorecard?: Scorecard | null }) {
  // Absent (a pre-#889 API) is not the same as empty — render nothing rather
  // than an authoritative-looking "no coverage" we can't actually vouch for.
  if (!scorecard) return null;

  const { covered, uncovered, unclassified_checks: unclassified } = scorecard;
  // "No checks at all" is narrower than "nothing covered": an asset whose only
  // checks are unclassified (custom SQL) has checks, they just aren't bucketed.
  // Telling that user "no checks are classified yet" is right; telling the
  // check-less user the same thing is wrong — they have nothing to classify.
  const noChecksAtAll = covered.length === 0 && unclassified === 0;

  return (
    <Card
      title="Data quality by dimension"
      size="small"
      styles={{ body: { paddingTop: 12 } }}
      data-testid="scorecard-panel"
    >
      {covered.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            noChecksAtAll
              ? 'No checks on this asset yet — every dimension is uncovered.'
              : 'No checks here carry a dimension yet, so there is nothing to score.'
          }
        />
      ) : (
        <Space orientation="vertical" size={10} style={{ width: '100%' }}>
          {covered.map((row) => (
            <Flex key={row.dimension} align="center" gap={12}>
              <Tooltip title={DIMENSION_HELP[row.dimension]}>
                <Typography.Text style={{ width: 110 }}>{titleCase(row.dimension)}</Typography.Text>
              </Tooltip>
              {row.score === null ? (
                // Checks EXIST but none evaluated (all skip/error). Showing 0
                // here would read as "everything failed" — the opposite fact.
                <Tag>No signal</Tag>
              ) : (
                <Progress
                  percent={row.score}
                  size="small"
                  strokeColor={scoreColour(row.score)}
                  format={(p) => `${p}%`}
                  style={{ flex: 1, marginBottom: 0 }}
                />
              )}
              <Tooltip
                title={
                  row.checks_evaluated < row.checks_total
                    ? `${row.checks_total - row.checks_evaluated} of ${row.checks_total} did not evaluate (not yet run, skipped, or errored) and are excluded from the score`
                    : undefined
                }
              >
                <Typography.Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
                  {row.checks_passing}/{row.checks_total} passing
                </Typography.Text>
              </Tooltip>
            </Flex>
          ))}
        </Space>
      )}

      {/* Always shown when non-empty. Suppressing it when nothing is covered hid
          the list in the MAXIMALLY actionable state — and since every pre-ADR-0038
          asset has an empty `covered`, that was the default rendering. */}
      {uncovered.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 14 }}
          message="Not covered"
          description={
            <Space size={[6, 6]} wrap>
              {uncovered.map((d) => (
                <Tooltip key={d} title={DIMENSION_HELP[d]}>
                  <Tag>{titleCase(d)}</Tag>
                </Tooltip>
              ))}
            </Space>
          }
        />
      )}

      {unclassified > 0 && (
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          {unclassified} check{unclassified === 1 ? '' : 's'} {unclassified === 1 ? 'has' : 'have'}{' '}
          no dimension set, so {unclassified === 1 ? 'it is' : 'they are'} not counted above.
        </Typography.Paragraph>
      )}
    </Card>
  );
}

import { Typography } from 'antd';

/**
 * The one generic reliability caveat every LLM-generated surface shows, kept in one place so the
 * three call sites (SQL generation, check suggestions, the RCA narrative) can't drift apart on
 * the wording — this is deliberately separate from each surface's own data-egress disclosure,
 * which says what the model is SENT; this says the opposite direction: the model's OUTPUT can be
 * wrong and needs a human's judgment before it is trusted.
 */
export function AiCaveat() {
  return (
    <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
      AI-generated — it can be wrong. Review before you rely on it.
    </Typography.Text>
  );
}

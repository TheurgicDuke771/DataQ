import { Button } from 'antd';

import { BrandMark } from '../components/BrandMark';
import { RESULT_STATUS_CHART_COLORS } from '../components/charts/chartTheme';
import { BRAND } from '../theme';

/**
 * The DataQ sign-in page. Grounded in the product's own subject — data quality
 * is the comparison of *expected* vs *observed*, banded by severity — so the
 * brand panel carries a small "expected → observed" ledger as its signature
 * element rather than generic marketing. Reuses the app's indigo + Inter system
 * and the shared balance mark, so the page reads as DataQ, not a template.
 *
 * Pure presentation: the parent (AuthGate) owns the MSAL `loginRedirect`; this
 * component only renders the panel and reports the click + redirect-in-progress.
 */
export function LoginPage({ onSignIn, signingIn }: { onSignIn: () => void; signingIn: boolean }) {
  return (
    <div className="dqlogin-root">
      <style>{LOGIN_CSS}</style>

      {/* Brand panel — the product's thesis + a live-feeling check ledger. */}
      <section className="dqlogin-brand" aria-labelledby="dqlogin-thesis">
        <div className="dqlogin-brand-inner">
          <div className="dqlogin-wordmark">
            <BrandMark size={34} />
            <span className="dqlogin-wordmark-text">DataQ</span>
          </div>

          <p className="dqlogin-eyebrow">Data quality monitoring</p>
          <h1 id="dqlogin-thesis" className="dqlogin-thesis">
            Prove your data
            <br />
            is right.
          </h1>
          <p className="dqlogin-sub">
            Expectations across Snowflake, Databricks, and your lakes — every run scored expected
            against observed, the moment it lands.
          </p>

          <div className="dqlogin-ledger" aria-hidden="true">
            <div className="dqlogin-ledger-head">
              <span>Check</span>
              <span>Expected</span>
              <span>Observed</span>
            </div>
            {LEDGER_ROWS.map((r, i) => (
              <div
                key={r.check}
                className="dqlogin-ledger-row"
                style={{ animationDelay: `${0.15 + i * 0.12}s` }}
              >
                <span className="dqlogin-check">{r.check}</span>
                <span className="dqlogin-mono dqlogin-expected">{r.expected}</span>
                <span className="dqlogin-mono dqlogin-observed">
                  <i
                    className="dqlogin-dot"
                    style={{ background: RESULT_STATUS_CHART_COLORS[r.severity] }}
                  />
                  {r.observed}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Action panel — the one job: sign in. */}
      <section className="dqlogin-action">
        <div className="dqlogin-card">
          <div className="dqlogin-card-mark">
            <BrandMark size={40} />
          </div>
          <h2 className="dqlogin-card-title">Sign in to DataQ</h2>
          <p className="dqlogin-card-sub">Use your organisation Microsoft account to continue.</p>

          <Button
            type="primary"
            size="large"
            block
            loading={signingIn}
            onClick={onSignIn}
            className="dqlogin-btn"
            icon={signingIn ? undefined : <MicrosoftLogo />}
          >
            {signingIn ? 'Opening Microsoft sign-in…' : 'Sign in with Microsoft'}
          </Button>

          <p className="dqlogin-foot">
            Single sign-on, secured by your tenant. Access is granted per suite by a workspace
            admin.
          </p>
        </div>
      </section>
    </div>
  );
}

/**
 * Illustrative check ledger (decorative, aria-hidden) — three real-shaped DQ
 * monitors that show the expected→observed comparison and that DataQ catches
 * failures, not just greens. Severity dots reuse the app-wide status palette
 * (RESULT_STATUS_CHART_COLORS) so they match the dashboard/results charts.
 */
const LEDGER_ROWS: {
  check: string;
  expected: string;
  observed: string;
  severity: keyof typeof RESULT_STATUS_CHART_COLORS;
}[] = [
  { check: 'orders.id unique', expected: '0 dupes', observed: '0', severity: 'pass' },
  { check: 'payments ↔ order_total', expected: 'match', observed: '2 off', severity: 'warn' },
  { check: 'inventory freshness', expected: '< 24h', observed: '31h', severity: 'fail' },
];

/** The Microsoft four-square logo (brand colours), sized to sit in the button. */
function MicrosoftLogo() {
  return (
    <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true" focusable="false">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

// Deep-indigo extensions of the BRAND.primary scale for the panel canvas; the
// rest of the palette comes straight from the shared theme tokens.
const PANEL_FROM = '#312e81'; // indigo-900
const PANEL_TO = '#1e1b4b'; // indigo-950

const LOGIN_CSS = `
.dqlogin-root {
  display: flex;
  min-height: 100vh;
  background: #ffffff;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Brand panel */
.dqlogin-brand {
  flex: 1 1 55%;
  position: relative;
  overflow: hidden;
  color: #ffffff;
  background:
    radial-gradient(120% 90% at 100% 0%, rgba(99,102,241,0.45) 0%, rgba(99,102,241,0) 55%),
    linear-gradient(155deg, ${PANEL_FROM} 0%, ${PANEL_TO} 100%);
  display: flex;
  align-items: center;
}
.dqlogin-brand-inner {
  width: 100%;
  max-width: 520px;
  margin: 0 auto;
  padding: 56px 8% 56px clamp(40px, 9vw, 110px);
}
.dqlogin-wordmark { display: flex; align-items: center; gap: 12px; margin-bottom: 56px; }
.dqlogin-wordmark-text { font-size: 22px; font-weight: 700; letter-spacing: -0.01em; }
.dqlogin-eyebrow {
  margin: 0 0 14px;
  font-size: 12px; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase;
  color: ${BRAND.primarySoft};
}
.dqlogin-thesis {
  margin: 0 0 20px;
  font-size: clamp(34px, 4.4vw, 50px); line-height: 1.04; font-weight: 700;
  letter-spacing: -0.025em; color: #ffffff;
}
.dqlogin-sub {
  margin: 0 0 44px; max-width: 30em;
  font-size: 15.5px; line-height: 1.6; color: rgba(226,232,255,0.78);
}

/* Signature: expected → observed ledger */
.dqlogin-ledger {
  border: 1px solid rgba(199,210,254,0.18);
  border-radius: 14px;
  background: rgba(255,255,255,0.04);
  backdrop-filter: blur(2px);
  padding: 6px 8px;
  max-width: 440px;
}
.dqlogin-ledger-head, .dqlogin-ledger-row {
  display: grid; grid-template-columns: 1.5fr 1fr 1fr; align-items: center;
  gap: 12px; padding: 11px 14px;
}
.dqlogin-ledger-head {
  font-size: 10.5px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase;
  color: rgba(199,210,254,0.6);
}
.dqlogin-ledger-row { border-top: 1px solid rgba(199,210,254,0.1); }
.dqlogin-check { font-size: 13.5px; font-weight: 500; color: rgba(241,245,255,0.92); }
.dqlogin-mono {
  font-family: ui-monospace, 'SFMono-Regular', 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
}
.dqlogin-expected { color: rgba(199,210,254,0.72); }
.dqlogin-observed { color: #ffffff; display: inline-flex; align-items: center; gap: 8px; }
.dqlogin-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; display: inline-block; }

/* Action panel */
.dqlogin-action {
  flex: 1 1 45%;
  display: flex; align-items: center; justify-content: center;
  padding: 40px 24px;
}
.dqlogin-card { width: 100%; max-width: 360px; text-align: center; }
.dqlogin-card-mark { display: none; margin-bottom: 18px; }
.dqlogin-card-title {
  margin: 0 0 8px; font-size: 24px; font-weight: 700; letter-spacing: -0.02em; color: ${BRAND.ink};
}
.dqlogin-card-sub { margin: 0 0 28px; font-size: 14.5px; color: #6b7280; line-height: 1.55; }
.dqlogin-btn { height: 46px; font-weight: 600; }
.dqlogin-foot { margin: 22px 0 0; font-size: 12.5px; line-height: 1.5; color: #9098a4; }

/* Motion — staggered reveal. Rows rest fully visible (opacity:1); the animation
   only eases them IN, via 'both' fill so the from-state covers the delay and the
   to-state persists. So if motion is reduced or the animation never runs, the
   content is still visible — never stuck transparent. */
@media (prefers-reduced-motion: no-preference) {
  .dqlogin-ledger-row { animation: dqlogin-rise 0.5s ease both; }
  @keyframes dqlogin-rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
}

/* Responsive — stack the panel above the card on narrow screens. */
@media (max-width: 860px) {
  .dqlogin-root { flex-direction: column; }
  .dqlogin-brand { flex: none; }
  .dqlogin-brand-inner { padding: 40px 28px; max-width: none; }
  .dqlogin-wordmark { margin-bottom: 28px; }
  .dqlogin-sub { margin-bottom: 0; }
  .dqlogin-ledger { display: none; }
  .dqlogin-card-mark { display: flex; justify-content: center; }
}
`;

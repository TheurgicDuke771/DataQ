import { Button, Result } from 'antd';
import { Link } from 'react-router-dom';

/**
 * The 403 page. Server-driven: shown when an endpoint returns 403 (the
 * workspace-admin gate), not a client-side role guess — so a non-admin who
 * deep-links to /admin still hits the API and lands here.
 */
export function Forbidden({
  message = "You don't have access to this page.",
}: {
  message?: string;
}) {
  return (
    <Result
      status="403"
      title="403 — Forbidden"
      subTitle={message}
      extra={
        <Link to="/">
          <Button type="primary">Back to app</Button>
        </Link>
      }
    />
  );
}

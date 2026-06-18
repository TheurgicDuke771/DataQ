import { Button, Result } from 'antd';
import { Link } from 'react-router-dom';

/**
 * The 403 page. Rendered by a page when the server's own determination denies
 * access — e.g. the Admin page shows it when `/me`'s `is_workspace_admin` is
 * false (a server-computed flag, not a client-side role guess), so a non-admin
 * who deep-links to /admin lands here rather than seeing admin UI.
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

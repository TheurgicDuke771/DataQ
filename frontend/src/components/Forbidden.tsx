import { ErrorState } from './feedback/ErrorState';

/**
 * The 403 page. Rendered by a page when the server's own determination denies
 * access — e.g. the Admin page shows it when `/me`'s `is_workspace_admin` is
 * false (a server-computed flag, not a client-side role guess), so a non-admin
 * who deep-links to /admin lands here rather than seeing admin UI.
 *
 * Thin wrapper over the shared `ErrorState` catalog so 403 reads identically to
 * the other error pages; callers keep passing a specific `message`.
 */
export function Forbidden({
  message = "You don't have access to this page.",
}: {
  message?: string;
}) {
  return <ErrorState code={403} message={message} />;
}

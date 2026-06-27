import { ErrorState } from '../components/feedback/ErrorState';

/** The router catch-all (`*`) — an in-brand 404 instead of a silent redirect. */
export function NotFound() {
  return <ErrorState code={404} />;
}

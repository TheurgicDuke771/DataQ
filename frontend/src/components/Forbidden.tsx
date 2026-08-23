import { ErrorState } from './feedback/ErrorState';

/** The 403 page. */
export function Forbidden({
  message = "You don't have access to this page.",
}: {
  message?: string;
}) {
  return <ErrorState code={403} message={message} />;
}

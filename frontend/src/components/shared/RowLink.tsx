import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** The keyboard + screen-reader path into a row whose `onRow` click navigates: a real link in the
 *  primary cell. It stops the click so the row handler does not navigate a second time (two
 *  history entries for one click). */
export function RowLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="dq-row-link" onClick={(e) => e.stopPropagation()}>
      {children}
    </Link>
  );
}

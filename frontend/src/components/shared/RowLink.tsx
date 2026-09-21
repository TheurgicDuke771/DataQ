import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** The keyboard + screen-reader path into a row or card whose own click navigates: a real link
 *  around the primary text. It stops the click so the container's handler does not navigate a
 *  second time (two history entries for one click).
 *
 *  `block` is for truncating text: the link wraps the ellipsis element rather than sitting inside
 *  it, because `overflow: hidden` on an ancestor clips the focus ring. */
export function RowLink({
  to,
  children,
  block = false,
  current = false,
}: {
  to: string;
  children: ReactNode;
  block?: boolean;
  current?: boolean;
}) {
  return (
    <Link
      to={to}
      className={block ? 'dq-row-link dq-row-link--block' : 'dq-row-link'}
      aria-current={current ? 'page' : undefined}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </Link>
  );
}

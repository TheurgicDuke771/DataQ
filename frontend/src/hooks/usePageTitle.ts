import { useEffect } from 'react';

/** Titles the document after the record a page has loaded, and puts the path's title back when
 *  the page goes. `null` (still loading, or failed) leaves the path's title alone. */
export function usePageTitle(title: string | null | undefined): void {
  useEffect(() => {
    if (!title) return;
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}

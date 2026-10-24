/** Published docs site (MkDocs Material → GitHub Pages). */
export const DOCS_URL = 'https://theurgicduke771.github.io/DataQ/docs/latest/';

/** Absolute URL of a published docs page, from its site-relative path (`guides/notifications/`). */
export function docsUrl(path: string): string {
  return `${DOCS_URL}${path.replace(/^\//, '')}`;
}

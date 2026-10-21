import { type ColumnTarget, targetString } from '../../api/suites';
import { asFileFormat } from '../suites/suiteTarget';

/** Pull the datasource-shaped identity out of the suite target, or null when no
 *  profilable target is set (the backend needs a `table` or a `path`). */
export function extractProfileTarget(target: Record<string, unknown> | null): ColumnTarget | null {
  const table = targetString(target, 'table');
  const path = targetString(target, 'path');
  if (!table && !path) return null;
  return {
    table,
    schema: targetString(target, 'schema'),
    catalog: targetString(target, 'catalog'),
    // Iceberg addresses `namespace.table`; the namespace rides alongside table.
    namespace: targetString(target, 'namespace'),
    path,
    file_format: asFileFormat(target?.file_format),
  };
}

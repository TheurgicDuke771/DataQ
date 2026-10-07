import { api } from '../api/client';
import { createDemoAdapter } from './adapter';
import type { FixtureBundle } from './fixtures';

export const FIXTURES_FILE = 'demo-fixtures.json';

/** Point the shared API client at the recorded responses published beside the bundle. */
export function installDemo(baseUrl: string = import.meta.env.BASE_URL): void {
  let bundle: Promise<FixtureBundle> | undefined;
  const load = () => {
    bundle ??= fetch(`${baseUrl}${FIXTURES_FILE}`).then((res) => {
      if (!res.ok) throw new Error(`demo fixtures failed to load (${res.status})`);
      return res.json() as Promise<FixtureBundle>;
    });
    return bundle;
  };
  api.defaults.adapter = createDemoAdapter(load);
}

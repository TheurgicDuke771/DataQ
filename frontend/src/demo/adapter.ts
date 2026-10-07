import axios, {
  AxiosError,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios';

import { daysSince, lookup, shiftDates, type FixtureBundle } from './fixtures';
import { announce } from './notices';

export const READ_ONLY_MESSAGE =
  'This is a read-only demo. Install DataQ to run it against your own data.';
export const NOT_RECORDED_MESSAGE = 'This view is not part of the read-only demo.';

function respond(
  config: InternalAxiosRequestConfig,
  status: number,
  data: unknown,
  headers: Record<string, string> = {},
): Promise<AxiosResponse> {
  const response: AxiosResponse = {
    data,
    status,
    statusText: '',
    headers: { ...headers, 'content-type': 'application/json' },
    config,
    request: {},
  };
  const ok = config.validateStatus ? config.validateStatus(status) : status >= 200 && status < 300;
  if (ok) return Promise.resolve(response);
  return Promise.reject(
    new AxiosError(
      `Request failed with status code ${status}`,
      status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST,
      config,
      response.request,
      response,
    ),
  );
}

const envelope = (code: string, message: string) => ({ error: { code, message } });

/**
 * An axios adapter that answers from recorded responses and never touches the network. Reads
 * replay the recording; anything that would change state is refused.
 */
export function createDemoAdapter(load: () => Promise<FixtureBundle>): AxiosAdapter {
  return async (config) => {
    const method = (config.method ?? 'get').toUpperCase();
    if (method !== 'GET') {
      announce('read-only');
      return respond(config, 403, envelope('demo_read_only', READ_ONLY_MESSAGE));
    }
    const bundle = await load();
    const found = lookup(bundle, method, axios.getUri(config));
    if (!found) {
      announce('not-recorded');
      return respond(config, 404, envelope('demo_not_recorded', NOT_RECORDED_MESSAGE));
    }
    if (found.approximate) announce('approximate');
    const { status, data, headers } = found.response;
    return respond(config, status, shiftDates(data, daysSince(bundle.capturedAt)), headers);
  };
}

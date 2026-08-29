import { api } from './client';

/** Admin-only outbound-LLM provider config (issue #1511, ADR 0042). */

export type LlmProvider = 'anthropic' | 'openai_compatible';
export type StructuredOutputMode = 'native' | 'prompt_json';

export interface LlmConfig {
  configured: boolean;
  provider: LlmProvider | null;
  base_url: string | null;
  model: string | null;
  structured_output: StructuredOutputMode | null;
  enabled: boolean;
  /** Whether a credential is stored — the key itself is write-only, never returned. */
  has_credential: boolean;
  updated_at: string | null;
}

export interface LlmConfigUpdate {
  provider: LlmProvider;
  model: string;
  base_url?: string;
  /** Write-only. Omit to keep the stored key — except a provider/base_url change,
   *  which the backend refuses (422 `llm_config_invalid`) unless re-supplied. */
  api_key?: string;
  structured_output: StructuredOutputMode;
  enabled: boolean;
}

export type LlmTestErrorCode =
  | 'llm_provider_unavailable'
  | 'llm_provider_error'
  | 'llm_config_invalid'
  | 'llm_credential_missing'
  | 'secret_store_unavailable';

/** Result of a live probe — persists nothing. */
export interface LlmTestResult {
  ok: boolean;
  model?: string;
  latency_ms?: number;
  reply_chars?: number;
  error_code?: LlmTestErrorCode;
  error?: string;
}

export async function getLlmConfig(): Promise<LlmConfig> {
  const { data } = await api.get<LlmConfig>('/admin/llm');
  return data;
}

export async function updateLlmConfig(update: LlmConfigUpdate): Promise<LlmConfig> {
  const { data } = await api.put<LlmConfig>('/admin/llm', update);
  return data;
}

export async function testLlmConfig(update: LlmConfigUpdate): Promise<LlmTestResult> {
  const { data } = await api.post<LlmTestResult>('/admin/llm/test', update);
  return data;
}

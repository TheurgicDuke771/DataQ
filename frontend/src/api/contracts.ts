import { api } from './client';

/** A clause DataQ cannot enforce, and why — shown as "not enforced", never dropped. */
export interface UnmappedClause {
  object: string | null;
  property: string | null;
  rule_id: string | null;
  field: string;
  reason: string;
  clause: Record<string, unknown>;
}

export interface ContractSuite {
  id: string;
  name: string;
  /** The ODCS schema object this suite enforces. */
  contract_object: string | null;
  /** False when the object's name did not resolve to a table: the suite cannot run yet. */
  runnable: boolean;
}

export interface Contract {
  id: string;
  /** The document's own id — the identity across versions. */
  odcs_id: string;
  name: string;
  version: string;
  status: string | null;
  domain: string | null;
  tenant: string | null;
  api_version: string;
  created_at: string;
  /** The newest imported version of this `odcs_id`. */
  latest_version: boolean;
  suites: ContractSuite[];
  /** Linked suites the caller cannot see — counted, never named. */
  restricted_suite_count: number;
  unmapped: UnmappedClause[];
}

export type ClauseState =
  'pass' | 'warn' | 'fail' | 'critical' | 'unverified' | 'not_run' | 'disabled' | 'missing';

export interface ClauseStateRow {
  suite_id: string;
  /** Null for a `missing` clause — the suite has no check for it. */
  check_id: string | null;
  check_name: string | null;
  clause: {
    object?: string | null;
    property?: string | null;
    rule_id?: string | null;
    field?: string;
  };
  state: ClauseState;
  /** Edited since import; null when that cannot be told. */
  drifted: boolean | null;
}

export interface SuiteCompliance {
  suite_id: string;
  last_run_id: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
  /** True only for a completed (succeeded) run; otherwise the states are partial. */
  results_final: boolean;
}

export interface ContractDetail extends Contract {
  compliance: {
    clauses: ClauseStateRow[];
    suites: SuiteCompliance[];
    counts: Record<ClauseState, number>;
    not_enforced: number;
  };
}

export interface ContractImportResult {
  contract: Contract;
  created_suite_ids: string[];
  reconciled_suite_ids: string[];
  retired_suite_ids: string[];
  untargeted_objects: string[];
  checks_added: number;
  checks_updated: number;
  checks_disabled: number;
  warnings: string[];
}

export async function listContracts(signal?: AbortSignal): Promise<Contract[]> {
  const { data } = await api.get<Contract[]>('/contracts', { signal });
  return data;
}

export async function getContract(id: string, signal?: AbortSignal): Promise<ContractDetail> {
  const { data } = await api.get<ContractDetail>(`/contracts/${id}`, { signal });
  return data;
}

/** Import an ODCS document (YAML text) onto a connection — one suite per schema object. */
export async function importContract(body: {
  connection_id: string;
  document_yaml: string;
  suite_ids?: string[];
  replace?: boolean;
}): Promise<ContractImportResult> {
  const { data } = await api.post<ContractImportResult>('/contracts/import', body);
  return data;
}

/** The contract as an ODCS YAML document. */
export async function exportContractYaml(id: string): Promise<string> {
  const { data } = await api.get<string>(`/contracts/${id}/export`, {
    params: { format: 'yaml' },
    responseType: 'text',
  });
  return data;
}

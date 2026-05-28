import { api } from './client';

export interface MeResponse {
  id: string;
  aad_object_id: string;
  email: string;
  display_name: string | null;
  last_seen_at: string | null;
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>('/me');
  return data;
}

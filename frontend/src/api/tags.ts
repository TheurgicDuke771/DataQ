import { api } from './client';

/** Tags API — the workspace-shared label vocabulary. Assignments ride on each entity's PATCH. */

/** Mirrors `TagRead`. `usage` is workspace-wide (counts suites the caller cannot open). */
export interface Tag {
  id: string;
  /** Stored normalized: lower-case, trimmed, single-spaced. */
  name: string;
  created_at: string;
  usage: { suites: number; assets: number; connections: number; checks: number };
}

export async function listTags(): Promise<Tag[]> {
  const { data } = await api.get<Tag[]>('/tags');
  return data;
}

/** Admin-only: removes the tag from the vocabulary and detaches it everywhere. */
export async function deleteTag(id: string): Promise<void> {
  await api.delete(`/tags/${id}`);
}

/** The server's stored form of a name — shown in the picker so what you type is what you get. */
export function normalizeTagName(raw: string): string {
  return raw.normalize('NFC').toLowerCase().trim().split(/\s+/).filter(Boolean).join(' ');
}

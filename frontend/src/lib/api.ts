/**
 * Typed client for the egraph API.
 *
 * Everything goes through the same-origin `/api` prefix, which nginx (in production) and
 * the Vite dev server (locally) both proxy to FastAPI. No base URL is ever baked into the
 * bundle, so one built image runs anywhere.
 */

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData must NOT carry an explicit Content-Type: the browser sets it along with the
  // multipart boundary, and overriding it makes the server unable to parse the body.
  const isForm = init?.body instanceof FormData;
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: isForm
      ? (init?.headers ?? {})
      : { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    const message =
      typeof detail === "object" && detail !== null && "detail" in detail
        ? String((detail as { detail: unknown }).detail)
        : `${response.status} ${response.statusText}`;
    throw new ApiError(message, response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------- types
export interface GCSummary {
  relations_expired: number;
  relations_weakened: number;
  entities_removed: number;
  chunks_removed: number;
  communities_marked_dirty: number;
  removed_entity_ids: string[];
  expired_relation_ids: string[];
}

export interface GraphMutation {
  kind: string;
  payload: Record<string, unknown>;
  provenance: string[];
  affected_communities: string[];
  at: string;
}

export interface ChurnResult {
  doc_id: string;
  op: "add" | "update" | "delete" | "noop";
  status: string;
  mutations: GraphMutation[];
  dirty_communities: string[];
  gc_summary: GCSummary | null;
  chunks_added: number;
  chunks_reused: number;
  chunks_removed: number;
  jobs_enqueued: string[];
  sync_wall_ms: number;
}

export interface UploadResult {
  ingested: number;
  rejected: number;
  results: ChurnResult[];
  rejections: { filename: string; reason: string }[];
}

export interface Answer {
  text: string;
  citations: string[];
  used_communities: string[];
  used_entities: string[];
  stale_communities_touched: number;
  mode: string;
  latency_ms: number;
  tokens: number;
}

export interface Staleness {
  dirty_count: number;
  community_count: number;
  dirty_fraction: number;
  oldest_dirty_age_s: number;
  queue_depth: number;
}

export interface IndexStats {
  documents: number;
  active_documents: number;
  chunks: number;
  entities: number;
  relations: number;
  communities: number;
  dirty_communities: number;
  total_tokens: number;
  total_usd: number;
}

export interface DocumentRow {
  doc_id: string;
  uri: string;
  status: "active" | "deleted";
  version: number;
  content_hash: string;
  updated_at: string;
  chunks: number;
}

/** The detail view returns the chunks themselves where the list view returns a count. */
export interface DocumentDetail extends Omit<DocumentRow, "chunks"> {
  text: string;
  chunks: { chunk_id: string; position: number; text: string }[];
}

export interface CommunityRow {
  community_id: string;
  title: string;
  summary: string;
  dirty: boolean;
  member_count: number;
  members: string[];
  updated_at: string;
  dirty_since: string | null;
  drifted_members: string[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  degree: number;
  community_id: string | null;
  mentions: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  support: number;
  provenance: string[];
}

export interface GraphProjection {
  nodes: GraphNode[];
  edges: GraphEdge[];
  communities: Record<string, { title: string; dirty: boolean; member_count: number }>;
  truncated: boolean;
}

export interface CostBreakdown {
  totals: {
    calls: number;
    tokens: number;
    tokens_in: number;
    tokens_out: number;
    usd: number;
    wall_ms: number;
    cache_hits: number;
  };
  by_operation: Record<
    string,
    { calls: number; tokens: number; usd: number; cache_hits: number }
  >;
  recent: {
    operation: string;
    tokens_in: number;
    tokens_out: number;
    wall_ms: number;
    usd: number;
    cache_hit: boolean;
    at: string;
  }[];
}

export interface ResolutionRow {
  candidate_name: string;
  candidate_type: string;
  decision: "merge" | "create";
  matched_entity_id: string | null;
  similarity: number;
  name_match: boolean;
  reason: string;
  chunk_id: string | null;
  at: string;
}

export interface Integrity {
  ok: boolean;
  orphan_entities: string[];
  unsupported_relations: string[];
  relations_citing_dead_chunks: string[];
  dangling_relations: string[];
}

export interface RecomputeReport {
  recomputed: number;
  remaining_dirty: number;
  skipped_clean: number;
  community_ids: string[];
  tokens: number;
  usd: number;
  wall_ms: number;
}

export interface CompactionReport {
  communities_before: number;
  communities_after: number;
  communities_created: number;
  communities_removed: number;
  communities_marked_dirty: number;
  entities_moved: number;
  stable_communities: number;
  wall_ms: number;
  backend: string;
}

export interface Readiness {
  ready: boolean;
  store: boolean;
  queue: boolean;
  queue_depth: number;
  store_backend: string;
  queue_backend: string;
  llm_backend: string;
}

export interface Config {
  config_hash: string;
  store_backend: string;
  queue_backend: string;
  llm_backend: string;
  llm_model: string;
  embed_backend: string;
  tau_sim: number;
  tau_name: number;
  dirty_batch_size: number;
  compaction_interval_s: number;
  compaction_enabled: boolean;
  seed: number;
}

// ---------------------------------------------------------------------------- calls
export const api = {
  stats: () => request<IndexStats>("/index/stats"),
  staleness: () => request<Staleness>("/index/staleness"),
  graph: (limit = 250) => request<GraphProjection>(`/index/graph?limit=${limit}`),
  communities: () => request<CommunityRow[]>("/index/communities"),
  costs: () => request<CostBreakdown>("/index/costs"),
  resolutions: (limit = 100) => request<ResolutionRow[]>(`/index/resolutions?limit=${limit}`),
  integrity: () => request<Integrity>("/maintenance/integrity"),
  ready: () => request<Readiness>("/ready"),
  config: () => request<Config>("/config"),

  documents: (status?: "active" | "deleted") =>
    request<DocumentRow[]>(`/documents${status ? `?status=${status}` : ""}`),
  document: (docId: string) => request<DocumentDetail>(`/documents/${encodeURIComponent(docId)}`),

  addDocument: (body: { uri: string; content: string; doc_id?: string }) =>
    request<ChurnResult>("/documents", { method: "POST", body: JSON.stringify(body) }),
  updateDocument: (docId: string, content: string) =>
    request<ChurnResult>(`/documents/${encodeURIComponent(docId)}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  uploadDocuments: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    return request<UploadResult>("/documents/upload", { method: "POST", body: form });
  },
  deleteDocument: (docId: string) =>
    request<ChurnResult>(`/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }),

  query: (body: { question: string; mode?: string; top_k_entities?: number }) =>
    request<Answer>("/query", { method: "POST", body: JSON.stringify(body) }),

  recompute: (opts: { drain?: boolean; batchSize?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.drain) params.set("drain", "true");
    if (opts.batchSize !== undefined) params.set("batch_size", String(opts.batchSize));
    return request<RecomputeReport>(`/maintenance/recompute?${params}`, { method: "POST" });
  },
  compact: () => request<CompactionReport>("/maintenance/compact", { method: "POST" }),
};

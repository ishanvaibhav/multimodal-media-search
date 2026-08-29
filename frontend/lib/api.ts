// Thin typed HTTP client for the FastAPI backend.
import { auth } from "./firebase";
import type {
  ApiErrorBody,
  ConsistencyReport,
  HealthResponse,
  HistoryItem,
  JobItem,
  MediaListResponse,
  MetricsSnapshot,
  SavedContext,
  SearchResponse,
  SearchRequest,
  SystemInfo,
  UploadComplete,
  UploadConfig,
  UploadInit,
  UploadStatus,
  VideoDetail,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    super(body?.error?.message ?? fallback);
    this.status = status;
    this.code = body?.error?.code ?? "unknown";
    this.detail = body?.error?.detail ?? null;
  }
}

export function setAdminToken(token: string | null): void {
  // Deprecated. We now use Firebase JWT.
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = 30000,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const onAbort = () => controller.abort();
  signal?.addEventListener("abort", onAbort);

  let token = null;
  try {
    if (auth.currentUser) {
      token = await auth.currentUser.getIdToken();
    }
  } catch (e) {
    // Ignore auth errors, let it send unauthenticated
  }

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers ?? {}),
      },
    });

    if (!res.ok) {
      let body: ApiErrorBody | null = null;
      try {
        body = (await res.json()) as ApiErrorBody;
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, body, `Request failed (${res.status})`);
    }

    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  systemInfo: () => request<SystemInfo>("/api/system/info"),
  uploadsConfig: () => request<UploadConfig>("/api/uploads/config"),
  metrics: () => request<MetricsSnapshot>("/api/system/metrics"),
  consistency: () => request<ConsistencyReport>("/api/system/consistency"),
  repairConsistency: () =>
    request<ConsistencyReport>("/api/system/consistency?repair=true", {
      method: "GET",
    }),
  listMedia: (params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return request<MediaListResponse>(`/api/media${qs ? `?${qs}` : ""}`);
  },
  getMedia: (videoId: string) => request<VideoDetail>(`/api/media/${videoId}`),
  deleteMedia: (videoId: string) =>
    request<{ video_id: string; deleted: Record<string, number> }>(
      `/api/media/${videoId}`,
      { method: "DELETE" },
    ),
  reindexMedia: (videoId: string) =>
    request<{ video_id: string; job_id: string }>(
      `/api/media/${videoId}/reindex`,
      { method: "POST" },
    ),
  search: (body: SearchRequest, signal?: AbortSignal) =>
    request<SearchResponse>(
      "/api/search",
      { method: "POST", body: JSON.stringify(body) },
      120000,
      signal,
    ),
  searchHistory: () =>
    request<{ items: HistoryItem[] }>("/api/search/history"),
  clearSearchHistory: () =>
    request<{ deleted: number }>("/api/search/history", { method: "DELETE" }),
  sendFeedback: (feedback: {
    query: string;
    relevant: boolean;
    video_id?: string;
    frame_id?: string;
    timestamp?: number;
  }) =>
    request<{ recorded: boolean }>("/api/search/feedback", {
      method: "POST",
      body: JSON.stringify(feedback),
    }),
  listJobs: () => request<JobItem[]>("/api/jobs?limit=50"),
  getJob: (jobId: string) => request<JobItem>(`/api/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    request<JobItem>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  saveContext: (payload: Record<string, unknown>) =>
    request<SavedContext>("/api/contexts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listContexts: () =>
    request<{ items: SavedContext[] }>("/api/contexts?limit=100"),
  deleteContext: (id: number) =>
    request<{ deleted: number }>(`/api/contexts/${id}`, { method: "DELETE" }),
  exportContexts: async (format: "txt" | "json" | "csv"): Promise<string> => {
    let token = null;
    if (auth.currentUser) token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API_BASE}/api/contexts/export?format=${format}`, {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      }
    });
    if (!res.ok) throw new ApiError(res.status, null, "export failed");
    return res.text();
  },
  clearAllData: (confirmation: string) =>
    request<{ cleared: boolean; deleted: Record<string, unknown> }>(
      "/api/admin/data",
      { method: "DELETE", body: JSON.stringify({ confirmation }) },
    ),
};

export function absoluteUrl(path: string): string {
  if (path.startsWith("http")) return path;
  return `${API_BASE}${path}`;
}

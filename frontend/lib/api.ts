// Thin typed HTTP client for the FastAPI backend.
// IMPORTANT: all large-file uploads go DIRECTLY to the backend (never through
// the Next.js server) — see lib/uploads.ts.

import type {
  ApiErrorBody,
  ConsistencyReport,
  HealthResponse,
  HistoryItem,
  JobItem,
  MediaListResponse,
  MetricsSnapshot,
  SearchResponse,
  SearchRequest,
  SystemInfo,
  UploadComplete,
  UploadConfig,
  UploadInit,
  UploadStatus,
  VideoDetail,
} from "./types";

// API base: RELATIVE by default so the browser talks to its own origin and the
// Next.js dev server proxies /api/* to the FastAPI backend (see next.config.mjs
// rewrites). Set NEXT_PUBLIC_API_BASE to a backend origin only when the
// backend is directly reachable from the browser (e.g. a separate deployment).
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

// Admin token is held in memory only (never persisted to localStorage, so a
// long-lived destructive credential is not left in browser storage). It must
// be re-entered after a full page reload — the documented tradeoff for
// avoiding persistent credentials. A future revision may add a short-lived,
// HttpOnly/Secure/SameSite session cookie instead.
let adminTokenMemory: string | null = null;

export function setAdminToken(token: string | null): void {
  adminTokenMemory = token;
}

function adminHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  return adminTokenMemory ? { "x-admin-token": adminTokenMemory } : {};
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
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...adminHeaders(),
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
  // health / system
  health: () => request<HealthResponse>("/api/health"),
  systemInfo: () => request<SystemInfo>("/api/system/info"),

  // uploads (large files go DIRECTLY to the backend — never through Next.js)
  uploadsConfig: () => request<UploadConfig>("/api/uploads/config"),
  metrics: () => request<MetricsSnapshot>("/api/system/metrics"),
  consistency: () => request<ConsistencyReport>("/api/system/consistency"),
  repairConsistency: () =>
    request<ConsistencyReport>("/api/system/consistency?repair=true", {
      method: "GET",
    }),

  // media
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

  // search
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

  // jobs
  listJobs: () => request<JobItem[]>("/api/jobs?limit=50"),
  getJob: (jobId: string) => request<JobItem>(`/api/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    request<JobItem>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),

  // admin
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

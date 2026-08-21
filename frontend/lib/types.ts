// Types mirroring the FastAPI backend schemas (backend/app/schemas).

export type VideoStatus =
  | "pending"
  | "queued"
  | "validating"
  | "probing"
  | "extracting_frames"
  | "deduplicating"
  | "embedding"
  | "indexing"
  | "finalizing"
  | "ready"
  | "failed"
  | "cancelled";

export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "cancelling";

export type SearchMode = "fast" | "accurate" | "metadata";

export interface VideoItem {
  video_id: string;
  filename: string;
  stored_filename: string;
  media_type: "video" | "image";
  size_bytes: number;
  duration_seconds: number | null;
  duration_hms: string | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  codec: string | null;
  container: string | null;
  has_audio: boolean;
  status: VideoStatus;
  frame_count: number;
  error: string | null;
  uploaded_at: string | null;
  indexed_at: string | null;
  thumbnail_url: string;
  stream_url: string;
}

export interface FrameInfo {
  frame_id: string;
  timestamp: number;
  timestamp_hms: string;
  frame_url: string;
}

export interface VideoDetail extends VideoItem {
  frames: FrameInfo[];
  job?: JobItem | null;
}

export interface MediaListResponse {
  items: VideoItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface JobItem {
  job_id: string;
  video_id: string;
  type: string;
  status: JobStatus;
  progress: number;
  current_stage: string;
  frames_processed: number;
  frames_total: number;
  frames_sampled: number;
  frames_kept: number;
  frames_embedded: number;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface UploadInit {
  upload_id: string;
  filename: string;
  file_size: number;
  chunk_size: number;
  total_chunks: number;
  status: string;
}

export interface UploadStatus {
  upload_id: string;
  filename: string;
  file_size: number;
  content_type: string | null;
  chunk_size: number;
  total_chunks: number;
  received_chunks: number;
  received_bytes: number;
  status: string;
  error: string | null;
  progress: number;
}

export interface UploadComplete {
  upload_id: string;
  video_id: string;
  job_id: string;
  status: string;
}

export interface ContextFrame {
  frame_id: string;
  timestamp: number;
  timestamp_hms: string;
  frame_url: string;
}

export interface SearchResult {
  video_id: string;
  video_name: string;
  media_type: "video" | "image";
  timestamp: number;
  timestamp_hms: string;
  similarity: number;
  raw_similarity: number;
  frame_id: string;
  retrieval_stage: string;
  frame_url: string;
  stream_url: string;
  duration: number | null;
  duration_hms: string | null;
  width: number | null;
  height: number | null;
  uploaded_at: string | null;
  context_frames: ContextFrame[];
}

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  took_ms: number;
  total_candidates: number;
  grouped_events: number;
  semantic_search: boolean;
  rerank: "applied" | "skipped" | "unavailable";
  results: SearchResult[];
}

export interface SearchFilters {
  video_ids: string[];
  date_from: string;
  date_to: string;
  min_similarity: number;
  top_k: number;
  final_results: number;
  fine_search: boolean;
  temporal_grouping: boolean;
  sort_by: "relevance" | "timestamp" | "upload_date";
  sort_order: "asc" | "desc";
  media_types: string[];
  min_duration: number | null;
  max_duration: number | null;
  status: string;
  media_type: "" | "video" | "image";
}

export interface SearchRequest extends SearchFilters {
  query: string;
  mode: SearchMode;
}

export interface HistoryItem {
  id: number;
  query: string;
  filters: string | null;
  result_count: number;
  mode: SearchMode | null;
  latency_ms: number | null;
  created_at: string;
}

export interface HealthDetails {
  python: string;
  embedding_backend: string;
  semantic_search: boolean;
  model: string;
  embedding_dim: number;
  embedding_device: string;
  vectors: number;
  model_mismatch: boolean;
  maintenance: boolean;
}

export interface HealthResponse {
  api: string;
  database: string;
  chromadb: string;
  ffmpeg: string;
  ffprobe: string;
  embedding_model: string;
  storage: string;
  worker: string;
  details: HealthDetails;
}

export interface SystemInfo {
  app_name: string;
  app_env: string;
  version: string;
  python: string;
  embedding_backend: string;
  semantic_search: boolean;
  model: string;
  embedding_dim: number;
  embedding_device: string;
  ffmpeg: string;
  ffprobe: string;
  chroma_collection: string;
  data_dir: string;
  storage: Record<string, number | string>;
  resources: Record<string, number | boolean>;
  admin_auth: "token" | "required" | "open";
}

export interface ConsistencyReport {
  videos: number;
  frames: number;
  vectors: number;
  missing_files: Array<Record<string, string>>;
  orphan_files: number;
  orphan_file_list?: string[];
  missing_vector_count: number;
  orphan_vector_count: number;
  orphan_jobs: Array<Record<string, string>>;
  repaired?: Record<string, number>;
  reconciliation_required_count?: number;
  fine_cache_intervals?: number;
  orphan_manifest_count?: number;
  invalid_manifest_count?: number;
  model_consistency?: {
    registry_model: string | null;
    chroma_models: string[];
    consistent: boolean;
  };
}

export interface MetricsSnapshot {
  counters: Record<string, number>;
  gauges: Record<string, number>;
  latency_s: Record<string, { count: number; mean_s: number; p95_s: number }>;
  uptime_s: number;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail: unknown;
  };
}

export interface UploadConfig {
  chunk_size: number;
  chunk_size_mb: number;
  max_upload_size_bytes: number;
  max_upload_size_gb: number;
  video_extensions: string[];
  image_extensions: string[];
}

// Context formatting + export helpers (shared by the Context Viewer and
// Saved Contexts). All outputs are plain text / structured data — never HTML.
import type { SearchResult, SavedContext } from "./types";

export function contextText(result: SearchResult, query: string): string {
  const lines = [
    "----------------------------------------",
    "RETRIEVED EVIDENCE",
    "",
    `Media: ${result.video_name}`,
    "",
    `Query: "${query}"`,
    `Match: ${result.timestamp_hms}`,
  ];
  if (result.context_start != null && result.context_end != null) {
    const start = result.context_start_hms ?? result.context_start.toFixed(2);
    const end = result.context_end_hms ?? result.context_end.toFixed(2);
    lines.push(`Segment: ${start} - ${end}`);
  }
  lines.push(`Relevance: ${result.similarity.toFixed(2)}`);
  if (result.context_frames.length > 0) {
    lines.push("Representative frames:");
    for (const f of result.context_frames) {
      lines.push(`  ${f.timestamp_hms}`);
    }
  }
  if (result.context_reason) lines.push(`Reason: ${result.context_reason}`);
  lines.push(`Source: ${result.video_name}`);
  lines.push("----------------------------------------");
  if (result.context_summary) {
    lines.push("", "----------------------------------------", "AI SUMMARY", "", result.context_summary, "----------------------------------------");
  }
  return lines.join("\n");
}

/**
 * Structured, readable plain text for "Copy Result" (never JSON-only).
 */
export function resultPlainText(result: SearchResult, query: string): string {
  const lines = [
    `Query: ${query}`,
    `Media: ${result.video_name}`,
    `Type: ${result.media_type}`,
    `Timestamp: ${result.timestamp_hms}`,
  ];
  if (result.context_start != null && result.context_end != null) {
    const start = result.context_start_hms ?? result.context_start.toFixed(2);
    const end = result.context_end_hms ?? result.context_end.toFixed(2);
    lines.push(`Segment: ${start} - ${end}`);
  }
  lines.push(`Relevance: ${result.similarity.toFixed(2)}`);
  lines.push(`Retrieval Stage: ${result.retrieval_stage}`);
  if (result.context_reason) lines.push(`Reason: ${result.context_reason}`);
  if (result.context_frames.length > 0) {
    lines.push(
      `Representative frames: ${result.context_frames.map((f) => f.timestamp_hms).join(", ")}`,
    );
  }
  lines.push("", "Context:", result.context_text ?? contextText(result, query));
  return lines.join("\n");
}

export function resultJson(result: SearchResult, query: string): string {
  return JSON.stringify(
    {
      query,
      media: result.video_name,
      media_type: result.media_type,
      timestamp: result.timestamp,
      start: result.context_start,
      end: result.context_end,
      score: result.similarity,
      frame_id: result.frame_id,
      retrieval_stage: result.retrieval_stage,
      context_frames: result.context_frames,
      reason: result.context_reason,
      context: result.context_text ?? contextText(result, query),
    },
    null,
    2,
  );
}

export function resultCsv(result: SearchResult, query: string): string {
  const esc = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = "query,media,media_type,timestamp,start,end,score,reason";
  const row = [
    query,
    result.video_name,
    result.media_type,
    result.timestamp,
    result.context_start ?? "",
    result.context_end ?? "",
    result.similarity,
    result.context_reason ?? "",
  ]
    .map(esc)
    .join(",");
  return `${header}\n${row}\n`;
}

export function savedContextText(s: SavedContext): string {
  const start = s.context_start != null ? s.context_start.toFixed(2) : "";
  const end = s.context_end != null ? s.context_end.toFixed(2) : "";
  return [
    "----------------------------------------",
    `Media: ${s.filename}`,
    "",
    `Query: "${s.query}"`,
    `Match: ${s.timestamp_hms}`,
    start && end ? `Segment: ${start} - ${end}` : "",
    `Relevance: ${s.score.toFixed(2)}`,
    "----------------------------------------",
  ]
    .filter(Boolean)
    .join("\n");
}

export function safeFileName(base: string): string {
  return base.replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) || "export";
}

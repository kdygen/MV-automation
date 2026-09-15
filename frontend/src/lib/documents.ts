/**
 * Pure logic for the knowledge-documents panel: file validation, status presentation,
 * and polling policy.
 *
 * Kept free of React and `fetch` so it can be unit-tested in the node-only vitest
 * environment, matching `knowledge.ts` and `history.ts`.
 *
 * The client-side checks here mirror the backend rather than replacing it. The server
 * sniffs the actual bytes and owns every limit; this exists only so an owner who picks
 * a 40 MB scan learns that before spending a minute uploading it.
 */

import { ApiError } from "./api";
import type { DocumentStatus, KnowledgeDocument } from "./dashboard-types";

/** Must stay in step with `knowledge_max_upload_bytes` in the backend settings. */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
export const MAX_DOCUMENTS = 200;

/** What the file picker offers. The server decides what it actually accepts. */
export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md"] as const;
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(",");

export function fileExtension(filename: string): string {
  const match = /\.[A-Za-z0-9]{1,10}$/.exec(filename);
  return match ? match[0].toLowerCase() : "";
}

/** Human file size. Whole megabytes past 1 MB — nobody needs three decimals here. */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Why this file cannot be uploaded, or `null` if it can.
 *
 * Only catches what the browser can know for certain. A PDF that turns out to be a scan
 * is indistinguishable from a good one until the server parses it, which is exactly why
 * the failed state exists in the UI.
 */
export function fileRejection(file: File, documentCount: number): string | null {
  if (documentCount >= MAX_DOCUMENTS) {
    return `You have reached the limit of ${MAX_DOCUMENTS} documents. Delete one you no longer need first.`;
  }
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(fileExtension(file.name))) {
    return "Upload a PDF, a Word document (.docx), or a text file.";
  }
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) {
    return `That file is ${fileSize(file.size)}. The limit is ${fileSize(MAX_UPLOAD_BYTES)} — try splitting it into sections.`;
  }
  return null;
}

/** "2026_cancellation-policy.pdf" → "2026 cancellation policy". Mirrors the backend. */
export function titleFromFilename(filename: string): string {
  const stem = filename.slice(0, filename.length - fileExtension(filename).length);
  const cleaned = stem.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return cleaned || "Untitled document";
}

// ------------------------------------------------------------------ presentation

const STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: "Queued",
  processing: "Processing",
  ready: "Live",
  failed: "Needs attention",
  inactive: "Switched off",
};

/**
 * Plain-English status.
 *
 * "Live" rather than "ready" because the question an owner is actually asking is
 * whether the assistant is using this document right now.
 */
export function statusLabel(status: DocumentStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function statusTone(status: DocumentStatus): string {
  if (status === "ready") return "bg-green-100 text-green-700";
  if (status === "failed") return "bg-red-100 text-red-700";
  if (status === "inactive") return "bg-slate-200 text-slate-600";
  return "bg-amber-100 text-amber-700";
}

/** Ingestion runs server-side after the upload responds, so these states resolve on their own. */
export function isSettling(status: DocumentStatus): boolean {
  return status === "pending" || status === "processing";
}

/** Whether any document is still being worked on, i.e. whether to keep polling. */
export function shouldPoll(documents: KnowledgeDocument[]): boolean {
  return documents.some((document) => isSettling(document.status));
}

/**
 * A document that finished but has no vectors was indexed while embeddings were
 * unavailable. It still answers keyword questions, so it is not a failure — but it is
 * working at half strength and retrying will fix it.
 */
export function isKeywordOnly(document: KnowledgeDocument): boolean {
  return (
    document.status !== "failed" &&
    document.chunk_count > 0 &&
    document.embedded_chunk_count < document.chunk_count
  );
}

/** Retry is offered for anything unfinished, and for a live document missing vectors. */
export function canRetry(document: KnowledgeDocument): boolean {
  if (isKeywordOnly(document)) return true;
  return document.status === "failed" || document.status === "processing";
}

/** Only a document that finished indexing can be switched on or off. */
export function canToggle(document: KnowledgeDocument): boolean {
  return document.status === "ready" || document.status === "inactive";
}

/** "3 passages · pages 1–4", omitting anything the format could not tell us. */
export function passageSummary(document: KnowledgeDocument): string {
  const parts = [`${document.chunk_count} passage${document.chunk_count === 1 ? "" : "s"}`];
  if (document.page_count) {
    parts.push(`${document.page_count} page${document.page_count === 1 ? "" : "s"}`);
  }
  parts.push(fileSize(document.byte_size));
  return parts.join(" · ");
}

export function pageLabel(from: number | null, to: number | null): string | null {
  if (from === null) return null;
  return to !== null && to !== from ? `Pages ${from}–${to}` : `Page ${from}`;
}

/**
 * How many documents the assistant is actually using, for the panel header.
 *
 * Counts `ready` only. A queued or failed document is not answering anything, and
 * counting it would tell an owner their policies are live when they are not.
 */
export function liveCount(documents: KnowledgeDocument[]): number {
  return documents.filter((document) => document.status === "ready").length;
}

/** Owner-safe copy for a failed request. Never surfaces a raw status or stack. */
export function documentErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    // The backend's validation and conflict messages are written for this reader —
    // they name the file, the limit, or the duplicate — so they are passed through.
    if (error.code === "validation_error" || error.code === "conflict") return error.message;
    if (error.code === "forbidden") return "Only owners and admins can manage documents.";
    if (error.code === "not_found")
      return "That document no longer exists. Refresh to see the latest.";
    if (error.code === "network_error") return "Could not reach the server. Please try again.";
  }
  return "Something went wrong. Please try again.";
}

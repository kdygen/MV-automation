"use client";

/**
 * Uploaded policy documents: the second way a company teaches its assistant.
 *
 * Manual entries answer one question each, in the owner's own words. Documents are for
 * what already exists — the cancellation policy, the COI letter, the rate sheet — where
 * retyping it as entries is the thing that stops an owner doing it at all.
 *
 * Three things drive the design:
 *
 * * **Ingestion is asynchronous.** The upload returns immediately with a queued row;
 *   parsing and embedding finish on the server. So the list polls while anything is
 *   unsettled and stops the moment nothing is.
 * * **A failure has to be readable.** `failure_reason` comes from the server already
 *   written for this reader ("this looks like a scanned document — upload a text-based
 *   PDF"), so it is shown verbatim rather than translated into a generic banner.
 * * **The owner can see what the assistant sees.** Expanding a document shows the exact
 *   passages that were indexed, which is the only way to diagnose a PDF whose layout
 *   scrambled on extraction.
 *
 * Every decision about what may be uploaded is the server's; `@/lib/documents` only
 * catches what the browser can know for certain, to save a doomed upload.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteDocument,
  getDocument,
  listDocuments,
  retryDocument,
  setDocumentActive,
  uploadDocument,
} from "@/lib/dashboard-api";
import type {
  DocumentPassage,
  KnowledgeDocument,
  KnowledgeDocumentDetail,
} from "@/lib/dashboard-types";
import {
  ACCEPT_ATTRIBUTE,
  MAX_DOCUMENTS,
  MAX_UPLOAD_BYTES,
  canRetry,
  canToggle,
  documentErrorMessage,
  fileRejection,
  fileSize,
  isKeywordOnly,
  isSettling,
  liveCount,
  pageLabel,
  passageSummary,
  shouldPoll,
  statusLabel,
  statusTone,
} from "@/lib/documents";
import { Button, Card, ErrorBanner } from "@/components/ui";

/** How often to re-check while something is still parsing. */
const POLL_MS = 1500;

export function KnowledgeDocuments() {
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(
    () => listDocuments().then(setDocuments),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    listDocuments()
      .then((loaded) => {
        if (!cancelled) setDocuments(loaded);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load your documents.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Poll while anything is queued or parsing, and stop as soon as nothing is.
   *
   * The effect re-runs whenever the list changes, so the interval is torn down the
   * moment the last document settles rather than polling a quiet page forever.
   */
  useEffect(() => {
    if (!documents || !shouldPoll(documents)) return;
    const timer = setInterval(() => {
      listDocuments()
        .then(setDocuments)
        .catch(() => {
          /* a dropped poll is not worth an error banner; the next one will tell us */
        });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [documents]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await reload();
    } catch (err) {
      setError(documentErrorMessage(err));
      // Re-read regardless: a rejected action often means this view is out of date.
      await reload().catch(() => {});
    } finally {
      setBusy(false);
    }
  };

  const choose = async (file: File) => {
    const rejection = fileRejection(file, documents?.length ?? 0);
    if (rejection) {
      setError(rejection);
      return;
    }
    await act(() => uploadDocument(file));
  };

  const remove = (document: KnowledgeDocument) => {
    if (
      !window.confirm(
        `Remove "${document.title}"? Your assistant will stop using it immediately.`,
      )
    ) {
      return;
    }
    if (expanded === document.id) setExpanded(null);
    void act(() => deleteDocument(document.id));
  };

  if (!documents && error) return <ErrorBanner message={error} />;

  const live = documents ? liveCount(documents) : 0;

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Policy documents
        </h2>
        {documents ? (
          <span className="text-xs text-slate-500">
            {live} in use · {documents.length} of {MAX_DOCUMENTS}
          </span>
        ) : null}
      </div>

      <Card>
        <p className="text-sm text-slate-600">
          Upload a policy you have already written and your assistant will answer from it.
          PDF, Word, or text — up to {fileSize(MAX_UPLOAD_BYTES)} each. Scanned documents
          cannot be read, because they hold pictures of words rather than words.
        </p>

        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          className="hidden"
          onChange={(e) => {
            const picked = e.target.files?.[0];
            // Reset so choosing the same file twice still fires a change event.
            e.target.value = "";
            if (picked) void choose(picked);
          }}
        />
        <div className="mt-4">
          <Button onClick={() => fileInput.current?.click()} disabled={busy}>
            {busy ? "Working…" : "Upload a document"}
          </Button>
        </div>

        {error ? (
          <div className="mt-4">
            <ErrorBanner message={error} />
          </div>
        ) : null}
      </Card>

      {documents === null ? (
        <p className="py-6 text-center text-sm text-slate-500">Loading documents…</p>
      ) : documents.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-8 text-center text-sm text-slate-500">
          No documents yet. Your written entries above still work on their own.
        </p>
      ) : (
        <ul className="space-y-3">
          {documents.map((document) => (
            <li key={document.id}>
              <DocumentRow
                document={document}
                busy={busy}
                expanded={expanded === document.id}
                onExpand={() =>
                  setExpanded(expanded === document.id ? null : document.id)
                }
                onRetry={() => act(() => retryDocument(document.id))}
                onToggle={() =>
                  act(() =>
                    setDocumentActive(document.id, document.status !== "ready"),
                  )
                }
                onDelete={() => remove(document)}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DocumentRow({
  document,
  busy,
  expanded,
  onExpand,
  onRetry,
  onToggle,
  onDelete,
}: {
  document: KnowledgeDocument;
  busy: boolean;
  expanded: boolean;
  onExpand: () => void;
  onRetry: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const settling = isSettling(document.status);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
            {document.title}
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${statusTone(document.status)}`}
            >
              {statusLabel(document.status)}
            </span>
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {document.original_filename} · {passageSummary(document)}
          </p>

          {document.failure_reason ? (
            <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {document.failure_reason}
            </p>
          ) : null}

          {isKeywordOnly(document) ? (
            <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Indexed for keyword search only — semantic search was unavailable when this
              was processed. Retry to finish it.
            </p>
          ) : null}

          {settling ? (
            <p className="mt-2 text-sm text-slate-500">
              Reading this document. This page updates on its own.
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2 text-xs">
          {canToggle(document) ? (
            <label className="flex items-center gap-2 text-slate-600">
              <input
                type="checkbox"
                checked={document.status === "ready"}
                onChange={onToggle}
                disabled={busy}
                aria-label={`${document.status === "ready" ? "Switch off" : "Switch on"} ${document.title}`}
                className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
              />
              In use
            </label>
          ) : null}
          <div className="flex gap-2">
            {document.chunk_count > 0 ? (
              <button
                type="button"
                onClick={onExpand}
                className="font-medium text-blue-600 hover:text-blue-800"
              >
                {expanded ? "Hide" : "Inspect"}
              </button>
            ) : null}
            {canRetry(document) ? (
              <button
                type="button"
                onClick={onRetry}
                disabled={busy}
                className="font-medium text-blue-600 hover:text-blue-800 disabled:text-slate-400"
              >
                Retry
              </button>
            ) : null}
            <button
              type="button"
              onClick={onDelete}
              disabled={busy}
              className="font-medium text-red-600 hover:text-red-800 disabled:text-slate-400"
            >
              Remove
            </button>
          </div>
        </div>
      </div>

      {expanded ? <DocumentInspector documentId={document.id} /> : null}
    </div>
  );
}

/**
 * What was actually extracted and indexed.
 *
 * Loaded on expand rather than with the list: extracted text runs to tens of thousands
 * of characters per document, and sending every document's text to render a list nobody
 * has opened yet would be the single heaviest request in the dashboard.
 */
function DocumentInspector({ documentId }: { documentId: string }) {
  // The fetch result carries the id it belongs to, so "loading" is derived from a
  // mismatch rather than set by the effect. That keeps a late response for a document
  // the owner has already collapsed from painting over the current one.
  const [result, setResult] = useState<
    { id: string; detail: KnowledgeDocumentDetail } | { id: string; failed: true } | null
  >(null);
  const [showText, setShowText] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getDocument(documentId)
      .then((loaded) => {
        if (!cancelled) setResult({ id: documentId, detail: loaded });
      })
      .catch(() => {
        if (!cancelled) setResult({ id: documentId, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  const current = result?.id === documentId ? result : null;
  if (current && "failed" in current) {
    return <p className="mt-4 text-sm text-slate-500">Could not load this document.</p>;
  }
  if (!current) return <p className="mt-4 text-sm text-slate-500">Loading…</p>;
  const detail = current.detail;

  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          What your assistant can quote
        </h3>
        {detail.extracted_text ? (
          <button
            type="button"
            onClick={() => setShowText(!showText)}
            className="text-xs font-medium text-blue-600 hover:text-blue-800"
          >
            {showText ? "Hide raw text" : "View raw text"}
          </button>
        ) : null}
      </div>

      {showText && detail.extracted_text ? (
        <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-700">
          {detail.extracted_text}
          {detail.extracted_text_truncated ? "\n\n… truncated" : ""}
        </pre>
      ) : null}

      <ol className="mt-3 space-y-3">
        {detail.passages.map((passage) => (
          <Passage key={passage.chunk_index} passage={passage} />
        ))}
      </ol>
    </div>
  );
}

function Passage({ passage }: { passage: DocumentPassage }) {
  const pages = pageLabel(passage.page_from, passage.page_to);
  return (
    <li className="rounded-lg border border-slate-100 bg-slate-50 p-3">
      <p className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        {passage.heading ? (
          <span className="font-medium text-slate-700">{passage.heading}</span>
        ) : null}
        {pages ? <span>{pages}</span> : null}
        {passage.is_embedded ? null : <span>keyword search only</span>}
      </p>
      <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">{passage.content}</p>
    </li>
  );
}

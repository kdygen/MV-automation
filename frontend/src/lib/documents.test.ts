import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import type { DocumentStatus, KnowledgeDocument } from "./dashboard-types";
import {
  ACCEPT_ATTRIBUTE,
  MAX_UPLOAD_BYTES,
  canRetry,
  canToggle,
  documentErrorMessage,
  fileExtension,
  fileRejection,
  fileSize,
  isKeywordOnly,
  isSettling,
  liveCount,
  pageLabel,
  passageSummary,
  shouldPoll,
  statusLabel,
  titleFromFilename,
} from "./documents";

function doc(patch: Partial<KnowledgeDocument> = {}): KnowledgeDocument {
  return {
    id: "d1",
    title: "Cancellation policy",
    original_filename: "policy.pdf",
    mime_type: "application/pdf",
    byte_size: 2048,
    status: "ready",
    failure_reason: null,
    page_count: 2,
    chunk_count: 3,
    embedded_chunk_count: 3,
    indexed_at: "2026-09-14T00:00:00Z",
    created_at: "2026-09-14T00:00:00Z",
    updated_at: "2026-09-14T00:00:00Z",
    ...patch,
  };
}

/** `File` is available in the vitest node environment via undici. */
function pick(name: string, size: number): File {
  return { name, size } as File;
}

describe("file validation", () => {
  it("reads an extension case-insensitively", () => {
    expect(fileExtension("Policy.PDF")).toBe(".pdf");
    expect(fileExtension("no-extension")).toBe("");
  });

  it("accepts the supported types", () => {
    for (const name of ["a.pdf", "a.docx", "a.txt", "a.md"]) {
      expect(fileRejection(pick(name, 1000), 0)).toBeNull();
    }
  });

  it("rejects a type the server cannot parse", () => {
    expect(fileRejection(pick("rates.xlsx", 1000), 0)).toContain("PDF");
  });

  it("rejects an empty file", () => {
    expect(fileRejection(pick("a.pdf", 0), 0)).toContain("empty");
  });

  it("rejects a file over the limit and says how big it was", () => {
    const rejection = fileRejection(pick("a.pdf", MAX_UPLOAD_BYTES + 1), 0);
    expect(rejection).toContain("20.0 MB");
  });

  it("rejects an upload once the company is at its document cap", () => {
    expect(fileRejection(pick("a.pdf", 10), 200)).toContain("limit of 200");
  });

  it("offers exactly the supported extensions to the picker", () => {
    expect(ACCEPT_ATTRIBUTE).toBe(".pdf,.docx,.txt,.md");
  });
});

describe("file size", () => {
  it("scales the unit to the number", () => {
    expect(fileSize(512)).toBe("512 B");
    expect(fileSize(2048)).toBe("2 KB");
    expect(fileSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

describe("titleFromFilename", () => {
  it("turns a filename into something a person would write", () => {
    expect(titleFromFilename("2026_cancellation-policy.pdf")).toBe("2026 cancellation policy");
  });

  it("never returns an empty title", () => {
    expect(titleFromFilename(".pdf")).toBe("Untitled document");
  });
});

describe("status presentation", () => {
  it("answers the question the owner is actually asking", () => {
    expect(statusLabel("ready")).toBe("Live");
    expect(statusLabel("inactive")).toBe("Switched off");
    expect(statusLabel("failed")).toBe("Needs attention");
  });

  it("knows which states resolve on their own", () => {
    const settling: DocumentStatus[] = ["pending", "processing"];
    for (const status of settling) expect(isSettling(status)).toBe(true);
    for (const status of ["ready", "failed", "inactive"] as DocumentStatus[]) {
      expect(isSettling(status)).toBe(false);
    }
  });

  it("polls only while something is still being worked on", () => {
    expect(shouldPoll([doc(), doc({ status: "failed" })])).toBe(false);
    expect(shouldPoll([doc(), doc({ status: "processing" })])).toBe(true);
    expect(shouldPoll([])).toBe(false);
  });

  it("counts only the documents the assistant is really using", () => {
    expect(
      liveCount([doc(), doc({ status: "inactive" }), doc({ status: "failed" }), doc()]),
    ).toBe(2);
  });

  it("summarises what was extracted, omitting what the format could not say", () => {
    expect(passageSummary(doc())).toBe("3 passages · 2 pages · 2 KB");
    expect(passageSummary(doc({ page_count: null, chunk_count: 1 }))).toBe("1 passage · 2 KB");
  });

  it("labels a page range only when there is one", () => {
    expect(pageLabel(1, 1)).toBe("Page 1");
    expect(pageLabel(1, 4)).toBe("Pages 1–4");
    expect(pageLabel(null, null)).toBeNull();
  });
});

describe("what the owner can do to a document", () => {
  it("treats a live document with no vectors as working at half strength", () => {
    expect(isKeywordOnly(doc({ embedded_chunk_count: 0 }))).toBe(true);
    expect(isKeywordOnly(doc())).toBe(false);
    // A failure has its own message; calling it "keyword only" would bury the real cause.
    expect(isKeywordOnly(doc({ status: "failed", chunk_count: 0 }))).toBe(false);
  });

  it("offers retry for anything unfinished or half-indexed, not for finished work", () => {
    expect(canRetry(doc({ status: "failed", chunk_count: 0 }))).toBe(true);
    expect(canRetry(doc({ status: "processing" }))).toBe(true);
    expect(canRetry(doc({ embedded_chunk_count: 1 }))).toBe(true);
    expect(canRetry(doc())).toBe(false);
  });

  it("offers the on/off switch only once indexing finished", () => {
    expect(canToggle(doc())).toBe(true);
    expect(canToggle(doc({ status: "inactive" }))).toBe(true);
    expect(canToggle(doc({ status: "pending" }))).toBe(false);
    expect(canToggle(doc({ status: "failed" }))).toBe(false);
  });
});

describe("documentErrorMessage", () => {
  it("passes through backend copy that was written for this reader", () => {
    const error = new ApiError("conflict", "You have already uploaded this file.", 409);
    expect(documentErrorMessage(error)).toBe("You have already uploaded this file.");
  });

  it("replaces codes the owner cannot act on", () => {
    expect(documentErrorMessage(new ApiError("forbidden", "nope", 403))).toContain(
      "owners and admins",
    );
    expect(documentErrorMessage(new Error("boom"))).toBe(
      "Something went wrong. Please try again.",
    );
  });
});

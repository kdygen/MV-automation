import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import type { KnowledgeEntry, StarterTopic } from "./dashboard-types";
import {
  KNOWLEDGE_CATEGORIES,
  MAX_CONTENT_LENGTH,
  MAX_KEYWORDS_LENGTH,
  MAX_TITLE_LENGTH,
  canSaveDraft,
  categoryLabel,
  coverage,
  draftErrors,
  draftFromEntry,
  draftFromStarter,
  draftToPayload,
  emptyDraft,
  groupByCategory,
  knowledgeErrorMessage,
  uncoveredStarters,
} from "./knowledge";

const entry = (overrides: Partial<KnowledgeEntry> = {}): KnowledgeEntry => ({
  id: "11111111-1111-1111-1111-111111111111",
  category: "insurance",
  title: "Certificate of Insurance",
  content: "We provide a COI at no charge.",
  keywords: "COI",
  is_active: true,
  updated_at: "2026-09-10T00:00:00Z",
  ...overrides,
});

const topic = (overrides: Partial<StarterTopic> = {}): StarterTopic => ({
  category: "insurance",
  title: "Certificate of Insurance",
  prompt: "Do you provide Certificates of Insurance (COIs)?",
  keywords: "COI, certificate of insurance",
  ...overrides,
});

describe("drafts", () => {
  it("starts empty, active, and on a known category", () => {
    const draft = emptyDraft();
    expect(draft.id).toBeNull();
    expect(draft.title).toBe("");
    expect(draft.content).toBe("");
    expect(draft.is_active).toBe(true);
    expect(KNOWLEDGE_CATEGORIES).toContain(draft.category);
  });

  it("round-trips an existing entry, mapping null keywords to an empty field", () => {
    expect(draftFromEntry(entry())).toMatchObject({
      id: entry().id,
      title: "Certificate of Insurance",
      keywords: "COI",
    });
    expect(draftFromEntry(entry({ keywords: null })).keywords).toBe("");
  });

  it("pre-fills a starter topic but never its answer", () => {
    const draft = draftFromStarter(topic());
    expect(draft.category).toBe("insurance");
    expect(draft.title).toBe("Certificate of Insurance");
    expect(draft.keywords).toBe("COI, certificate of insurance");
    // The owner supplies every business fact — nothing is put in their mouth.
    expect(draft.content).toBe("");
    expect(draft.id).toBeNull();
  });
});

describe("validation", () => {
  const valid = { ...emptyDraft(), title: "Stairs", content: "No extra stair fee." };

  it("accepts a complete draft", () => {
    expect(draftErrors(valid)).toEqual({});
    expect(canSaveDraft(valid, false)).toBe(true);
  });

  it("requires category, title, and answer", () => {
    for (const blank of ["", "   ", "\n\t"]) {
      expect(draftErrors({ ...valid, title: blank }).title).toBeDefined();
      expect(draftErrors({ ...valid, content: blank }).content).toBeDefined();
      expect(draftErrors({ ...valid, category: blank }).category).toBeDefined();
    }
  });

  it("rejects whitespace-only answers rather than saving a blank policy", () => {
    expect(canSaveDraft({ ...valid, content: "     " }, false)).toBe(false);
  });

  it("enforces the same limits as the backend schema", () => {
    expect(draftErrors({ ...valid, title: "x".repeat(MAX_TITLE_LENGTH) }).title).toBeUndefined();
    expect(draftErrors({ ...valid, title: "x".repeat(MAX_TITLE_LENGTH + 1) }).title).toBeDefined();
    expect(
      draftErrors({ ...valid, content: "x".repeat(MAX_CONTENT_LENGTH + 1) }).content,
    ).toBeDefined();
    expect(
      draftErrors({ ...valid, keywords: "x".repeat(MAX_KEYWORDS_LENGTH + 1) }).keywords,
    ).toBeDefined();
  });

  it("blocks saving while a request is in flight", () => {
    expect(canSaveDraft(valid, true)).toBe(false);
  });
});

describe("payloads", () => {
  it("trims every field and never sends an identifier", () => {
    const payload = draftToPayload({
      ...emptyDraft(),
      category: "  policy  ",
      title: "  Cancellation  ",
      content: "  Free up to 48 hours.  ",
      keywords: "  cancel, refund  ",
    });
    expect(payload).toEqual({
      category: "policy",
      title: "Cancellation",
      content: "Free up to 48 hours.",
      keywords: "cancel, refund",
      is_active: true,
    });
    expect(payload).not.toHaveProperty("company_id");
    expect(payload).not.toHaveProperty("id");
  });

  it("sends null for blank keywords so 'none' has one representation", () => {
    expect(draftToPayload({ ...emptyDraft(), keywords: "   " }).keywords).toBeNull();
  });
});

describe("onboarding coverage", () => {
  const topics = [topic(), topic({ title: "Stairs", prompt: "Are there fees for stairs?" })];

  it("suggests only topics with no entry yet", () => {
    const remaining = uncoveredStarters(topics, [entry()]);
    expect(remaining.map((t) => t.title)).toEqual(["Stairs"]);
  });

  it("matches titles case- and whitespace-insensitively", () => {
    expect(uncoveredStarters(topics, [entry({ title: "  certificate of INSURANCE " })])).toHaveLength(
      1,
    );
  });

  it("treats a deactivated entry as answered", () => {
    // Re-suggesting it would invite a duplicate title, which the backend rejects.
    expect(uncoveredStarters(topics, [entry({ is_active: false })])).toHaveLength(1);
  });

  it("reports progress", () => {
    expect(coverage(topics, [])).toEqual({ answered: 0, total: 2 });
    expect(coverage(topics, [entry()])).toEqual({ answered: 1, total: 2 });
  });

  it("suggests everything when nothing is written yet", () => {
    expect(uncoveredStarters(topics, [])).toHaveLength(2);
  });
});

describe("grouping", () => {
  it("groups by category and preserves the server's ordering", () => {
    const groups = groupByCategory([
      entry({ id: "a", category: "access", title: "Stairs" }),
      entry({ id: "b", category: "access", title: "Elevators" }),
      entry({ id: "c", category: "policy", title: "Cancellation" }),
    ]);
    expect(groups.map((g) => g.category)).toEqual(["access", "policy"]);
    expect(groups[0].entries.map((e) => e.title)).toEqual(["Stairs", "Elevators"]);
  });

  it("handles an empty list", () => {
    expect(groupByCategory([])).toEqual([]);
  });

  it("labels categories readably", () => {
    expect(categoryLabel("special_items")).toBe("Special items");
    expect(categoryLabel("insurance")).toBe("Insurance");
  });
});

describe("error copy", () => {
  const apiError = (code: string, status: number) =>
    new ApiError(code, "internal detail that must not be shown", status);

  it("explains a duplicate title", () => {
    expect(knowledgeErrorMessage(apiError("conflict", 409))).toContain("already have an entry");
  });

  it("explains a permission failure", () => {
    expect(knowledgeErrorMessage(apiError("forbidden", 403))).toContain("owners and admins");
  });

  it("explains an entry deleted elsewhere", () => {
    expect(knowledgeErrorMessage(apiError("not_found", 404))).toContain("no longer exists");
  });

  it("never leaks the backend's raw message", () => {
    for (const code of ["conflict", "validation_error", "forbidden", "not_found", "boom"]) {
      const message = knowledgeErrorMessage(apiError(code, 400));
      expect(message).not.toContain("internal detail");
    }
    expect(knowledgeErrorMessage(new Error("kaboom"))).toBe("Something went wrong. Please try again.");
  });
});

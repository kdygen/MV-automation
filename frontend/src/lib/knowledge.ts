/**
 * Pure logic for the Agent Knowledge dashboard: drafts, validation, grouping, and
 * onboarding coverage.
 *
 * Kept free of React and `fetch` so it can be unit-tested in the node-only vitest
 * environment, matching `chat.ts` and `quote-form.ts`.
 *
 * Validation here mirrors the backend's schema rather than replacing it: the server is
 * the authority, this just avoids a round trip to learn that a field is blank. The
 * limits below must stay in step with `app/schemas/knowledge.py`.
 */

import { ApiError } from "./api";
import type { KnowledgeEntry, KnowledgeEntryPayload, StarterTopic } from "./dashboard-types";

export const MAX_CATEGORY_LENGTH = 50;
export const MAX_TITLE_LENGTH = 200;
export const MAX_CONTENT_LENGTH = 4000;
export const MAX_KEYWORDS_LENGTH = 500;

/** Suggested categories, mirroring `KNOWLEDGE_CATEGORIES` in the backend service. */
export const KNOWLEDGE_CATEGORIES = [
  "insurance",
  "packing",
  "special_items",
  "policy",
  "access",
  "payment",
  "service_area",
  "scope",
] as const;

/** The form's working copy. `id` is null while creating, set while editing. */
export interface KnowledgeDraft {
  id: string | null;
  category: string;
  title: string;
  content: string;
  keywords: string;
  is_active: boolean;
}

export type DraftErrors = Partial<Record<"category" | "title" | "content" | "keywords", string>>;

export function emptyDraft(): KnowledgeDraft {
  return {
    id: null,
    category: KNOWLEDGE_CATEGORIES[0],
    title: "",
    content: "",
    keywords: "",
    is_active: true,
  };
}

export function draftFromEntry(entry: KnowledgeEntry): KnowledgeDraft {
  return {
    id: entry.id,
    category: entry.category,
    title: entry.title,
    content: entry.content,
    keywords: entry.keywords ?? "",
    is_active: entry.is_active,
  };
}

/**
 * Pre-fill a draft from an onboarding template.
 *
 * Category, title, and search keywords come from the template; `content` stays empty
 * on purpose — the answer is the company's to write, and pre-filling one would put
 * words in their mouth that the agent would then repeat as policy.
 */
export function draftFromStarter(topic: StarterTopic): KnowledgeDraft {
  return {
    id: null,
    category: topic.category,
    title: topic.title,
    content: "",
    keywords: topic.keywords,
    is_active: true,
  };
}

export function draftErrors(draft: KnowledgeDraft): DraftErrors {
  const errors: DraftErrors = {};
  const check = (
    field: "category" | "title" | "content",
    value: string,
    label: string,
    max: number,
  ) => {
    const trimmed = value.trim();
    if (!trimmed) errors[field] = `${label} is required.`;
    else if (trimmed.length > max)
      errors[field] = `${label} must be ${max.toLocaleString()} characters or fewer.`;
  };

  check("category", draft.category, "Category", MAX_CATEGORY_LENGTH);
  check("title", draft.title, "Title", MAX_TITLE_LENGTH);
  check("content", draft.content, "Answer", MAX_CONTENT_LENGTH);

  if (draft.keywords.trim().length > MAX_KEYWORDS_LENGTH) {
    errors.keywords = `Keywords must be ${MAX_KEYWORDS_LENGTH} characters or fewer.`;
  }
  return errors;
}

export function canSaveDraft(draft: KnowledgeDraft, busy: boolean): boolean {
  return !busy && Object.keys(draftErrors(draft)).length === 0;
}

/** Build the request body. Trims everything; sends `null` for cleared keywords. */
export function draftToPayload(draft: KnowledgeDraft): KnowledgeEntryPayload {
  const keywords = draft.keywords.trim();
  return {
    category: draft.category.trim(),
    title: draft.title.trim(),
    content: draft.content.trim(),
    keywords: keywords || null,
    is_active: draft.is_active,
  };
}

const normalizeTitle = (title: string) => title.trim().toLowerCase();

/**
 * Templates the company has not written an answer for yet.
 *
 * Matched on title, including deactivated entries: a topic that exists but is switched
 * off has been answered, and re-suggesting it would invite a duplicate that the
 * backend's uniqueness rule would then reject.
 */
export function uncoveredStarters(
  topics: StarterTopic[],
  entries: KnowledgeEntry[],
): StarterTopic[] {
  const answered = new Set(entries.map((entry) => normalizeTitle(entry.title)));
  return topics.filter((topic) => !answered.has(normalizeTitle(topic.title)));
}

export function coverage(
  topics: StarterTopic[],
  entries: KnowledgeEntry[],
): { answered: number; total: number } {
  return {
    answered: topics.length - uncoveredStarters(topics, entries).length,
    total: topics.length,
  };
}

/** Group entries for display. Input order is preserved, so the API's sort carries through. */
export function groupByCategory(
  entries: KnowledgeEntry[],
): { category: string; entries: KnowledgeEntry[] }[] {
  const groups = new Map<string, KnowledgeEntry[]>();
  for (const entry of entries) {
    const bucket = groups.get(entry.category);
    if (bucket) bucket.push(entry);
    else groups.set(entry.category, [entry]);
  }
  return [...groups].map(([category, grouped]) => ({ category, entries: grouped }));
}

/** "special_items" → "Special items". Free-text categories pass through readably. */
export function categoryLabel(category: string): string {
  const words = category.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : category;
}

/** Customer-safe copy for a failed save. Never surfaces a raw status or stack. */
export function knowledgeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "conflict") return "You already have an entry with that title.";
    if (error.code === "validation_error") return "Please check the highlighted fields.";
    if (error.code === "forbidden") return "Only owners and admins can change knowledge entries.";
    if (error.code === "not_found") return "That entry no longer exists. Refresh to see the latest.";
    if (error.code === "network_error") return "Could not reach the server. Please try again.";
  }
  return "Something went wrong. Please try again.";
}

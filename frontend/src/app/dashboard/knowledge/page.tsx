"use client";

/**
 * Agent Knowledge: the answers the post-quote sales assistant is allowed to give.
 *
 * Everything the assistant tells a customer about company policy comes from this list.
 * If a topic is not here, the assistant says it does not know rather than guessing —
 * so the page leads with the topics that are still unanswered.
 *
 * All state is local and refetched after each write; there is no client-side cache to
 * fall out of step with the server. Validation logic lives in `@/lib/knowledge` so it
 * can be tested without a DOM.
 */

import { useCallback, useEffect, useState } from "react";

import {
  createKnowledge,
  deleteKnowledge,
  listKnowledge,
  listStarterTopics,
  updateKnowledge,
} from "@/lib/dashboard-api";
import type { KnowledgeEntry, StarterTopic } from "@/lib/dashboard-types";
import {
  KNOWLEDGE_CATEGORIES,
  MAX_CONTENT_LENGTH,
  type KnowledgeDraft,
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
} from "@/lib/knowledge";
import { EmptyState, Loading, PageHeader } from "@/components/dashboard";
import { Button, Card, Checkbox, ErrorBanner, Field, Select, TextArea, TextInput } from "@/components/ui";

export default function KnowledgePage() {
  const [entries, setEntries] = useState<KnowledgeEntry[] | null>(null);
  const [topics, setTopics] = useState<StarterTopic[]>([]);
  const [draft, setDraft] = useState<KnowledgeDraft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(
    () => listKnowledge().then(setEntries),
    [],
  );

  useEffect(() => {
    Promise.all([listKnowledge(), listStarterTopics()])
      .then(([loaded, starters]) => {
        setEntries(loaded);
        setTopics(starters);
      })
      .catch(() => setLoadError("Could not load your knowledge entries."));
  }, []);

  const openDraft = (next: KnowledgeDraft) => {
    setSaveError(null);
    setDraft(next);
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setSaveError(null);
    try {
      const payload = draftToPayload(draft);
      if (draft.id) await updateKnowledge(draft.id, payload);
      else await createKnowledge(payload);
      await reload();
      setDraft(null);
    } catch (err) {
      setSaveError(knowledgeErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  /** Optimism would be wrong here: a rejected toggle must not look like it worked. */
  const toggleActive = async (entry: KnowledgeEntry) => {
    setSaveError(null);
    try {
      await updateKnowledge(entry.id, { is_active: !entry.is_active });
      await reload();
    } catch (err) {
      setSaveError(knowledgeErrorMessage(err));
    }
  };

  const remove = async (entry: KnowledgeEntry) => {
    if (!window.confirm(`Delete "${entry.title}"? This cannot be undone.`)) return;
    setSaveError(null);
    try {
      await deleteKnowledge(entry.id);
      if (draft?.id === entry.id) setDraft(null);
      await reload();
    } catch (err) {
      setSaveError(knowledgeErrorMessage(err));
    }
  };

  if (loadError) return <ErrorBanner message={loadError} />;
  if (!entries) return <Loading />;

  const remaining = uncoveredStarters(topics, entries);
  const { answered, total } = coverage(topics, entries);
  const groups = groupByCategory(entries);

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="Agent Knowledge">
        <Button onClick={() => openDraft(emptyDraft())} disabled={busy}>
          Add entry
        </Button>
      </PageHeader>

      <p className="-mt-2 text-sm text-slate-600">
        Your assistant answers customer policy questions using only what you write here. If a
        topic is missing, it says it doesn&apos;t know instead of guessing.
      </p>

      {saveError ? <ErrorBanner message={saveError} /> : null}

      {draft ? (
        <EntryForm
          draft={draft}
          busy={busy}
          onChange={setDraft}
          onSave={save}
          onCancel={() => setDraft(null)}
        />
      ) : null}

      {topics.length > 0 ? (
        <StarterTopics
          remaining={remaining}
          answered={answered}
          total={total}
          onPick={(topic) => openDraft(draftFromStarter(topic))}
        />
      ) : null}

      {entries.length === 0 ? (
        <EmptyState message="No entries yet. Start with a suggested question above." />
      ) : (
        groups.map((group) => (
          <section key={group.category}>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
              {categoryLabel(group.category)}
            </h2>
            <div className="space-y-3">
              {group.entries.map((entry) => (
                <EntryCard
                  key={entry.id}
                  entry={entry}
                  onEdit={() => openDraft(draftFromEntry(entry))}
                  onToggle={() => toggleActive(entry)}
                  onDelete={() => remove(entry)}
                />
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}

function StarterTopics({
  remaining,
  answered,
  total,
  onPick,
}: {
  remaining: StarterTopic[];
  answered: number;
  total: number;
  onPick: (topic: StarterTopic) => void;
}) {
  return (
    <Card>
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Common customer questions</h2>
        <span className="text-xs text-slate-500">
          {answered} of {total} answered
        </span>
      </div>
      {remaining.length === 0 ? (
        <p className="mt-3 text-sm text-green-700">
          You&apos;ve covered every suggested topic. Add your own entries any time.
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-slate-600">
            Pick one to write your own answer. These are prompts only — the wording is yours.
          </p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {remaining.map((topic) => (
              <li key={topic.title}>
                <button
                  type="button"
                  onClick={() => onPick(topic)}
                  className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-left text-sm text-slate-700 hover:border-blue-400 hover:text-blue-700"
                >
                  {topic.prompt}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function EntryCard({
  entry,
  onEdit,
  onToggle,
  onDelete,
}: {
  entry: KnowledgeEntry;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`rounded-xl border bg-white p-4 ${
        entry.is_active ? "border-slate-200" : "border-slate-200 bg-slate-50"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-medium text-slate-900">
            {entry.title}
            {entry.is_active ? null : (
              <span className="ml-2 rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600">
                off
              </span>
            )}
          </p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{entry.content}</p>
          {entry.keywords ? (
            <p className="mt-2 text-xs text-slate-400">Also matches: {entry.keywords}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={entry.is_active}
              onChange={onToggle}
              aria-label={`${entry.is_active ? "Deactivate" : "Activate"} ${entry.title}`}
              className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            />
            Active
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onEdit}
              className="text-xs font-medium text-blue-600 hover:text-blue-800"
            >
              Edit
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="text-xs font-medium text-red-600 hover:text-red-800"
            >
              Delete
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function EntryForm({
  draft,
  busy,
  onChange,
  onSave,
  onCancel,
}: {
  draft: KnowledgeDraft;
  busy: boolean;
  onChange: (draft: KnowledgeDraft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const errors = draftErrors(draft);
  // Errors are shown per field only once the field has been touched, so a freshly
  // opened form isn't already red.
  const [touched, setTouched] = useState(false);
  const update = (patch: Partial<KnowledgeDraft>) => {
    setTouched(true);
    onChange({ ...draft, ...patch });
  };
  const errorFor = (field: keyof typeof errors) => (touched ? errors[field] : undefined);

  const isKnownCategory = (KNOWLEDGE_CATEGORIES as readonly string[]).includes(draft.category);

  return (
    <Card>
      <div className="space-y-4">
        <h2 className="text-sm font-semibold text-slate-900">
          {draft.id ? "Edit entry" : "New entry"}
        </h2>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="Category" error={errorFor("category")}>
            <Select
              value={isKnownCategory ? draft.category : ""}
              onChange={(e) => update({ category: e.target.value })}
            >
              {isKnownCategory ? null : <option value="">{draft.category || "Choose…"}</option>}
              {KNOWLEDGE_CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {categoryLabel(category)}
                </option>
              ))}
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Field label="Title" error={errorFor("title")}>
              <TextInput
                value={draft.title}
                placeholder="Certificate of Insurance"
                onChange={(e) => update({ title: e.target.value })}
              />
            </Field>
          </div>
        </div>

        <Field label="Answer (what the assistant will tell customers)" error={errorFor("content")}>
          <TextArea
            rows={4}
            value={draft.content}
            maxLength={MAX_CONTENT_LENGTH}
            placeholder="Write this in your own words, as if you were answering the customer."
            onChange={(e) => update({ content: e.target.value })}
          />
        </Field>

        <Field
          label="Other words customers might use (optional, comma-separated)"
          error={errorFor("keywords")}
        >
          <TextInput
            value={draft.keywords}
            placeholder="COI, certificate of insurance, building management"
            onChange={(e) => update({ keywords: e.target.value })}
          />
        </Field>

        <Checkbox
          label="Active — the assistant may use this answer"
          checked={draft.is_active}
          onChange={(e) => update({ is_active: e.target.checked })}
        />

        <div className="flex items-center gap-3">
          <Button onClick={onSave} disabled={!canSaveDraft(draft, busy)}>
            {busy ? "Saving…" : draft.id ? "Save changes" : "Add entry"}
          </Button>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        </div>
      </div>
    </Card>
  );
}

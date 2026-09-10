"use client";

/**
 * Post-quote chat panel for the customer's quote page.
 *
 * Owns all of its own state, so mounting it does not change how the quote itself
 * loads, accepts, or declines. All behavioural logic (validation, error copy) lives
 * in `lib/chat.ts`; this file is the rendering layer.
 *
 * Deliberately simple for V1: no streaming, no markdown, no history fetch, no
 * persistence. The opening greeting is local, so opening the page costs nothing.
 */

import { useEffect, useRef, useState } from "react";

import { sendQuoteChatMessage } from "@/lib/api";
import {
  type ChatMessage,
  MAX_CHAT_MESSAGE_LENGTH,
  SUGGESTIONS,
  canSend,
  chatErrorMessage,
  draftError,
  initialMessages,
  nextMessageId,
} from "@/lib/chat";
import { Button } from "@/components/ui";

export function ChatPanel({ token }: { token: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryText, setRetryText] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  // Scroll the transcript itself, never the window — the customer should not be
  // yanked away from their quote when a reply arrives.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  const send = async (text: string) => {
    if (sending || draftError(text) !== null) return;

    setMessages((current) => [
      ...current,
      { id: nextMessageId("user"), role: "user", text },
    ]);
    setDraft("");
    setError(null);
    setRetryText(null);
    setSending(true);

    try {
      const { reply } = await sendQuoteChatMessage(token, text);
      setMessages((current) => [
        ...current,
        { id: nextMessageId("assistant"), role: "assistant", text: reply },
      ]);
    } catch (err) {
      // No fabricated assistant turn: the customer's message stays visible and the
      // failure is shown for what it is, with a way to retry.
      setError(chatErrorMessage(err));
      setRetryText(text);
    } finally {
      setSending(false);
    }
  };

  const overLimit = draft.length > MAX_CHAT_MESSAGE_LENGTH;
  const showSuggestions = messages.every((m) => m.role === "assistant") && !sending;

  return (
    <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
      <h2 className="text-base font-semibold text-slate-900">Questions about your move?</h2>
      <p className="mt-1 text-xs text-slate-500">
        Answers come from your quote details. For changes or booking, contact the company.
      </p>

      <div
        ref={transcriptRef}
        role="log"
        aria-live="polite"
        aria-label="Conversation"
        className="mt-4 max-h-80 space-y-3 overflow-y-auto"
      >
        {messages.map((message) => (
          <div
            key={message.id}
            className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
          >
            <p
              className={
                message.role === "user"
                  ? "max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-blue-600 px-4 py-2 text-sm text-white"
                  : "max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-bl-sm bg-slate-100 px-4 py-2 text-sm text-slate-800"
              }
            >
              <span className="sr-only">
                {message.role === "user" ? "You said: " : "Assistant said: "}
              </span>
              {message.text}
            </p>
          </div>
        ))}

        {sending ? (
          <div className="flex justify-start">
            <p className="rounded-2xl rounded-bl-sm bg-slate-100 px-4 py-2 text-sm text-slate-500">
              Thinking…
            </p>
          </div>
        ) : null}
      </div>

      {showSuggestions ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => send(suggestion)}
              className="rounded-full border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-200"
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}

      {error ? (
        <div
          role="alert"
          className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
        >
          {error}
          {retryText ? (
            <button
              type="button"
              onClick={() => send(retryText)}
              className="ml-2 font-semibold underline focus:outline-none focus:ring-2 focus:ring-red-300"
            >
              Try again
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4">
        <label htmlFor="chat-message" className="sr-only">
          Ask a question about your move
        </label>
        <div className="flex items-end gap-2">
          <textarea
            id="chat-message"
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter adds a newline.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(draft.trim());
              }
            }}
            disabled={sending}
            placeholder="Ask about your quote, crew, or move date…"
            aria-describedby={overLimit ? "chat-limit" : undefined}
            aria-invalid={overLimit || undefined}
            className="w-full resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:bg-slate-50"
          />
          <Button
            onClick={() => void send(draft.trim())}
            disabled={!canSend(draft, sending)}
            aria-label="Send message"
          >
            {sending ? "Sending…" : "Send"}
          </Button>
        </div>

        {overLimit ? (
          <p id="chat-limit" role="status" className="mt-1 text-xs text-red-600">
            {draft.length.toLocaleString()} / {MAX_CHAT_MESSAGE_LENGTH.toLocaleString()} characters
            — please shorten your message.
          </p>
        ) : null}
      </div>
    </section>
  );
}

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChatError, streamChat } from "./api";
import { AssistantBubble } from "./components/AssistantBubble";
import { ArrowUp, Plus } from "./components/icons";
import { isNearBottom } from "./scroll";
import type { AssistantMessage, Message } from "./types";

const EXAMPLES = [
  "Why did range drop on VIN-1042 this week?",
  "What does error code ERR_401 mean?",
  "What is the maximum discount on a fleet deal?",
];

const newId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState(newId);
  const [busy, setBusy] = useState(false);

  const listRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const inFlight = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Follow the answer as it grows, unless the reader has scrolled up.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => () => inFlight.current?.abort(), []);

  const update = useCallback((id: string, patch: (m: AssistantMessage) => AssistantMessage) => {
    setMessages((all) =>
      all.map((m) => (m.id === id && m.role === "assistant" ? patch(m) : m)),
    );
  }, []);

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || busy) return;
      const answerId = newId();
      setMessages((all) => [
        ...all,
        { id: newId(), role: "user", text: q },
        { id: answerId, role: "assistant", question: q, text: "", status: "working",
          progress: null, done: null, error: null },
      ]);
      setInput("");
      setBusy(true);
      stick.current = true;
      const controller = new AbortController();
      inFlight.current = controller;

      try {
        await streamChat(q, sessionId, {
          onProgress: (p) => update(answerId, (m) => ({ ...m, progress: p })),
          onToken: (t) => update(answerId, (m) => ({ ...m, status: "streaming", text: m.text + t })),
          onReset: () => update(answerId, (m) => ({ ...m, text: "", status: "working" })),
          onDone: (d) => update(answerId, (m) => ({ ...m, status: "done", text: d.answer, done: d })),
        }, controller.signal);
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        const message = err instanceof ChatError
          ? err.message
          : "Could not reach the assistant. Check your connection and try again.";
        update(answerId, (m) => ({ ...m, status: "error", error: message }));
        // Never lose what the user typed: put the question back, unless
        // they have already started typing something else.
        setInput((current) => current || q);
      } finally {
        if (inFlight.current === controller) {
          inFlight.current = null;
          setBusy(false);
        }
      }
    },
    [busy, sessionId, update],
  );

  function newConversation() {
    inFlight.current?.abort();
    inFlight.current = null;
    setBusy(false);
    setMessages([]);
    setInput("");
    setSessionId(newId());
    stick.current = true;
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void ask(input);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1 className="topbar__title">
          <span className="topbar__mark" aria-hidden="true" />
          Ops Copilot
        </h1>
        {messages.length > 0 && (
          <button type="button" className="topbar__new" onClick={newConversation}
            aria-label="New conversation">
            <Plus />
            <span className="topbar__new-text">New conversation</span>
          </button>
        )}
      </header>

      <main
        className="thread"
        ref={listRef}
        onScroll={(e) => {
          stick.current = isNearBottom(e.currentTarget);
        }}
      >
        {messages.length === 0 ? (
          <section className="empty">
            <h2 className="empty__title">What would you like to know?</h2>
            <p className="empty__lead">Ask about a vehicle, an error code, or how sales are going.</p>
            <div className="empty__examples">
              {EXAMPLES.map((q) => (
                <button key={q} type="button" className="example" onClick={() => void ask(q)}>
                  {q}
                </button>
              ))}
            </div>
          </section>
        ) : (
          messages.map((m) =>
            m.role === "user" ? (
              <article key={m.id} className="bubble bubble--user">
                <p>{m.text}</p>
              </article>
            ) : (
              <AssistantBubble key={m.id} message={m} onRetry={(q) => void ask(q)} />
            ),
          )
        )}
      </main>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(input);
        }}
      >
        <label htmlFor="question" className="visually-hidden">
          Your question
        </label>
        <textarea
          id="question"
          ref={inputRef}
          rows={1}
          placeholder="Ask a question…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" disabled={busy || !input.trim()} aria-label="Send">
          <ArrowUp />
        </button>
      </form>
    </div>
  );
}

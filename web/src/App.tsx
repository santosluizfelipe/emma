import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { api, ChatResponse, Health, Turn } from "./api";
import { Answer } from "./components/Answer";
import { EmptyState } from "./components/EmptyState";
import { Header } from "./components/Header";
import { RecordPanel } from "./components/RecordPanel";

interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;
  error?: string;
}

const THINKING_STEPS = ["Reading the question", "Checking risk signals", "Reading records", "Checking citations"];

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [thinkingStep, setThinkingStep] = useState(0);
  const [openRef, setOpenRef] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setHealth({ ok: false, error: e.message }));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  useEffect(() => {
    if (!loading) return;
    setThinkingStep(0);
    const timer = setInterval(() => setThinkingStep((s) => Math.min(s + 1, THINKING_STEPS.length - 1)), 2500);
    return () => clearInterval(timer);
  }, [loading]);

  async function ask(question: string) {
    const text = question.trim();
    if (!text || loading) return;

    // Only plain question/answer text goes back as history; the server re-fetches data.
    const history: Turn[] = messages
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.content }));

    setMessages((prev) => [...prev, { id: nextId.current++, role: "user", content: text }]);
    setInput("");
    setLoading(true);
    try {
      const response = await api.chat(text, history);
      setMessages((prev) => [
        ...prev,
        { id: nextId.current++, role: "assistant", content: response.answer, response },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { id: nextId.current++, role: "assistant", content: "", error: (e as Error).message },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void ask(input);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(input);
    }
  }

  return (
    <div className={`layout ${openRef ? "with-panel" : ""}`}>
      <div className="main">
        <Header health={health} onReset={messages.length ? () => setMessages([]) : undefined} />

        <main className="conversation" aria-live="polite">
          {messages.length === 0 && <EmptyState onPick={ask} disabled={loading} />}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="bubble user">
                {m.content}
              </div>
            ) : m.error ? (
              <div key={m.id} className="bubble error" role="alert">
                <strong>Something went wrong.</strong> {m.error}
              </div>
            ) : (
              <Answer key={m.id} response={m.response!} onOpenRef={setOpenRef} activeRef={openRef} />
            ),
          )}

          {loading && (
            <div className="thinking">
              <span className="spinner" aria-hidden />
              {THINKING_STEPS[thinkingStep]}…
            </div>
          )}
          <div ref={bottomRef} />
        </main>

        <form className="composer" onSubmit={onSubmit}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about a service user, risks, visits or safeguarding…"
            rows={2}
            maxLength={2000}
            disabled={loading}
            aria-label="Your question"
          />
          <button type="submit" disabled={loading || !input.trim()}>
            Ask
          </button>
        </form>
      </div>

      {openRef && <RecordPanel refId={openRef} onClose={() => setOpenRef(null)} />}
    </div>
  );
}

import { useEffect, useState } from "react";
import { api, FormRecord, RecordDetail, Signal, VisitRecord } from "../api";

function escapeRegExp(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Highlight the sentences the extractor used as evidence
function Highlighted({ text, quotes }: { text: string; quotes: string[] }) {
  const usable = quotes.map((q) => q.trim().replace(/\.$/, "")).filter((q) => q.length > 3);
  if (!usable.length) return <>{text}</>;
  const parts = text.split(new RegExp(`(${usable.map(escapeRegExp).join("|")})`, "gi"));
  return (
    <>
      {parts.map((part, i) =>
        usable.some((q) => q.toLowerCase() === part.toLowerCase()) ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>,
      )}
    </>
  );
}

function SignalList({ signals }: { signals: Signal[] }) {
  if (!signals.length) return <p className="muted">No risk signals were extracted from this visit.</p>;
  return (
    <ul className="signals">
      {signals.map((s, i) => (
        <li key={i}>
          <span className={`severity ${s.severity}`}>{s.severity}</span>
          <span className="category">{s.category.replace("_", " ")}</span>
          {s.escalated ? <span className="escalated">escalated</span> : null}
        </li>
      ))}
    </ul>
  );
}

function Visit({ record }: { record: VisitRecord }) {
  return (
    <>
      <p className="meta">
        <strong>{record.service_user}</strong>
        <br />
        {record.start.slice(0, 16)}
        {record.end ? `–${record.end.slice(11, 16)}` : ""} · carer {record.carer ?? "unknown"}
      </p>
      <h4>Signals</h4>
      <SignalList signals={record.signals} />
      <h4>Visit note</h4>
      <p className="note">
        <Highlighted text={record.note} quotes={record.signals.map((s) => s.evidence)} />
      </p>
      <h4>Tasks</h4>
      <ul className="tasks">
        {record.tasks.map((t, i) => (
          <li key={i}>
            <strong>{t.name}</strong> <span className="muted">({t.status})</span>
            <br />
            {t.comment ?? <em className="warn">No comment recorded</em>}
          </li>
        ))}
      </ul>
    </>
  );
}

function Form({ record }: { record: FormRecord }) {
  const sections = new Map<string, FormRecord["answers"]>();
  record.answers.forEach((a) => sections.set(a.section, [...(sections.get(a.section) ?? []), a]));
  return (
    <>
      <p className="meta">
        <strong>{record.form}</strong> · created {record.created_at}
      </p>
      {[...sections.entries()].map(([section, answers]) => (
        <section key={section}>
          <h4>{section}</h4>
          <dl>
            {answers.map((a, i) => (
              <div key={i}>
                <dt>{a.question}</dt>
                <dd>{a.answer}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </>
  );
}

export function RecordPanel({ refId, onClose }: { refId: string; onClose: () => void }) {
  const [record, setRecord] = useState<RecordDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRecord(null);
    setError(null);
    api
      .record(refId)
      .then((r) => !cancelled && setRecord(r))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [refId]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside className="panel" aria-label={`Record ${refId}`}>
      <div className="panel-header">
        <h3>
          {refId.startsWith("V") ? "Visit" : "Form"} {refId}
          {record?.type === "form" && <span className="panel-title"> · {record.title}</span>}
        </h3>
        <button className="ghost" onClick={onClose} aria-label="Close record">
          ✕
        </button>
      </div>
      <div className="panel-body">
        {error && <p className="error-text">{error}</p>}
        {!record && !error && <p className="muted">Loading…</p>}
        {record?.type === "visit" && <Visit record={record} />}
        {record?.type === "form" && <Form record={record} />}
      </div>
    </aside>
  );
}

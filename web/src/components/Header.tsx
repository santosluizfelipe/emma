import { Health } from "../api";

function formatDate(value?: string) {
  if (!value) return "";
  const d = new Date(value.replace(" ", "T"));
  return isNaN(d.getTime())
    ? value
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function Header({ health, onReset }: { health: Health | null; onReset?: () => void }) {
  return (
    <header className="header">
      <div>
        <h1>Care Data Agent</h1>
        <p className="subtitle">Ask how service users are doing and where the risks are.</p>
      </div>
      <div className="header-right">
        {health === null ? (
          <span className="status muted">Connecting…</span>
        ) : health.ok ? (
          <span className="status" title={`extractor: ${health.extractor ?? "none"} · vectors: ${health.vector_backend ?? "off"}`}>
            <span className="dot ok" aria-hidden /> Data as of {formatDate(health.as_of)} · {health.visits} visits
            {!health.llm_configured && <span className="warn"> · no API key</span>}
          </span>
        ) : (
          <span className="status error-text">
            <span className="dot bad" aria-hidden /> Backend unavailable: {health.error}
          </span>
        )}
        {onReset && (
          <button className="ghost" onClick={onReset}>
            New conversation
          </button>
        )}
      </div>
    </header>
  );
}

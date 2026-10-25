import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatResponse, ToolCall } from "../api";

// [V205] -> a markdown link we render as a clickable chip
function linkCitations(text: string) {
  return text.replace(/\[([VF]\d+)\]/g, "[$1](#cite-$1)");
}

const TOOL_LABELS: Record<string, string> = {
  find_service_user: "Looked up a person",
  risk_overview: "Ranked risk across service users",
  signal_timeline: "Read a risk timeline",
  get_visit_notes: "Read visit notes",
  get_care_plan: "Read a care plan",
  safeguarding_cases: "Checked safeguarding cases",
  record_issues: "Checked documentation issues",
  search_records: "Searched records",
  query_sql: "Ran a database query",
};

function describe(call: ToolCall) {
  const args = Object.entries(call.arguments)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
  return { label: TOOL_LABELS[call.tool] ?? call.tool, detail: `${call.tool}(${args})` };
}

interface Props {
  response: ChatResponse;
  activeRef: string | null;
  onOpenRef: (ref: string) => void;
}

export function Answer({ response, activeRef, onOpenRef }: Props) {
  const labels = new Map(response.citations.map((c) => [c.ref, c.label]));

  return (
    <article className="bubble assistant">
      <div className="markdown">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              if (href?.startsWith("#cite-")) {
                const ref = href.slice(6);
                return (
                  <button
                    type="button"
                    className={`chip ${ref.startsWith("V") ? "visit" : "form"} ${activeRef === ref ? "active" : ""}`}
                    onClick={() => onOpenRef(ref)}
                    title={labels.get(ref) ?? ref}
                  >
                    {ref}
                  </button>
                );
              }
              return (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              );
            },
          }}
        >
          {linkCitations(response.answer)}
        </ReactMarkdown>
      </div>

      {response.removed_citations.length > 0 && (
        <p className="notice">
          Removed {response.removed_citations.length} citation(s) that weren't backed by retrieved records:{" "}
          {response.removed_citations.join(", ")}.
        </p>
      )}

      <footer className="answer-footer">
        {response.citations.length > 0 && (
          <span className="muted">
            {response.citations.length} source{response.citations.length > 1 ? "s" : ""}
          </span>
        )}
        {response.tool_calls.length > 0 && (
          <details className="trace">
            <summary>How this was answered ({response.tool_calls.length} steps)</summary>
            <ol>
              {response.tool_calls.map((call, i) => {
                const { label, detail } = describe(call);
                return (
                  <li key={i}>
                    {label} <code>{detail}</code>
                    {call.error && <span className="error-text"> ({call.error})</span>}
                  </li>
                );
              })}
            </ol>
          </details>
        )}
      </footer>
    </article>
  );
}

import { useEffect, useState } from "react";
import { api, Briefing } from "../api";

const SUGGESTIONS = [
  "Who should I worry about this week?",
  "How is Doris Whitfield doing?",
  "What's outstanding on safeguarding?",
  "Has anyone had a fall recently?",
  "Which tasks were marked completed without a comment?",
  "How many visits did each carer do last week?",
];

const ALERT_LABELS = { immediate: "Serious event", trend: "Worsening trend", none: "Earlier signal" };

interface Props {
  onPick: (q: string) => void;
  disabled: boolean;
  onOpenRef: (ref: string) => void;
  activeRef: string | null;
}

// Who needs attention, read straight from the risk tables: shown before anyone asks
function NeedsAttention({ briefing, onPick, disabled, onOpenRef, activeRef }: Props & { briefing: Briefing }) {
  const gaps = briefing.issues["completed_without_comment"] ?? 0;
  return (
    <section className="briefing" aria-label="Needs attention">
      <h3>Needs attention this week</h3>
      <p className="muted">
        {briefing.alerting.any_alert} of {briefing.service_users_in_scope} people with an alert ·{" "}
        {briefing.safeguarding.total} safeguarding case{briefing.safeguarding.total === 1 ? "" : "s"} ·{" "}
        {gaps} task{gaps === 1 ? "" : "s"} completed without a comment (14 days)
      </p>
      {briefing.people.length === 0 ? (
        <p>No risk signals recorded in the last 7 days.</p>
      ) : (
        <ul className="attention-list">
          {briefing.people.map((p) => (
            <li key={p.service_user_id}>
              <div className="attention-head">
                <strong>{p.name}</strong>
                <span className={`badge ${p.alert}`}>{ALERT_LABELS[p.alert]}</span>
                <span className="muted">{p.categories.map((c) => c.replace("_", " ")).join(", ")}</span>
                <button className="link" onClick={() => onPick(`How is ${p.name} doing?`)} disabled={disabled}>
                  Ask about {p.name.split(" ")[0]}
                </button>
              </div>
              {p.evidence.map((e) => (
                <p key={e.ref + e.category} className="attention-quote">
                  <button
                    type="button"
                    className={`chip visit ${activeRef === e.ref ? "active" : ""}`}
                    onClick={() => onOpenRef(e.ref)}
                  >
                    {e.ref}
                  </button>{" "}
                  {e.day.slice(5)}: “{e.evidence}”
                </p>
              ))}
            </li>
          ))}
        </ul>
      )}
      {briefing.people_total > briefing.people.length && (
        <p className="muted">
          Showing the top {briefing.people.length} of {briefing.people_total} people with signals.
        </p>
      )}
    </section>
  );
}

export function EmptyState(props: Props) {
  const [briefing, setBriefing] = useState<Briefing | null>(null);

  useEffect(() => {
    api.briefing().then(setBriefing).catch(() => setBriefing(null));
  }, []);

  return (
    <div className="empty">
      {briefing && <NeedsAttention briefing={briefing} {...props} />}
      <h2>What would you like to know?</h2>
      <p>
        Answers are built from visit notes, care plans, risk assessments and safeguarding records. Every
        statement links to the record it came from.
      </p>
      <div className="suggestions">
        {SUGGESTIONS.map((q) => (
          <button key={q} className="suggestion" onClick={() => props.onPick(q)} disabled={props.disabled}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

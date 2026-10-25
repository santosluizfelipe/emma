const SUGGESTIONS = [
  "Who should I worry about this week?",
  "How is Doris Whitfield doing?",
  "What's outstanding on safeguarding?",
  "Has anyone had a fall recently?",
  "Which tasks were marked completed without a comment?",
  "How many visits did each carer do last week?",
];

export function EmptyState({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  return (
    <div className="empty">
      <h2>What would you like to know?</h2>
      <p>
        Answers are built from visit notes, care plans, risk assessments and safeguarding records. Every
        statement links to the record it came from.
      </p>
      <div className="suggestions">
        {SUGGESTIONS.map((q) => (
          <button key={q} className="suggestion" onClick={() => onPick(q)} disabled={disabled}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

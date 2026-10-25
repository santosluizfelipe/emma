// Types mirror the FastAPI responses in backend/app/api.py

export type Role = "user" | "assistant";

export interface Turn {
  role: Role;
  content: string;
}

export interface Citation {
  ref: string;
  type: "visit" | "form";
  label: string;
  text: string | null;
}

export interface ToolCall {
  tool: string;
  arguments: Record<string, unknown>;
  error: string | null;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  tool_calls: ToolCall[];
  removed_citations: string[];
  unverified_quotes: string[];
  as_of: string;
}

export interface BriefingEvidence {
  ref: string;
  day: string;
  category: string;
  severity: "low" | "medium" | "high";
  evidence: string;
}

export interface BriefingPerson {
  service_user_id: number;
  name: string;
  alert: "none" | "trend" | "immediate";
  score_now: number | null;
  categories: string[];
  evidence: BriefingEvidence[];
}

export interface Briefing {
  as_of: string;
  people: BriefingPerson[];
  people_total: number;
  alerting: { any_alert: number; immediate: number };
  service_users_in_scope: number;
  safeguarding: { total: number };
  issues: Record<string, number>;
}

export interface Health {
  ok: boolean;
  error?: string;
  as_of?: string;
  visits?: number;
  signals?: number;
  extractor?: string | null;
  embedding_model?: string | null;
  vector_backend?: string | null;
  llm_configured?: boolean;
  agent_model?: string;
}

export interface Signal {
  category: string;
  severity: "low" | "medium" | "high";
  evidence: string;
  escalated: number;
}

export interface VisitRecord {
  type: "visit";
  ref: string;
  service_user: string;
  carer: string | null;
  start: string;
  end: string | null;
  status: string;
  note: string;
  tasks: { name: string; status: string; comment: string | null }[];
  signals: Signal[];
}

export interface FormRecord {
  type: "form";
  ref: string;
  title: string;
  form: string;
  created_at: string;
  answers: { section: string; question: string; answer: string }[];
}

export type RecordDetail = VisitRecord | FormRecord;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // not JSON: keep the status text
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  briefing: () => request<Briefing>("/api/briefing"),
  chat: (message: string, history: Turn[]) =>
    request<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify({ message, history }) }),
  record: (ref: string) => request<RecordDetail>(`/api/records/${encodeURIComponent(ref)}`),
};

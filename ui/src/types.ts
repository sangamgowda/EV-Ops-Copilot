// Shapes of what /chat streams. Mirrors api/schemas.py.

export interface Progress {
  stage: string;
  lap: number | null;
  message: string;
  turn_id?: string;
  session_id?: string;
}

export interface Evidence {
  id: string;
  tool: string;
  status: "ok" | "empty" | "below_threshold" | "failed";
  summary: string;
  lap: number;
  source_doc: string | null;
  sql: string | null;
  query: string | null;
  score: number | null;
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
}

export interface Lap {
  lap: number;
  reasoning: string | null;
  tools: ToolCall[];
  found: string[];
  decision: string | null;
  missing: string[];
  next_question: string | null;
}

export interface Citation {
  claim: string;
  evidence_id: string;
}

export interface Done {
  turn_id: string;
  session_id: string;
  answer: string;
  citations: Citation[];
  confidence: "high" | "medium" | "low";
  gaps: string[];
  iterations: number;
  stop_reason: string | null;
  partial: boolean;
  grounded: boolean | null;
  evidence: Evidence[];
  laps: Lap[];
}

export type Rating = "up" | "down";

export interface UserMessage {
  id: string;
  role: "user";
  text: string;
}

export interface AssistantMessage {
  id: string;
  role: "assistant";
  question: string;
  text: string;
  status: "working" | "streaming" | "done" | "error";
  progress: Progress | null;
  done: Done | null;
  error: string | null;
}

export type Message = UserMessage | AssistantMessage;

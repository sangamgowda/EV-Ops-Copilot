// Plain-language labels for what the agent did. The UI is for ops
// people, not engineers: "Checked the database" rather than
// "structured_query_tool", "Enough evidence" rather than "complete".

import type { Evidence, ToolCall } from "./types";

export function describeTool(call: ToolCall): string {
  if (call.tool === "rag_retrieval_tool") {
    return `Searched the documents for “${String(call.args.query ?? "")}”`;
  }
  if (call.tool === "structured_query_tool") return "Queried the database";
  return call.tool;
}

export function evidenceKind(e: Evidence): string {
  if (e.tool === "rag_retrieval_tool") return "Document";
  if (e.tool === "structured_query_tool") return "Database";
  if (e.tool === "entity_resolution") return "Vehicle check";
  return e.tool;
}

const DECISIONS: Record<string, string> = {
  continue: "Not enough yet — looked further",
  complete: "Enough evidence to answer",
  exhausted: "Nothing more could be found — answered partially",
  cap_reached: "Reached the lap limit — answered with what was found",
  no_new_evidence: "No new evidence — answered with what was found",
  timeout: "Ran out of time — answered with what was found",
};

export function describeDecision(decision: string | null): string {
  return decision ? DECISIONS[decision] ?? decision : "—";
}

const STATUS: Record<Evidence["status"], string> = {
  ok: "",
  empty: "returned nothing",
  below_threshold: "no close match",
  failed: "failed",
};

export function statusNote(e: Evidence): string {
  return STATUS[e.status];
}

// Reflect names missing pieces with short labels ("mechanism"); say
// what each means to a reader.
const GAPS: Record<string, string> = {
  measurement: "The actual measured values",
  baseline: "What the values should normally be",
  mechanism: "A documented explanation of the cause",
  alternative: "Whether another cause was ruled out",
  recommendation: "What to do next",
};

export function describeGap(gap: string): string {
  return GAPS[gap] ?? gap;
}

export function firstLine(text: string): string {
  return text.trim().split("\n")[0] ?? "";
}

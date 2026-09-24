import { SSEParser } from "./sse";
import type { Done, Progress, Rating } from "./types";

export interface StreamHandlers {
  onProgress(p: Progress): void;
  onToken(text: string): void;
  onReset(): void;
  onDone(d: Done): void;
}

export class ChatError extends Error {}

/** Ask a question and stream the answer. Resolves when `done` arrives;
 *  rejects with ChatError if the turn fails or the stream ends without
 *  an answer. An aborted request rejects with the AbortError. */
export async function streamChat(
  question: string,
  sessionId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question, session_id: sessionId, stream: true }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new ChatError(await describeHttpError(res));
  }

  const parser = new SSEParser();
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let finished = false;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const ev of parser.feed(decoder.decode(value, { stream: true }))) {
      const data = JSON.parse(ev.data);
      switch (ev.event) {
        case "progress":
          handlers.onProgress(data as Progress);
          break;
        case "token":
          handlers.onToken(data.text as string);
          break;
        case "reset":
          handlers.onReset();
          break;
        case "done":
          finished = true;
          handlers.onDone(data as Done);
          break;
        case "error":
          throw new ChatError(data.message ?? "The request failed.");
      }
    }
  }
  if (!finished) throw new ChatError("The connection closed before the answer finished.");
}

async function describeHttpError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not JSON */
  }
  return `The server answered ${res.status}.`;
}

export async function sendFeedback(
  turnId: string,
  rating: Rating,
  comment?: string,
): Promise<void> {
  const res = await fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ turn_id: turnId, rating, comment: comment || null }),
  });
  if (!res.ok) throw new ChatError(await describeHttpError(res));
}

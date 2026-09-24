import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../App";
import type { Done } from "../types";

// ── a fake /chat that streams events on command ─────────────

function sse(event: string, data: unknown): string {
  return `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
}

interface FakeStream {
  push(text: string): void;
  end(): void;
}

function streamingResponse(): { response: Response; stream: FakeStream } {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start: (c) => { ctrl = c; } });
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    stream: { push: (t) => ctrl.enqueue(encoder.encode(t)), end: () => ctrl.close() },
  };
}

const DONE: Done = {
  turn_id: "turn-1",
  session_id: "s",
  answer: "Current draw is 36.4% above baseline [e1]. The cause cannot be established [e2].",
  citations: [{ claim: "Current draw is 36.4% above baseline .", evidence_id: "e1" }],
  confidence: "low",
  gaps: ["mechanism"],
  iterations: 1,
  stop_reason: "exhausted",
  partial: true,
  grounded: true,
  evidence: [
    { id: "e1", tool: "structured_query_tool", status: "ok", lap: 1,
      summary: "current_draw: 32.6 A vs baseline 23.9 (+36.4%, above_normal)",
      source_doc: null, sql: "SELECT t.metric_name AS metric FROM vehicle_telemetry t", query: null, score: null },
    { id: "e2", tool: "rag_retrieval_tool", status: "below_threshold", lap: 1,
      summary: "No usable documentation", source_doc: null, sql: null,
      query: "range dropped with high current", score: 0.3 },
  ],
  laps: [{ lap: 1, reasoning: "need readings against baseline", found: ["e1", "e2"],
           tools: [{ tool: "structured_query_tool", args: { sql: "SELECT ..." } }],
           decision: "exhausted", missing: ["mechanism"], next_question: null }],
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

async function askAndStream(user: ReturnType<typeof userEvent.setup>) {
  const { response, stream } = streamingResponse();
  fetchMock.mockResolvedValueOnce(response);
  await user.type(screen.getByLabelText("Your question"), "Why did range drop?");
  await user.click(screen.getByRole("button", { name: "Send" }));
  return stream;
}

// ── tests ───────────────────────────────────────────────────

describe("asking a question", () => {
  it("shows progress, streams the answer with a cursor, then renders the finished answer", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const stream = await askAndStream(user);

    // Loading state from the first moment — never a blank wait.
    expect(await screen.findByRole("status")).toHaveTextContent("Starting");
    act(() => stream.push(sse("progress", { stage: "plan", lap: 1,
      message: "Checking current_draw against normal values" })));
    expect(await screen.findByText(/Checking current_draw against normal values/)).toBeInTheDocument();

    act(() => stream.push(sse("token", { text: "Current draw is " }) + sse("token", { text: "36.4% above" })));
    expect(await screen.findByText(/Current draw is 36.4% above/)).toBeInTheDocument();
    expect(container.querySelector(".cursor")).not.toBeNull();

    act(() => { stream.push(sse("done", DONE)); stream.end(); });
    await screen.findByRole("button", { name: "Source e1" });
    expect(container.querySelector(".cursor")).toBeNull();
  });

  it("makes a partial answer look partial and lists what could not be confirmed", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const stream = await askAndStream(user);
    act(() => { stream.push(sse("done", DONE)); stream.end(); });

    await screen.findByText("What I couldn’t confirm");
    expect(screen.getByText("A documented explanation of the cause")).toBeInTheDocument();
    expect(container.querySelector(".bubble--partial")).not.toBeNull();
  });

  it("opens a citation to show the SQL that ran", async () => {
    const user = userEvent.setup();
    render(<App />);
    const stream = await askAndStream(user);
    act(() => { stream.push(sse("done", DONE)); stream.end(); });

    await user.click(await screen.findByRole("button", { name: "Source e1" }));
    const source = screen.getByRole("complementary", { name: "Source e1" });
    expect(within(source).getByText(/SELECT t.metric_name/)).toBeInTheDocument();
    await user.click(within(source).getByRole("button", { name: "Close source" }));
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("keeps the reasoning folded away until asked for", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const stream = await askAndStream(user);
    act(() => { stream.push(sse("done", DONE)); stream.end(); });

    const summary = await screen.findByText(/How I got this · 1 lap/);
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    await user.click(summary);
    expect(details.open).toBe(true);
    expect(within(details).getByText("Lap 1")).toBeInTheDocument();
    expect(within(details).getByText(/Nothing more could be found/)).toBeInTheDocument();
  });

  it("discards a streamed draft when the server resets it", async () => {
    const user = userEvent.setup();
    render(<App />);
    const stream = await askAndStream(user);
    act(() => stream.push(sse("token", { text: "A draft that failed the check" })));
    await screen.findByText(/A draft that failed the check/);
    act(() => stream.push(sse("reset", { reason: "grounding" })));
    await waitFor(() => expect(screen.queryByText(/A draft that failed the check/)).toBeNull());
  });
});

describe("when something goes wrong", () => {
  it("shows the error, offers a retry, and puts the question back in the input", async () => {
    const user = userEvent.setup();
    render(<App />);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "The model is unavailable." }),
      { status: 503, headers: { "Content-Type": "application/json" } }));
    await user.type(screen.getByLabelText("Your question"), "Why did range drop?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The model is unavailable.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toHaveValue("Why did range drop?");
  });

  it("treats a stream that ends without an answer as an error", async () => {
    const user = userEvent.setup();
    render(<App />);
    const stream = await askAndStream(user);
    act(() => { stream.push(sse("progress", { stage: "plan", lap: 1, message: "x" })); stream.end(); });
    expect(await screen.findByRole("alert")).toHaveTextContent(/closed before the answer finished/);
  });
});

describe("feedback", () => {
  it("rates in one click; a comment appears only after thumbs-down", async () => {
    const user = userEvent.setup();
    render(<App />);
    const stream = await askAndStream(user);
    act(() => { stream.push(sse("done", DONE)); stream.end(); });

    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await user.click(await screen.findByRole("button", { name: "Helpful" }));
    expect(screen.queryByLabelText(/What was wrong/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    const box = screen.getByLabelText(/What was wrong/);
    await user.type(box, "Missed the payload");
    await user.click(screen.getByRole("button", { name: "Send comment" }));

    const feedbackCalls = fetchMock.mock.calls.filter(([url]) => url === "/feedback");
    const bodies = feedbackCalls.map(([, init]) => JSON.parse((init as RequestInit).body as string));
    expect(bodies).toEqual([
      { turn_id: "turn-1", rating: "up", comment: null },
      { turn_id: "turn-1", rating: "down", comment: null },
      { turn_id: "turn-1", rating: "down", comment: "Missed the payload" },
    ]);
    expect(await screen.findByText("Thanks — noted")).toBeInTheDocument();
  });
});

describe("conversation controls", () => {
  it("sends on Enter, not on Shift+Enter", async () => {
    const user = userEvent.setup();
    render(<App />);
    fetchMock.mockResolvedValue(streamingResponse().response);
    const box = screen.getByLabelText("Your question");
    await user.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(fetchMock).not.toHaveBeenCalled();
    await user.type(box, "{Enter}");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("starts a new conversation with a fresh session and an empty thread", async () => {
    const user = userEvent.setup();
    render(<App />);
    const stream = await askAndStream(user);
    act(() => { stream.push(sse("done", DONE)); stream.end(); });
    await screen.findByRole("button", { name: "Source e1" });

    await user.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.getByText(/Ask about a vehicle/)).toBeInTheDocument();

    const second = await askAndStream(user);
    act(() => second.end());
    const sessions = fetchMock.mock.calls
      .filter(([url]) => url === "/chat")
      .map(([, init]) => JSON.parse((init as RequestInit).body as string).session_id);
    expect(sessions).toHaveLength(2);
    expect(sessions[0]).not.toEqual(sessions[1]);
  });
});

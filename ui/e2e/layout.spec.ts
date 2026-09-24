import { expect, test, type Page } from "@playwright/test";

// Phones are the main target. 280px is what a 380px phone gives the page
// at 135% browser zoom — the case that used to spill off the side.
const WIDTHS = [280, 320, 360, 380, 414, 768];

// A deliberately awkward answer: long SQL, a long unbroken token, many
// chips, a partial result with gaps. If any of it can push the page
// wider than the screen, this will.
const LONG_ID = "VIN-1042-REPLACEMENT-UNIT-WITH-AN-UNBROKEN-IDENTIFIER-0000000000";
const DONE = {
  turn_id: "turn-e2e",
  session_id: "s",
  answer:
    `Current draw was 32.8 A, 36.7% above baseline [e1]. Payload averaged 189.7 kg [e2].\n\n` +
    `- Range estimate fell 25.9% [e3]\n- Cell health stayed at 97.7% [e4]\n\n` +
    `The unit ${LONG_ID} was not checked [e5].`,
  citations: [{ claim: "Current draw was 32.8 A", evidence_id: "e1" }],
  confidence: "low",
  gaps: ["mechanism", "alternative"],
  iterations: 2,
  stop_reason: "exhausted",
  partial: true,
  grounded: false,
  evidence: ["e1", "e2", "e3", "e4", "e5"].map((id, i) => ({
    id, tool: i === 4 ? "rag_retrieval_tool" : "structured_query_tool", status: "ok", lap: 1,
    summary: `${id}: a measurement with a long description ${LONG_ID}`,
    source_doc: i === 4 ? "SB-114_sustained_overload" : null,
    sql: i === 4 ? null :
      "SELECT t.metric_name AS metric, round(avg(t.metric_value)::numeric,1) AS actual, " +
      "round(avg(b.nominal_value)::numeric,1) AS baseline FROM vehicle_telemetry t JOIN vehicles v " +
      "ON v.vehicle_id = t.vehicle_id JOIN vehicle_baseline_specs b ON b.model_code = v.model_code " +
      "WHERE t.vehicle_id = 'VIN-1042' AND t.recorded_at >= now() - interval '7 days' GROUP BY 1 LIMIT 10",
    query: i === 4 ? "range dropped and current draw is high with heavy loads" : null,
    score: i === 4 ? 0.7396 : null,
  })),
  laps: [
    { lap: 1, reasoning: "need readings against baseline and the documented mechanism",
      tools: [{ tool: "structured_query_tool", args: {} }, { tool: "rag_retrieval_tool",
        args: { query: "range dropped and current draw is high with heavy loads" } }],
      found: ["e1", "e2", "e3", "e5"], decision: "continue", missing: ["alternative"],
      next_question: "What is the latest cell health for VIN-1042?" },
    { lap: 2, reasoning: "cell health rules degradation in or out", tools: [], found: ["e4"],
      decision: "exhausted", missing: ["mechanism"], next_question: null },
  ],
};

const sse = (event: string, data: unknown) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;

async function mockChat(page: Page) {
  await page.route("**/chat", (route) =>
    route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      body: sse("progress", { stage: "plan", lap: 1, message: "Checking current_draw and payload against normal values" })
        + sse("token", { text: DONE.answer }) + sse("done", DONE),
    }),
  );
}

async function overflow(page: Page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const out: string[] = [];
    for (const el of document.querySelectorAll("body *")) {
      const b = el.getBoundingClientRect();
      if (b.width && b.right > vw + 0.5) out.push(`${el.tagName.toLowerCase()}.${el.className} → ${Math.round(b.right)}px`);
    }
    return { vw, width: document.documentElement.scrollWidth, sticksOut: out.slice(0, 5) };
  });
}

for (const width of WIDTHS) {
  test(`fits the screen at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 760 });
    await mockChat(page);
    await page.goto("/");

    let r = await overflow(page);
    expect(r.sticksOut, "empty screen").toEqual([]);
    expect(r.width).toBeLessThanOrEqual(r.vw);

    await page.getByLabel("Your question").fill(`Why did range drop on ${LONG_ID} this week?`);
    await page.getByRole("button", { name: "Send" }).click();
    await page.getByText(/How I got this/).click();
    await page.getByRole("button", { name: "Source e1" }).click();

    r = await overflow(page);
    expect(r.sticksOut, "answer with sources and reasoning open").toEqual([]);
    expect(r.width).toBeLessThanOrEqual(r.vw);

    // The input stays on screen and usable.
    const box = await page.getByLabel("Your question").boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width && box.y + box.height <= 760).toBe(true);

    await page.screenshot({ path: info.outputPath(`answer-${width}.png`) });
  });
}

import { SSEParser } from "../sse";
import { isNearBottom } from "../scroll";

describe("SSEParser", () => {
  it("parses CRLF events split at arbitrary chunk boundaries", () => {
    const wire =
      'event: progress\r\ndata: {"message":"Reading"}\r\n\r\n' +
      'event: token\r\ndata: {"text":"Hi"}\r\n\r\n';
    for (const size of [1, 3, 7, 50]) {
      const p = new SSEParser();
      const events = [];
      for (let i = 0; i < wire.length; i += size) events.push(...p.feed(wire.slice(i, i + size)));
      expect(events).toEqual([
        { event: "progress", data: '{"message":"Reading"}' },
        { event: "token", data: '{"text":"Hi"}' },
      ]);
    }
  });

  it("ignores keep-alive comments and waits for a complete event", () => {
    const p = new SSEParser();
    expect(p.feed(": ping\n\n")).toEqual([]);
    expect(p.feed("event: done\ndata: {}")).toEqual([]);
    expect(p.feed("\n\n")).toEqual([{ event: "done", data: "{}" }]);
  });
});

describe("scroll anchoring", () => {
  it("sticks only when the reader is at the bottom", () => {
    expect(isNearBottom({ scrollTop: 920, scrollHeight: 1500, clientHeight: 500 })).toBe(true);
    expect(isNearBottom({ scrollTop: 300, scrollHeight: 1500, clientHeight: 500 })).toBe(false);
  });
});

// Server-Sent Events over a POST response.
//
// The browser's EventSource only does GET, and /chat is a POST, so the
// stream is read with fetch and parsed here. Network chunks do not line
// up with events — one chunk can hold half an event, or three — so the
// parser buffers until it sees the blank line that ends an event.
// The server sends CRLF line endings; LF is accepted too.

export interface SSEEvent {
  event: string;
  data: string;
}

export class SSEParser {
  private buffer = "";
  private pendingCR = false;

  feed(chunk: string): SSEEvent[] {
    // A chunk can end between the "\r" and "\n" of one CRLF. Normalising
    // that lone "\r" immediately would turn one line ending into two — a
    // phantom blank line that cuts the event in half and loses its name.
    // Hold it back until the next chunk shows what follows.
    if (this.pendingCR) {
      chunk = "\r" + chunk;
      this.pendingCR = false;
    }
    if (chunk.endsWith("\r")) {
      this.pendingCR = true;
      chunk = chunk.slice(0, -1);
    }
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const out: SSEEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      const parsed = parseBlock(block);
      if (parsed) out.push(parsed);
    }
    return out;
  }
}

function parseBlock(block: string): SSEEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // comment / keep-alive ping
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, data: data.join("\n") } : null;
}

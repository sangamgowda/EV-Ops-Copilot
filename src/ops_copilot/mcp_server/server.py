"""MCP server. Runs as its own process.

Why MCP rather than in-process tools:

  Process isolation. The database credentials live HERE, not in the
  agent. A prompt-injected agent cannot exfiltrate them; it can only
  request a tool call, which still passes validation.
  A single chokepoint for authz, rate limiting, and audit logging.
  Framework independence — swap LangGraph out, tools untouched.
  Multi-consumer reuse — a second internal agent becomes another
  client against the same contract, not a second copy of the
  credentials. The system is meant to serve several internal teams.

Transport: stdio by default (server spawned as a subprocess, one
less thing to run). Set MCP_TRANSPORT=http to run standalone.

TODO(build): implement with the `mcp` package. Register both tools,
expose their JSON schemas, handle both transports.
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    args = parser.parse_args()
    raise NotImplementedError("see module docstring")


if __name__ == "__main__":
    main()

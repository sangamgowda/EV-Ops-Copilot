import { useState } from "react";
import { describeGap } from "../describe";
import type { AssistantMessage } from "../types";
import { AnswerText } from "./AnswerText";
import { FeedbackBar } from "./FeedbackBar";
import { HowIGotThis } from "./HowIGotThis";
import { SourceCard } from "./SourceCard";

interface Props {
  message: AssistantMessage;
  onRetry(question: string): void;
}

export function AssistantBubble({ message, onRetry }: Props) {
  const [openId, setOpenId] = useState<string | null>(null);
  const { done, status } = message;
  const partial = Boolean(done?.partial);
  const knownIds = done ? new Set(done.evidence.map((e) => e.id)) : null;
  const openEvidence = done?.evidence.find((e) => e.id === openId) ?? null;
  const toggle = (id: string) => setOpenId((cur) => (cur === id ? null : id));

  return (
    <article
      className={`bubble bubble--assistant${partial ? " bubble--partial" : ""}${status === "error" ? " bubble--error" : ""}`}
      aria-busy={status === "working" || status === "streaming"}
    >
      {status === "working" && (
        <p className="progress" role="status" aria-live="polite">
          <span className="progress__dot" aria-hidden="true" />
          {message.progress?.message ?? "Starting"}…
        </p>
      )}

      {message.text && (
        <div className="answer">
          <AnswerText text={message.text} knownIds={knownIds} openId={openId} onCite={toggle} />
          {status === "streaming" && <span className="cursor" aria-hidden="true" />}
        </div>
      )}

      {openEvidence && <SourceCard evidence={openEvidence} onClose={() => setOpenId(null)} />}

      {done && partial && done.gaps.length > 0 && (
        <div className="unconfirmed">
          <p className="unconfirmed__title">What I couldn’t confirm</p>
          <ul>
            {[...new Set(done.gaps)].map((g) => (
              <li key={g}>{describeGap(g)}</li>
            ))}
          </ul>
        </div>
      )}

      {status === "error" && (
        <div className="error" role="alert">
          <p>{message.error}</p>
          <button type="button" onClick={() => onRetry(message.question)}>
            Try again
          </button>
        </div>
      )}

      {done && (
        <footer className="bubble__foot">
          <HowIGotThis done={done} onCite={toggle} />
          <FeedbackBar turnId={done.turn_id} />
        </footer>
      )}
    </article>
  );
}

import { useState } from "react";
import { sendFeedback } from "../api";
import type { Rating } from "../types";

/** One click to rate. A comment box appears only after thumbs-down, and
 *  sending it updates the same rating rather than adding a second one. */
export function FeedbackBar({ turnId }: { turnId: string }) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function rate(r: Rating) {
    setError(null);
    const previous = rating;
    setRating(r);
    setSent(false);
    try {
      await sendFeedback(turnId, r);
    } catch {
      setRating(previous);
      setError("Could not save that rating. Try again.");
    }
  }

  async function submitComment() {
    if (!comment.trim()) return;
    try {
      await sendFeedback(turnId, "down", comment.trim());
      setSent(true);
    } catch {
      setError("Could not send the comment. Try again.");
    }
  }

  return (
    <div className="feedback">
      <div className="feedback__buttons" role="group" aria-label="Rate this answer">
        <button type="button" aria-pressed={rating === "up"} aria-label="Helpful"
          className={`feedback__btn${rating === "up" ? " feedback__btn--on" : ""}`}
          onClick={() => rate("up")}>
          👍
        </button>
        <button type="button" aria-pressed={rating === "down"} aria-label="Not helpful"
          className={`feedback__btn${rating === "down" ? " feedback__btn--on" : ""}`}
          onClick={() => rate("down")}>
          👎
        </button>
        {rating === "up" && <span className="feedback__thanks">Thanks</span>}
      </div>
      {rating === "down" && !sent && (
        <div className="feedback__comment">
          <label htmlFor={`fb-${turnId}`}>What was wrong? (optional)</label>
          <textarea id={`fb-${turnId}`} rows={2} value={comment}
            onChange={(e) => setComment(e.target.value)} />
          <button type="button" onClick={submitComment} disabled={!comment.trim()}
            aria-label="Send comment">
            Send
          </button>
        </div>
      )}
      {sent && <span className="feedback__thanks">Thanks — noted</span>}
      {error && <span className="feedback__error" role="alert">{error}</span>}
    </div>
  );
}

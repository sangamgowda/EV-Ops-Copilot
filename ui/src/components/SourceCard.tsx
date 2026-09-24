import { evidenceKind, statusNote } from "../describe";
import type { Evidence } from "../types";

/** What a citation chip opens: where this piece of the answer came from.
 *  For a database fact, the SQL that actually ran; for a document, the
 *  passage, its source and how closely it matched the question. */
export function SourceCard({ evidence, onClose }: { evidence: Evidence; onClose(): void }) {
  const note = statusNote(evidence);
  return (
    <aside className="source" aria-label={`Source ${evidence.id}`}>
      <header className="source__head">
        <span className="source__id">{evidence.id}</span>
        <span className="source__kind">{evidenceKind(evidence)}</span>
        {note && <span className="source__note">{note}</span>}
        <button type="button" className="source__close" onClick={onClose} aria-label="Close source">
          ×
        </button>
      </header>
      <p className="source__summary">{evidence.summary}</p>
      {evidence.sql && (
        <>
          <p className="source__label">Query that ran</p>
          <pre className="source__code">{evidence.sql}</pre>
        </>
      )}
      {evidence.query && (
        <p className="source__meta">
          Searched for “{evidence.query}”
          {evidence.source_doc && <> · from {evidence.source_doc}</>}
          {evidence.score !== null && <> · match {evidence.score.toFixed(2)}</>}
        </p>
      )}
    </aside>
  );
}

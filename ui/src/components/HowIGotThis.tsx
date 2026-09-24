import { describeDecision, describeGap, describeTool, firstLine, statusNote } from "../describe";
import type { Done } from "../types";

/** The agent's laps, folded away by default. <details> gives keyboard
 *  and screen-reader support for free and needs no state. */
export function HowIGotThis({ done, onCite }: { done: Done; onCite(id: string): void }) {
  const byId = new Map(done.evidence.map((e) => [e.id, e]));
  const laps = done.laps.length;
  if (laps === 0) return null;
  return (
    <details className="how">
      <summary>
        How I got this · {laps} {laps === 1 ? "lap" : "laps"}
        {done.grounded !== null && (
          <span
            className={`how__check${done.grounded ? "" : " how__check--warn"}`}
            title={done.grounded ? "Every number and citation was checked against the evidence"
                                 : "Parts of this answer could not be checked against the evidence"}
          >
            {done.grounded ? "Checked" : "Not fully checked"}
          </span>
        )}
      </summary>
      <ol className="how__laps">
        {done.laps.map((lap) => (
          <li key={lap.lap} className="how__lap">
            <p className="how__title">Lap {lap.lap}</p>
            {lap.reasoning && (
              <p>
                <span className="how__label">Decided</span> {lap.reasoning}
              </p>
            )}
            {lap.tools.length > 0 && (
              <div>
                <span className="how__label">Looked up</span>
                <ul>
                  {lap.tools.map((t, i) => (
                    <li key={i}>{describeTool(t)}</li>
                  ))}
                </ul>
              </div>
            )}
            {lap.found.length > 0 && (
              <div>
                <span className="how__label">Found</span>
                <ul>
                  {lap.found.map((id) => {
                    const e = byId.get(id);
                    if (!e) return null;
                    const note = statusNote(e);
                    return (
                      <li key={id}>
                        <button type="button" className="chip" onClick={() => onCite(id)}>
                          {id}
                        </button>{" "}
                        {note ? <em>{note}</em> : firstLine(e.summary)}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
            <p>
              <span className="how__label">Concluded</span> {describeDecision(lap.decision)}
              {lap.decision === "continue" && lap.next_question && <> — next: {lap.next_question}</>}
            </p>
            {lap.missing.length > 0 && lap.decision !== "complete" && (
              <p className="how__missing">Missing: {lap.missing.map(describeGap).join(", ")}</p>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}

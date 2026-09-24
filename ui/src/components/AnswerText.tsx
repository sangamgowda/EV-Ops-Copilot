import { Fragment, type ReactNode } from "react";

interface Props {
  text: string;
  /** Evidence ids that exist; a tag for any other id renders as plain text. */
  knownIds: Set<string> | null;
  openId: string | null;
  onCite(id: string): void;
}

// The tag plus any punctuation straight after it, so the two can be kept
// on one line — otherwise ". Battery…" can wrap onto a new line alone.
const TAG = /\[(e\d+)\]([.,;:!?)]*)/g;

/** The answer as paragraphs and bullet lists, with each [eN] tag turned
 *  into a citation chip. Text is rendered as text — React escapes it —
 *  so nothing the model writes can inject markup. While streaming,
 *  `knownIds` is null and every well-formed tag becomes a chip; a tag
 *  still being written ("[e") simply shows as text until it completes. */
export function AnswerText({ text, knownIds, openId, onCite }: Props) {
  const blocks = text.split(/\n{2,}/).filter((b) => b.trim());
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim());
        const isList = lines.length > 0 && lines.every((l) => /^\s*[-*•]\s+/.test(l));
        if (isList) {
          return (
            <ul key={i}>
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*[-*•]\s+/, ""), knownIds, openId, onCite)}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i}>
            {lines.map((l, j) => (
              <Fragment key={j}>
                {j > 0 && <br />}
                {inline(l, knownIds, openId, onCite)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </>
  );
}

function inline(
  line: string,
  knownIds: Set<string> | null,
  openId: string | null,
  onCite: (id: string) => void,
): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of line.matchAll(TAG)) {
    const id = m[1];
    const at = m.index ?? 0;
    const before = line.slice(last, at);
    if (knownIds === null || knownIds.has(id)) {
      // Keep the word before the chip with it ("kg [e2]."), so a chip is
      // never stranded at the start of a line on a narrow screen.
      const split = before.search(/\S+\s*$/);
      const head = split > 0 ? before.slice(0, split) : split === 0 ? "" : before;
      const lastWord = split >= 0 ? before.slice(split) : "";
      if (head) out.push(head);
      out.push(
        <span key={`${id}-${at}`} className="chip-wrap">
          {lastWord}
          <button
            type="button"
            className={`chip${openId === id ? " chip--open" : ""}`}
            aria-expanded={openId === id}
            aria-label={`Source ${id}`}
            onClick={() => onCite(id)}
          >
            {id}
          </button>
          {m[2]}
        </span>,
      );
    } else {
      if (before) out.push(before);
      out.push(m[0]);
    }
    last = at + m[0].length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}

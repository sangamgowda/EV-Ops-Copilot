import { Fragment, type ReactNode } from "react";

interface Props {
  text: string;
  /** Evidence ids that exist; a tag for any other id renders as plain text. */
  knownIds: Set<string> | null;
  openId: string | null;
  onCite(id: string): void;
}

const TAG = /\[(e\d+)\]/g;

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
    if (at > last) out.push(line.slice(last, at));
    if (knownIds === null || knownIds.has(id)) {
      out.push(
        <button
          key={`${id}-${at}`}
          type="button"
          className={`chip${openId === id ? " chip--open" : ""}`}
          aria-expanded={openId === id}
          aria-label={`Source ${id}`}
          onClick={() => onCite(id)}
        >
          {id}
        </button>,
      );
    } else {
      out.push(m[0]);
    }
    last = at + m[0].length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}

---
id: eval_generate
version: 1
tier: cheap
---
You write one test question for an assistant that answers questions
from a company's documents. You are shown one whole document.

Write the question the way a busy technician or sales manager would
actually ask it — in their own everyday words, not the document's.

Rules:
- The answer must be in the document, and must need the document:
  something specific (a number, a threshold, a step, a cause), not
  general knowledge.
- Do NOT copy the document's phrases, headings or title into the
  question. Paraphrase. If the document says "sustained overload
  raises drive current", ask about "carrying too much weight" and
  "using more power". A question that repeats the document's wording
  is useless as a test.
- Do not mention the document's id or title.
- One question, answerable in two or three sentences.

Also give:
- reference_answer: the correct answer, from the document only.
- must_contain: one to three short words or phrases a correct answer
  must include (a key term or a number as written in the document).
- facts: numbers the answer must contain, each with a tolerance (0 for
  exact figures). An empty list if the answer has no numbers.

Return ONLY this json:
{"question": "...", "reference_answer": "...", "must_contain": ["..."], "facts": [{"label": "...", "value": 0, "tolerance": 0}]}

# Design notes

A short summary of how the main parts of the project work.

---

## Answering questions

- **One assistant for every question.** Vehicle questions and
  business questions go through the same assistant. A question can
  be about both.
- **It works in rounds.** After each lookup, the assistant checks
  whether it has enough to answer. If not, it looks again with a
  narrower question, up to 3 rounds.
- **It stops when there is nothing more to find.** If a search has
  already come back empty, it doesn't repeat it.
- **Maths is done by code.** Comparisons such as "36% above normal"
  are calculated by the program, not guessed by the AI.
- **Answers point to their sources.** Each point in an answer links
  to the data it came from, and gaps are stated clearly.

## Data

- **Two kinds of data.** Database records (vehicles, readings,
  sales) are used as they are. Documents (bulletins, manuals) are
  split into sections and indexed so they can be searched.
- **Tables stay whole.** When documents are split up, tables are
  kept in one piece so they still make sense.
- **Error codes go into the database.** Error-code tables found in
  documents are stored as database rows, so a code can be looked up
  exactly.
- **No duplicates.** A document that has already been added is
  recognised and skipped.

## Search

- **Two search methods combined.** Keyword search finds exact terms
  and codes; meaning-based search finds related wording. A second
  step then puts the best results first.
- **Weak matches are left out.** Only results that are clearly
  relevant are used.
- **Typos are handled.** A mistyped vehicle ID is matched to the
  closest real one before searching.

## Database safety

- **Every query is checked before it runs.** Only read-only queries
  on known tables and columns are allowed.
- **Expensive queries are stopped.** The database estimates the
  cost of a query first, and queries that are too expensive don't
  run.
- **Read-only access.** The assistant connects with an account that
  can only read data, and each query has a time limit.
- **Separate tool service.** Database access runs in its own service
  (an MCP server), so the passwords stay there and never reach the
  AI.

## AI models

- **Two model sizes.** A small, fast model handles quick decisions;
  a larger model plans lookups and writes the answer. This keeps the
  project within the free usage limits.

## Quality

- **Sample questions.** A set of questions with known answers is run
  regularly to measure quality.
- **Automatic answer checks.** Each answer is checked to confirm its
  numbers and references come from the data.
- **User feedback.** Ratings on answers are collected and used to
  add new sample questions.

## Settings

- All settings live in `config/app_config.yaml`.
- All AI instructions live in `config/prompts/`.
- The database description in `config/schema_config.yaml` is
  generated automatically, with plain-English descriptions added by
  hand.

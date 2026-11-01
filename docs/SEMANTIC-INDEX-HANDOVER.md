# Semantic index handover — storage and scale

Status: **steps 1 and 2 implemented, not yet deployed.** Written 2026-11-01 against `30ed766`;
implemented 2026-11-01. Step 3 remains the option if the corpus outgrows the raised cap.

What changed: `ItemVector.vector` is packed float32 instead of JSON text, migration v3 repacks
the existing rows in place, and `MAX_COMPARE` is 20,000. Measured on a dev machine at 5,000
vectors of 768 dimensions: a vector is 3,072 bytes instead of 15,591, parsing the set costs
14 ms instead of 1,774 ms, and a full search drops from ~2.3 s to ~0.57 s. The absolute
numbers are slower than the appliance figures below; the ratios are what carry over.

## What exists today

Search by meaning is live on production. The pieces:

| Piece | Where |
|---|---|
| Vector table | `backend/bt/hub/models.py` — `ItemVector(item_id, model, dim, vector, created_at)` |
| Embedding, indexing, search | `backend/bt/hub/semantic.py` |
| Scheduler hook | `backend/bt/hub/engine.py` — `dispatch_due()` calls `semantic.sweep()` |
| API | `backend/bt/api/hub.py` — `GET /hub/search/semantic`, `GET /hub/search/semantic/status` |
| UI | `frontend/src/routes/find/+page.svelte` — "Search by meaning" |
| Settings | `frontend/src/lib/settings/AiTab.svelte` — Embeddings server/model/key + "Test embeddings" |
| Tests | `backend/tests/test_semantic.py` |

There is **no external vector store**. Vectors are a table in the same `bt.sqlite3`, so they
inherit the existing backup, restore and rollback story. That is deliberate and worth keeping.

### How it works

- `sweep()` runs at the top of every scheduler tick, throttled to 120 s, and embeds the
  **32 newest** items that have no vector for the current model (`BATCH = 32`).
- `search()` loads up to `MAX_COMPARE = 20000` vectors for the current model, computes cosine
  similarity in pure Python, filters to what the profile may see, sorts, returns the top N.
- Vectors are keyed by `(item_id, model)`, so changing the model re-embeds rather than
  comparing vectors from different spaces.
- The vector is stored as **packed float32** in a `BLOB` column, with `dim` as the length check.
  A row that will not unpack — JSON-era text, a truncated write — is skipped, not raised.

## The problem (as measured before the change)

Measured on production at 151 items, 768 dimensions, model `embeddinggemma`:

| | 151 vectors (today) | 5,000 (the cap) |
|---|---|---|
| SQLite read | ~1 ms | ~30 ms |
| **JSON parse** | 33 ms | **1.1 s** |
| Python cosine | 3.9 ms | 129 ms |
| **Total per search** | **~37 ms** | **~1.2 s** |

Two facts follow:

1. **The store is not the bottleneck.** Reading vectors from SQLite is about a millisecond.
   Parsing the JSON costs 8× the actual similarity maths. A vector store would replace the
   129 ms cosine and leave the 1.1 s parse untouched.
2. **The cap is the real deadline.** Past 5,000 embedded items, `MAX_COMPARE` silently drops
   the oldest items out of semantic results — no error, just quietly worse answers.

Growth: 151 content items in 1.7 days ≈ **87 per day**, which reaches 5,000 in about two
months. That rate is the initial curation burst and will settle lower, so treat two months as
pessimistic but directionally right.

## The plan

### Step 1 — store vectors as float32 BLOB (do this first) — **done**

Replace the JSON text with packed float32 bytes.

- 768 floats = **3,072 bytes** instead of ~17,000. A 5.5× storage win.
- `array.array('f').frombytes(blob)` parses in microseconds instead of 0.22 ms per vector.
- At 5,000 items a search drops from ~1.2 s to **~155 ms** (25 ms parse + 129 ms cosine).
- No new dependency, no new service, backup story unchanged.

Implementation notes:

- `ItemVector.vector` becomes `LargeBinary` (or keep `TEXT` and store base64 — worse; use a
  BLOB). `dim` already exists and becomes load-bearing: it is the length check.
- `semantic.embed()` returns `list[list[float]]`; pack with
  `array.array('f', vec).tobytes()` before storing.
- `search()` unpacks with `array.array('f'); a.frombytes(blob)` and compares `len(a) == len(query)`.
- **Migration:** the table is additive and already deployed, so this is a data conversion, not
  a schema migration. Two options, pick one:
  - *Convert in place*: read each row, `json.loads`, repack, write back. 151 rows today,
    trivial. Guard with a `try/except` so a malformed row is dropped rather than crashing.
  - *Re-embed*: bump the model key (e.g. store `embeddinggemma@f32`) so old rows are ignored
    and the sweep refills. Simpler and self-healing, at the cost of re-embedding everything.
  The in-place conversion is better here because it keeps the index warm; the re-embed path is
  the fallback if the conversion is ever ambiguous.
- Keep `semantic.base_url()` and the per-model keying exactly as they are.

What was actually done:

- The in-place conversion, as `migrate._v3` (schema version 3). It pages on
  `typeof(vector) != 'blob'` rather than loading the table, because at 20,000 items the JSON
  text is ~340 MB. Each pass converts or deletes every row it selects, so the predicate empties
  and the loop ends; a row that will not parse is deleted so the sweep re-embeds it.
- No `ALTER TABLE` is needed. SQLite keeps the column's `TEXT` affinity on an existing
  database, but a BLOB is stored as a BLOB regardless, and the old rows read back as `str`.
  A fresh database gets a real `BLOB` column from `create_all`.
- The ORM's `LargeBinary` binding refuses a `str`, so the conversion works on the driver
  connection. That is also why the test that plants a JSON-era row uses raw SQL.

### Step 2 — raise the cap — **done**

Once parsing is cheap, `MAX_COMPARE` can go to **20,000**. At that size a search is ~0.6 s
(100 ms parse + 516 ms cosine), which is acceptable for a deliberate button rather than a
per-keystroke search. That buys roughly seven months at the current rate.

Do not raise it before step 1: at 20,000 items the JSON parse alone would be ~4.4 s.

### Step 3 — only if it is still not enough

If the corpus passes ~50,000 items, or if silently dropping old items becomes unacceptable,
add **`sqlite-vec`**: a loadable SQLite extension, still one file, still the same backup and
rollback. It gives ANN search and removes the `MAX_COMPARE` cliff.

Do **not** move to pgvector, Qdrant, Milvus or a hosted vector service. That means a second
datastore to back up, restore and roll back, for a household appliance whose main virtue is
one SQLite file. The measured numbers do not justify it.

## Acceptance criteria

- A search over the converted index returns the same ranking as before the change for a fixed
  query and a fixed set of vectors (assert on ids, not scores).
- `semantic.status()` still reports `indexed` and `items` correctly.
- A vector whose length does not match the query is skipped, not compared.
- A malformed or truncated BLOB is skipped, not raised.
- Switching the embeddings model still re-embeds instead of mixing spaces.
- `tests/test_semantic.py` passes, plus a new test that a JSON-era row and a BLOB-era row
  cannot be compared against each other.

All six are covered in `tests/test_semantic.py`: `test_converting_a_json_index_preserves_the_ranking`,
`test_the_float32_index_ranks_the_same_as_the_json_one`,
`test_a_vector_of_the_wrong_length_is_skipped_not_compared`,
`test_a_truncated_or_malformed_vector_is_skipped_not_raised`,
`test_a_json_era_row_is_not_compared_with_a_float32_row`, and
`test_v2_vectors_are_repacked_as_float32`.

## Rollback

The change is contained to `semantic.py`, `models.py` and one column's encoding. Rollback is
the previous image (`watchcraft:rollback-*` for the release that introduced it) plus, because
the column was converted in place, restoring the pre-deploy SQLite backup — the same procedure
as any other rollout, recorded in `docs/SOURCE_SCOUT_HANDOVER.md`.

The re-embed alternative was not taken, so rollback does need that data restore. The old image
reads the column as JSON text and would fail on the repacked rows.

## Known limits to keep in mind

- **The cap is silent.** Past `MAX_COMPARE`, older items simply stop appearing. Raising it to
  20,000 moves the cliff, it does not remove it. The status endpoint still reports only
  `indexed` and `items`, not how many are actually compared; that is tracked as T-063.
- **No ANN.** Every query is a linear scan of the compared set. That is fine at these sizes and
  is the reason the cap exists.
- **Embeddings are optional.** With no embeddings model configured, `available()` is false, the
  endpoint says why, and keyword search is the only path. Nothing here changes that.
- **A separate embeddings server never receives the judge's key.** Keep that property if the
  storage code is refactored.

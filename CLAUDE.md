# Yantra

AI copilot that guides factory operators through step-by-step fault resolution from OEM manuals. See README.md for the full description and graph schema.

## Core principle

The fault graph is built **once**, at ingestion, by an LLM. At runtime the system is **deterministic graph traversal**: no generated text, for safety and traceability. Do not turn this into a chatbot over PDFs.

- The LLM (`llm.generate*`) may only be called from `app/ingest.py`. Providers: Gemini (default, `GEMINI_API_KEY`) or Hugging Face (`HF_TOKEN`), chosen in `config.py`.
- Runtime (`app/diagnose.py`) uses only the local embedding model, to match the operator's words to symptoms.
- Every question, option and step shown to an operator must be a row in the graph.

## Scope is locked

Text only. No voice, no multilingual input, no offline mode, no auth, no user accounts. Do not add features that are not already built. **Ask the user before any decision that changes scope.**

Keep code minimal and direct. Prefer plain if/else and fixed thresholds in `config.py` over configurable or clever abstractions.

## Commands

Run from the project root, using the venv (`.venv\Scripts\activate`).

```bash
python -m app.ingest data/manuals/hp200_manual.pdf   # load the sample manual (no token needed)
uvicorn app.main:app                                  # app at http://localhost:8000
python evaluate.py                                    # 20 test cases -> table + results.json
python data/build_sample.py                           # regenerate sample PDF + extraction cache
```

There is no unit test suite; `evaluate.py` is the test harness. After changing diagnosis, ingestion or the graph, run it and report the table honestly, including failures.

## Layout

`config.py` (models, paths, thresholds) · `app/main.py` (routes, serves `static/`) · `app/db.py` (schema) · `app/llm.py` (all Hugging Face access) · `app/ingest.py` · `app/diagnose.py` · `app/escalate.py` · `app/review.py` · `static/` (plain HTML/JS/CSS) · `data/` (manuals, extraction cache, `yantra.db`).

## Things that will bite you

- **Extraction cache** is keyed by the PDF's sha256 in `data/extractions/`. The sample's cache is hand-authored. Changing `data/build_sample.py` and rebuilding changes the PDF bytes only if content changes; rebuild writes the matching cache.
- **Re-ingesting** a manual with the same name deletes and recreates it, so its `manual_id` and all fault/cause ids change. Runtime tables have no foreign keys on purpose, but old sessions then point at ids that no longer exist.
- **Approved promotions change the live graph** in `data/yantra.db`. Delete the db and re-ingest to get back to the clean sample.
- **Never write API keys or tokens into files**; they come from environment variables only.
- **Ingestion is resumable**: `extract()` saves `<sha>.partial.json` after each LLM call and deletes it when done. Gemini's free tier is about 20 requests/day per model, and `llm._gemini` fails fast on a daily quota instead of sleeping.
- **Extracted graphs are drafts.** On the KUKA LED-table manual, extraction missed about a third of the rows, and similarity matching then sent those problems to a near-identical wrong fault with high confidence. `faults.source_pages` lets a reviewer check each fault; pages are located from the PDF text in `ingest.locate_pages`, not trusted from the LLM. A deterministic guard for identifier tokens (e.g. `2.5V`) not present in the matched fault is a known open idea; ask before adding it.
- **Chunk size** (`CHUNK_CHARS = 900`) is deliberately small: MiniLM only reads about 1000 characters, and larger chunks silently hurt retrieval.
- **`db.connect()`** returns a connection that closes on leaving `with`. Keep that: on Windows an open handle stops temp files being deleted. `evaluate.py` swaps `db.DB_PATH` to a scratch file, so always read the path through `db.DB_PATH` at call time.
- **`MATCH_MIN`** (0.50) was set from a small probe set. Do not tune it to make `tests.json` pass; that is overfitting.
- The preview server config lives in the parent folder's `.claude/launch.json` (it uses `--app-dir yantra`).

## Style

Match the surrounding code: short docstrings that say why, no speculative options. UI copy is plain sentence case; the UI must stay usable on a phone and in dark mode.

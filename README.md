# Yantra

A copilot that walks a factory operator through fixing a machine fault, step by step, using the OEM manual.

The manual is read **once**, at ingestion, by an LLM that turns it into a fault graph. After that, everything an operator sees (questions, options, steps) is a row from that graph. There is no generated text at runtime, so every answer can be traced to the manual.

## Run it

You need Python 3.10 or newer.

```bash
# 1. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate

# 2. Install dependencies (the first run also downloads a ~90 MB embedding model)
pip install -r requirements.txt

# 3. Load the sample manual into the graph (no token needed)
python -m app.ingest data/manuals/hp200_manual.pdf

# 4. Start the app, then open http://localhost:8000
uvicorn app.main:app

# 5. Run the evaluation
python evaluate.py
```

Pages: **Diagnose** (operators), **Manuals** (upload a PDF or pick a pre-loaded one, see what was found), **Supervisor** (`/supervisor.html`, approve or reject fixes).

Ingesting a **new** PDF calls an LLM, so set a key first. The default is Gemini: `$env:GEMINI_API_KEY = "..."` in PowerShell (`export GEMINI_API_KEY=...` elsewhere). For Hugging Face instead, set `LLM_PROVIDER = "huggingface"`, `LLM_MODEL` and `HF_TOKEN`. The sample manual needs no key, because its extraction is saved in `data/extractions/`.

Extraction is resumable: progress is saved after every LLM call (`data/extractions/<hash>.partial.json`), so if a rate limit stops it, run the same command again later. The Gemini free tier allows only about 20 requests a day per model, and a manual needs one request per fault plus one. When a model's daily quota runs out, `llm.py` moves on to the models in `LLM_FALLBACKS`, and if all are used up it stops, so run the command again the next day.

## How it works

1. **Ingestion** (`app/ingest.py`). Read the PDF, split it into chunks, embed them, and use retrieval to pick the chunks about each fault. The LLM lists the faults, then extracts each one as JSON. Each fault is checked against the PDF: its manual pages are located from the text itself (the LLM's cited pages are only a hint), and a fault whose causes and steps cannot be found in the manual is flagged. The result is validated (malformed items are dropped and reported) and written to SQLite. It is cached by PDF hash, so re-ingesting the same file is free and identical.
2. **Diagnosis** (`app/diagnose.py`). The operator's text is matched to the closest fault with the local embedding model. Multiple-choice questions then narrow the causes one at a time, and each answer prunes the rest. When one cause is left, its fix steps are shown one at a time with a Done button.
3. **Escalation** (`app/escalate.py`). If there is no confident match, the fix needs a technician, no question can separate the remaining causes, or every fix failed, the case is escalated with a short report: machine, symptom, questions and answers, steps tried. Operators can also escalate at any point.
4. **Promotion** (`app/review.py`). When an escalated case is later fixed, the operator writes down what fixed it. A supervisor reviews it. Approving adds the operator's wording as a new symptom of the matched fault, or creates a new fault with the written steps if nothing matched.

The fixed rules (all in `config.py`):

| Setting | Value | Meaning |
|---|---|---|
| `MATCH_MIN` | 0.50 | Below this similarity, escalate instead of guessing |
| `MARGIN` | 0.05 | If the top faults are this close, ask the operator which one |
| `MAX_QUESTIONS` | 6 | More questions than this, escalate |

The next question is the one whose worst-case answer leaves the fewest causes; ties go to the lowest id. Models are swapped on one line each: `LLM_MODEL` and `EMBED_MODEL`.

## Graph schema

```
manuals ─┬─ faults ─┬─ symptoms        phrases operators might use (+ embedding)
         │          ├─ questions ── options
         │          └─ causes ──┬─ cause_option   "this cause fits this answer"
         │                      └─ fix_steps      ordered, with warning flag
```

| Table | Key columns |
|---|---|
| `manuals` | name, machine, source_file, sha |
| `faults` | manual_id, title, source_pages |
| `symptoms` | fault_id, text, embedding |
| `causes` | fault_id, description, needs_technician, reason |
| `questions`, `options` | fault_id, text; question_id, label |
| `cause_option` | cause_id, option_id |
| `fix_steps` | cause_id, seq, text, is_warning |

**Pruning rule:** an answer keeps a cause if the cause lists that option, or lists none for that question. A cause marked `needs_technician` is never walked through; it escalates.

Runtime tables (no foreign keys, so re-ingesting a manual never breaks history): `sessions`, `session_log` (every question, answer and step, with timestamps), `review_queue`, and `resolution_times`, which records how long each case took. The view `fault_time_stats` gives average, minimum and maximum time per fault for resolved cases; it is served at `GET /api/stats/faults`.

## Files

```
config.py            models, paths, thresholds
evaluate.py          runs tests.json, prints a table, writes results.json
tests.json           20 test cases
app/
  main.py            FastAPI routes + serves static/
  db.py              SQLite schema and helpers
  llm.py             the only code that touches Hugging Face models
  ingest.py          PDF -> fault graph
  diagnose.py        deterministic session state machine + resolution timer
  escalate.py        technician report (fixed template)
  review.py          promotion queue
static/              operator page, supervisor page, one stylesheet
data/
  build_sample.py    regenerates the sample PDF and its extraction cache
  manuals/           PDFs (sample: hp200_manual.pdf)
  extractions/       cached LLM output, keyed by PDF hash
  yantra.db          generated
```

## Evaluation

`python evaluate.py` runs the 20 cases in `tests.json` through the real diagnosis code with a simulated operator who answers as the expected cause would. It uses a scratch database built from the sample manual, so your live data is untouched. The cases are 15 fixes, 3 that should escalate to a technician, and 2 that should match nothing.

Latest run:

| | |
|---|---|
| Faults / causes / fix paths in graph | 6 / 20 / 20 |
| Test cases passed | 18 / 20 |
| Average cross-questions to a fix | 1.93 |
| Average time to resolution | 0.05 s |

The time is system time, since the simulated operator answers instantly. In the live app the same table records real operator time.

The two failures are real limits of matching by embeddings, kept visible on purpose:

- **#16** "ram slowly drifts down under load" matched the generic *Ram* faults, not *Pressing force too low*, because that fault has no symptom phrase about drifting. It escalated, which is the safe direction. Approving a supervisor review would add that phrasing.
- **#19** "the conveyor belt feeding the press is jammed" scored 0.57 against *Pressing force too low* because of the word "press", and walked into a wrong fix path. This is the unsafe direction. Raising `MATCH_MIN` would stop it but also reject some valid phrasings, and 20 cases is too few to tune it fairly.

## Known limits

- The sample manual is fictional, and its saved extraction was written by hand to match the PDF, not produced by the LLM.
- **Real extraction has been run on the 114-page KUKA Sunrise Cabinet Med manual** (Gemini). Its troubleshooting chapter is LED status tables, a hard shape for a fault, cause and remedy graph. After improving the prompts and checking pages, the result was:
  - 15 faults, each named by component and state ("STAS2 Safety node B LED Off") and tied to its manual page. Spot checks against pages 97 to 104 matched the manual's remedies.
  - **About 8 of roughly 23 diagnosable rows are missing** (PWR/3.3V, PWR/2.5V, PWR/1.2V, the 27 V LED, PS1, PS2 and the two PMB_SR rows). The model left them out of its fault list.
  - **Missing rows are matched confidently to the wrong fault.** "PWR/2.5V LED is off" matched "PWR/5V LED Off" at 0.95 similarity and would show that LED's steps. Similarity matching cannot tell near-identical identifiers apart.
  - One invented fault ("27 V LED flashing", a state the manual does not have) was dropped by validation, as designed.
  - Treat an extracted graph as a **draft that a person must check against the manual** before operators use it. The saved page numbers make that check quick.
- Gemini's free tier allows about 20 requests a day per model, and the key's three configured models were used up in one day.
- Matching thresholds were set from a small hand-made probe set.
- Text only; no accounts, voice, other languages or offline mode.

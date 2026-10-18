"""Ingestion: PDF -> chunks -> RAG + LLM extraction -> validated fault graph in SQLite.

The LLM runs here and nowhere else. Extraction output is cached by PDF hash, so re-ingesting the
same file is free and gives an identical graph.

Usage:  python -m app.ingest data/manuals/hp200_manual.pdf
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
from pypdf import PdfReader

from app import db, llm
from config import CHUNK_CHARS, EXTRACTIONS_DIR, FAULT_DETAIL_CHUNKS, FAULT_LIST_CHUNKS, FAULT_NEIGHBOURS

SYSTEM = "You extract structured data from equipment manuals. Reply with one JSON object and nothing else."

LIST_PROMPT = """Manual excerpts (each page starts with a [page N] marker):
{text}

List every distinct problem that the troubleshooting part of these excerpts lets an operator diagnose, and give the machine name.
Rules:
- One entry per problem. Name the component or indicator and its state, for example "PWR/3.3V LED off". Never just "Off".
- Do not merge different components or different states into one entry.
- Skip normal or OK states, and anything that has no remedy or action (a remedy of "-").
- Skip safety rules, descriptions and specifications that are not troubleshooting.
Reply as JSON: {{"machine": "<machine name>", "faults": ["<problem>", "..."]}}"""

FAULT_PROMPT = """Manual excerpts (each page starts with a [page N] marker):
{text}

Extract what the excerpts say about this one problem: "{title}"
Reply as JSON with exactly this shape:
{{
  "title": "<problem title>",
  "pages": [<page numbers the answer comes from>],
  "symptoms": ["<short phrase an operator might say>", "..."],
  "questions": [{{"text": "<multiple-choice question>", "options": ["<option>", "<option>"]}}],
  "causes": [{{
    "description": "<cause>",
    "needs_technician": false,
    "reason": "<why a technician is needed, or empty>",
    "answers": [{{"q": 0, "opts": [0]}}],
    "steps": [{{"text": "<one remedy step>", "is_warning": false}}]
  }}]
}}
Rules:
- Use only text about this exact problem (this component and this state). Do not use text about other problems or from other chapters.
- If the excerpts give no remedy for this exact problem, return an empty "causes" list.
- Do not invent causes, checks or steps. Keep the manual's own wording for steps.
- "steps" are actions the operator carries out, in order. Do not include general safety rules or background notes.
- "symptoms": 3 to 5 short phrases an operator might use to describe the problem.
- "questions": questions an operator can answer by looking or listening, taken from each cause's check.
  2 or 3 options each. Together they must tell the causes apart. If there is only one cause, use an empty list.
- "answers": for each cause, the options of each relevant question that fit that cause
  (q = question index, opts = option indexes, both 0-based).
- "needs_technician": true only if the manual says a qualified technician is required or the repair is unsafe for an operator.
- Set is_warning true for warnings."""


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_pdf(path):
    """Text of each page, in order (page n is element n-1)."""
    return [page.extract_text() or "" for page in PdfReader(str(path)).pages]


def chunk(pages):
    """Split into chunks of about CHUNK_CHARS on line boundaries. Each chunk remembers its pages,
    and every page start inside it is marked "[page N]" so the LLM can cite pages."""
    chunks, cur, cur_pages = [], "", []
    for n, text in enumerate(pages, 1):
        for line in [f"[page {n}]"] + text.splitlines():
            if cur and len(cur) + len(line) > CHUNK_CHARS:
                chunks.append({"text": cur, "pages": cur_pages})
                cur, cur_pages = "", []
            cur += line + "\n"
            if n not in cur_pages:
                cur_pages.append(n)
    if cur.strip():
        chunks.append({"text": cur, "pages": cur_pages})
    return chunks


def top_chunks(chunks, vecs, query, k, after=0):
    """RAG retrieval: the k chunks closest to the query, each followed by `after` more chunks.
    Returns (text in document order, pages covered)."""
    scores = vecs @ llm.embed([query])[0]
    keep = sorted({j for i in np.argsort(-scores)[:k] for j in range(i, min(i + after + 1, len(chunks)))})
    return "\n".join(chunks[i]["text"] for i in keep), sorted({p for i in keep for p in chunks[i]["pages"]})


def extract(pages, partial):
    """Two LLM passes: list the faults, then extract each one from its most relevant chunks.
    Progress is saved to `partial` after every call, so a rate limit or crash resumes instead of starting over."""
    chunks = chunk(pages)
    vecs = llm.embed([c["text"] for c in chunks])
    state = json.loads(partial.read_text(encoding="utf-8")) if partial.exists() else {}
    save = lambda: partial.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    if "titles" not in state:
        found, _ = top_chunks(chunks, vecs, "troubleshooting fault symptoms causes remedy", FAULT_LIST_CHUNKS)
        listing = llm.generate_json(SYSTEM, LIST_PROMPT.format(text=chunks[0]["text"] + "\n---\n" + found))
        state.update(machine=str(listing.get("machine", "Unknown machine")), titles=listing.get("faults", []), faults=[])
        save()
    for i, title in enumerate(state["titles"], 1):
        if i <= len(state["faults"]):
            continue  # done in an earlier run
        print(f"extracting fault {i}/{len(state['titles'])}: {title}", file=sys.stderr, flush=True)
        excerpts, seen = top_chunks(chunks, vecs, str(title), FAULT_DETAIL_CHUNKS, FAULT_NEIGHBOURS)
        fault = llm.generate_json(SYSTEM, FAULT_PROMPT.format(text=excerpts, title=title))
        fault["pages"] = [p for p in fault.get("pages", []) if p in seen] or seen  # cited pages must be ones it was shown
        state["faults"].append(fault)
        save()
    return {"machine": state["machine"], "faults": state["faults"]}


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def locate_pages(fault, claimed, page_tokens):
    """Find the manual pages a fault's text really appears on. The LLM's cited pages are only a hint:
    each page is scored by how many of the fault's causes and steps it contains.
    Returns (pages, coverage) where coverage is the share of causes and steps found on those pages."""
    items = [_tokens(t) for t in [c["description"] for c in fault["causes"]] + [s["text"] for c in fault["causes"] for s in c["steps"]]]
    items = [t for t in items if len(t) >= 2]
    if not items:
        return [], 0.0
    found = lambda p: [len(t & page_tokens[p]) / len(t) >= 0.8 for t in items]
    pool = [p for p in claimed if p in page_tokens] or list(page_tokens)
    scored = sorted(((sum(found(p)) / len(items), p) for p in pool), reverse=True)
    best = scored[0][0]
    if best == 0:
        return [], 0.0
    pages = sorted(p for score, p in scored if score >= max(0.25, best / 2))[:4]
    covered = [any(hits) for hits in zip(*(found(p) for p in pages))]
    return pages, sum(covered) / len(items)


def fmt_pages(pages):
    """[97, 98, 99, 103] -> "97-99, 103" """
    out, run = [], []
    for p in pages:
        if run and p != run[-1] + 1:
            out.append(run)
            run = []
        run.append(p)
    out += [run] if run else []
    return ", ".join(str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in out)


def validate(f):
    """Return (clean fault or None, warnings). Drops anything malformed rather than guessing."""
    warns = []
    title = str(f.get("title", "")).strip()
    if not title:
        return None, ["a fault without a title was skipped"]

    symptoms = [s.strip() for s in f.get("symptoms", []) if isinstance(s, str) and s.strip()] or [title]

    questions, qmap = [], {}  # qmap: index in LLM output -> index in cleaned list
    for i, q in enumerate(f.get("questions", [])):
        opts = q.get("options", []) if isinstance(q, dict) else []
        text = str(q.get("text", "")).strip() if isinstance(q, dict) else ""
        if text and len(opts) >= 2 and all(isinstance(o, str) and o.strip() for o in opts):
            qmap[i] = len(questions)
            questions.append({"text": text, "options": [o.strip() for o in opts]})
        else:
            warns.append(f"{title}: question {i} was malformed and skipped")

    causes = []
    for c in f.get("causes", []):
        desc = str(c.get("description", "")).strip()
        steps = [
            {"text": str(s.get("text", "")).strip(), "is_warning": bool(s.get("is_warning"))}
            for s in c.get("steps", [])
            if isinstance(s, dict) and str(s.get("text", "")).strip()
        ]
        tech = bool(c.get("needs_technician"))
        if not desc or (not steps and not tech):
            warns.append(f"{title}: a cause with no description or no steps was skipped")
            continue
        answers = []
        for a in c.get("answers", []):
            q = qmap.get(a.get("q")) if isinstance(a, dict) else None
            if q is None:
                continue
            n = len(questions[q]["options"])
            opts = sorted({o for o in a.get("opts", []) if isinstance(o, int) and 0 <= o < n})
            if opts:
                answers.append({"q": q, "opts": opts})
        causes.append({"description": desc, "needs_technician": tech, "reason": str(c.get("reason", "")).strip(),
                       "answers": answers, "steps": steps})

    if not causes:
        return None, warns + [f"{title}: no usable causes, fault skipped"]

    sigs = [tuple((a["q"], tuple(a["opts"])) for a in sorted(c["answers"], key=lambda a: a["q"])) for c in causes]
    if len(set(sigs)) < len(sigs):
        warns.append(f"{title}: some causes have identical answers, so questions cannot tell them apart")
    return {"title": title, "symptoms": symptoms, "questions": questions, "causes": causes}, warns


def store(conn, name, machine, source_file, sha, faults):
    """Write the graph. Re-ingesting a manual with the same name replaces it."""
    conn.execute("DELETE FROM manuals WHERE name = ?", (name,))
    manual_id = conn.execute(
        "INSERT INTO manuals (name, machine, source_file, sha) VALUES (?, ?, ?, ?)", (name, machine, source_file, sha)
    ).lastrowid

    # one embedding call for every symptom phrase (the fault title counts as a phrase too)
    phrases = [[f["title"]] + [s for s in f["symptoms"] if s != f["title"]] for f in faults]
    vecs = llm.embed([p for group in phrases for p in group])
    v = 0
    for f, group in zip(faults, phrases):
        fault_id = conn.execute("INSERT INTO faults (manual_id, title, source_pages) VALUES (?, ?, ?)",
                                (manual_id, f["title"], fmt_pages(f.get("pages", [])))).lastrowid
        for text in group:
            conn.execute("INSERT INTO symptoms (fault_id, text, embedding) VALUES (?, ?, ?)",
                         (fault_id, text, db.vec_to_blob(vecs[v])))
            v += 1
        option_ids = []  # option_ids[q][o] -> options.id
        for q in f["questions"]:
            qid = conn.execute("INSERT INTO questions (fault_id, text) VALUES (?, ?)", (fault_id, q["text"])).lastrowid
            option_ids.append([conn.execute("INSERT INTO options (question_id, label) VALUES (?, ?)", (qid, label)).lastrowid
                               for label in q["options"]])
        for c in f["causes"]:
            cid = conn.execute(
                "INSERT INTO causes (fault_id, description, needs_technician, reason) VALUES (?, ?, ?, ?)",
                (fault_id, c["description"], int(c["needs_technician"]), c["reason"]),
            ).lastrowid
            for a in c["answers"]:
                for o in a["opts"]:
                    conn.execute("INSERT INTO cause_option (cause_id, option_id) VALUES (?, ?)", (cid, option_ids[a["q"]][o]))
            for seq, s in enumerate(c["steps"], 1):
                conn.execute("INSERT INTO fix_steps (cause_id, seq, text, is_warning) VALUES (?, ?, ?, ?)",
                             (cid, seq, s["text"], int(s["is_warning"])))
    return manual_id


def ingest(pdf_path):
    pdf_path = Path(pdf_path)
    sha = file_sha(pdf_path)
    cache = EXTRACTIONS_DIR / f"{sha[:16]}.json"
    from_cache = cache.exists()
    pdf_pages = read_pdf(pdf_path)
    page_tokens = {n: _tokens(t) for n, t in enumerate(pdf_pages, 1)}
    if from_cache:
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        partial = EXTRACTIONS_DIR / f"{sha[:16]}.partial.json"
        data = extract(pdf_pages, partial)
        cache.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        partial.unlink()

    faults, warnings = [], []
    for raw in data.get("faults", []):
        clean, w = validate(raw)
        warnings += w
        if clean:
            claimed = [p for p in raw.get("pages", []) if isinstance(p, int)]
            clean["pages"], coverage = locate_pages(clean, claimed, page_tokens)
            if coverage < 0.5:
                warnings.append(f"{clean['title']}: only {coverage:.0%} of its causes and steps could be found in the manual. "
                                "Check it against the source before use.")
            faults.append(clean)
    if not faults:
        raise ValueError("No faults could be extracted from this manual.")

    db.init_db()
    with db.connect() as conn:
        manual_id = store(conn, pdf_path.stem, data.get("machine", pdf_path.stem), pdf_path.name, sha, faults)
        counts = db.manual_counts(conn, manual_id)
    return {"manual_id": manual_id, "name": pdf_path.stem, "machine": data.get("machine", pdf_path.stem),
            "from_cache": from_cache, "warnings": warnings, **counts}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.ingest <manual.pdf>")
    print(json.dumps(ingest(sys.argv[1]), indent=2))

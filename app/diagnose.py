"""Runtime diagnosis. Deterministic: graph traversal only, no generated text.

The only model used here is the local embedding model, to match the operator's words to symptoms.
Every question, option and step shown to the operator is a row from the fault graph.

States:  choose_fault -> questioning -> fixing -> resolved
         any state -> escalated
"""
import json
from datetime import datetime, timezone

from app import db, llm
from config import MARGIN, MATCH_MIN, MAX_QUESTIONS

REASONS = {
    "no_match": "No confident match to a known fault.",
    "no_cause": "The answers given do not match any known cause.",
    "ambiguous": "Several causes remain and no further question can tell them apart.",
    "too_many_questions": "Too many questions were asked without isolating a cause.",
    "technician": "The fix for this cause needs a qualified technician.",
    "fix_failed": "Every known fix for this fault was tried without success.",
    "operator": "The operator asked to escalate.",
}
NONE_OF_THESE = 0  # option id for "none of these" in the fault choice


# ---------- small helpers ----------

def _now():
    return datetime.now(timezone.utc)


def _log(conn, sid, kind, payload):
    conn.execute("INSERT INTO session_log (session_id, kind, payload, ts) VALUES (?, ?, ?, ?)",
                 (sid, kind, json.dumps(payload), _now().isoformat()))


def _load(conn, sid):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row is None:
        raise LookupError(f"No such session: {sid}")
    s = dict(row)
    for key in ("candidate_faults", "candidate_causes", "asked"):
        s[key] = json.loads(s[key])
    return s


def _save(conn, s):
    conn.execute(
        "UPDATE sessions SET fault_id=?, cause_id=?, state=?, candidate_faults=?, candidate_causes=?, asked=?, "
        "step_idx=?, outcome=?, ended_at=? WHERE id=?",
        (s["fault_id"], s["cause_id"], s["state"], json.dumps(s["candidate_faults"]), json.dumps(s["candidate_causes"]),
         json.dumps(s["asked"]), s["step_idx"], s["outcome"], s["ended_at"], s["id"]),
    )


def _graph(conn, fault_id):
    """One fault's causes, questions and answer links, loaded into plain dicts."""
    g = {"causes": {}, "questions": {}, "cause_opts": {}}
    for r in conn.execute("SELECT * FROM causes WHERE fault_id = ? ORDER BY id", (fault_id,)):
        g["causes"][r["id"]] = dict(r)
        g["cause_opts"][r["id"]] = set()
    for q in conn.execute("SELECT * FROM questions WHERE fault_id = ? ORDER BY id", (fault_id,)):
        opts = conn.execute("SELECT id, label FROM options WHERE question_id = ? ORDER BY id", (q["id"],)).fetchall()
        g["questions"][q["id"]] = {"id": q["id"], "text": q["text"], "options": [dict(o) for o in opts]}
    for r in conn.execute("SELECT co.cause_id, co.option_id FROM cause_option co JOIN causes c ON c.id = co.cause_id "
                          "WHERE c.fault_id = ?", (fault_id,)):
        g["cause_opts"][r["cause_id"]].add(r["option_id"])
    return g


def _compatible(g, cause_id, question_id, option_id):
    """A cause fits an answer if it lists that option, or says nothing about the question."""
    option_ids = {o["id"] for o in g["questions"][question_id]["options"]}
    listed = g["cause_opts"][cause_id] & option_ids
    return not listed or option_id in listed


def _next_question(g, cands, asked_qids):
    """The unasked question whose worst-case answer leaves the fewest causes. Ties: lowest id."""
    best = None
    for qid, q in g["questions"].items():
        if qid in asked_qids:
            continue
        sizes = [sum(_compatible(g, c, qid, o["id"]) for c in cands) for o in q["options"]]
        if max(sizes) >= len(cands):  # every answer keeps every cause: question tells us nothing
            continue
        if best is None or (max(sizes), qid) < best[0]:
            best = ((max(sizes), qid), qid)
    return best[1] if best else None


# ---------- state transitions ----------

def _end(conn, s, outcome):
    s["state"], s["outcome"], s["ended_at"] = outcome, outcome, _now().isoformat()
    duration = (datetime.fromisoformat(s["ended_at"]) - datetime.fromisoformat(s["started_at"])).total_seconds()
    conn.execute(
        "INSERT INTO resolution_times (session_id, fault_id, cause_id, started_at, ended_at, duration_s, outcome, "
        "questions_asked) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (s["id"], s["fault_id"], s["cause_id"], s["started_at"], s["ended_at"], duration, outcome, len(s["asked"])),
    )


def _escalate(conn, s, reason):
    _log(conn, s["id"], "escalate", {"reason": reason, "text": REASONS[reason]})
    _end(conn, s, "escalated")


def _advance(conn, s):
    """Decide what happens next, given the surviving candidate causes."""
    cands = s["candidate_causes"]
    if not cands:
        return _escalate(conn, s, "no_cause")
    if len(cands) == 1:
        s["cause_id"] = cands[0]
        if conn.execute("SELECT needs_technician FROM causes WHERE id = ?", (cands[0],)).fetchone()[0]:
            return _escalate(conn, s, "technician")
        s["state"], s["step_idx"] = "fixing", 0
        return
    if len(s["asked"]) >= MAX_QUESTIONS:
        return _escalate(conn, s, "too_many_questions")
    g = _graph(conn, s["fault_id"])
    if _next_question(g, cands, {a["question_id"] for a in s["asked"]}) is None:
        return _escalate(conn, s, "ambiguous")
    s["state"] = "questioning"


def _enter_fault(conn, s, fault_id):
    s["fault_id"] = fault_id
    s["candidate_causes"] = [r["id"] for r in conn.execute("SELECT id FROM causes WHERE fault_id = ? ORDER BY id", (fault_id,))]
    _advance(conn, s)


def match_faults(conn, manual_id, description):
    """Faults ranked by their best-matching symptom: [(fault_id, cosine score), ...]."""
    q = llm.embed([description])[0]
    best = {}
    for r in conn.execute("SELECT s.fault_id, s.embedding FROM symptoms s JOIN faults f ON f.id = s.fault_id "
                          "WHERE f.manual_id = ?", (manual_id,)):
        score = float(db.blob_to_vec(r["embedding"]) @ q)
        best[r["fault_id"]] = max(best.get(r["fault_id"], -1.0), score)
    return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))


# ---------- public API ----------

def start(manual_id, description):
    description = description.strip()
    if not description:
        raise ValueError("Describe the problem first.")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM manuals WHERE id = ?", (manual_id,)).fetchone() is None:
            raise LookupError(f"No such manual: {manual_id}")
        ranked = match_faults(conn, manual_id, description)
        sid = conn.execute("INSERT INTO sessions (manual_id, description, state, started_at) VALUES (?, ?, 'questioning', ?)",
                           (manual_id, description, _now().isoformat())).lastrowid
        s = _load(conn, sid)
        _log(conn, sid, "match", {"scores": [[f, round(v, 3)] for f, v in ranked[:3]]})
        close = [f for f, v in ranked if v >= MATCH_MIN and ranked[0][1] - v <= MARGIN][:3]
        if not close:
            _escalate(conn, s, "no_match")
        elif len(close) == 1:
            _enter_fault(conn, s, close[0])
        else:
            s["state"], s["candidate_faults"] = "choose_fault", close
        _save(conn, s)
        return _view(conn, s)


def get(session_id):
    with db.connect() as conn:
        return _view(conn, _load(conn, session_id))


def answer(session_id, option_id):
    """Answer the current question (or pick a fault when several matched)."""
    with db.connect() as conn:
        s = _load(conn, session_id)
        if s["state"] == "choose_fault":
            if option_id == NONE_OF_THESE:
                _log(conn, s["id"], "fault_choice", {"fault_id": None})
                _escalate(conn, s, "no_match")
            elif option_id in s["candidate_faults"]:
                _log(conn, s["id"], "fault_choice", {"fault_id": option_id})
                _enter_fault(conn, s, option_id)
            else:
                raise ValueError("That is not one of the offered faults.")
        elif s["state"] == "questioning":
            g = _graph(conn, s["fault_id"])
            asked = {a["question_id"] for a in s["asked"]}
            qid = _next_question(g, s["candidate_causes"], asked)
            option = next((o for o in g["questions"][qid]["options"] if o["id"] == option_id), None)
            if option is None:
                raise ValueError("That is not an option for the current question.")
            s["asked"].append({"question_id": qid, "option_id": option_id})
            s["candidate_causes"] = [c for c in s["candidate_causes"] if _compatible(g, c, qid, option_id)]
            _log(conn, s["id"], "answer", {"question": g["questions"][qid]["text"], "answer": option["label"]})
            _advance(conn, s)
        else:
            raise ValueError(f"Nothing to answer in state '{s['state']}'.")
        _save(conn, s)
        return _view(conn, s)


def step_done(session_id):
    with db.connect() as conn:
        s = _load(conn, session_id)
        steps = _steps(conn, s["cause_id"]) if s["state"] == "fixing" else []
        if s["state"] != "fixing" or s["step_idx"] >= len(steps):
            raise ValueError("There is no step to mark as done.")
        _log(conn, s["id"], "step", {"seq": s["step_idx"] + 1, "text": steps[s["step_idx"]]["text"]})
        s["step_idx"] += 1
        _save(conn, s)
        return _view(conn, s)


def fix_result(session_id, fixed):
    """After the last step: did it fix the problem?"""
    with db.connect() as conn:
        s = _load(conn, session_id)
        if s["state"] != "fixing" or s["step_idx"] < len(_steps(conn, s["cause_id"])):
            raise ValueError("Finish all the steps first.")
        _log(conn, s["id"], "fix_result", {"cause_id": s["cause_id"], "fixed": fixed})
        if fixed:
            _end(conn, s, "resolved")
        else:
            s["candidate_causes"] = [c for c in s["candidate_causes"] if c != s["cause_id"]]
            s["cause_id"], s["step_idx"] = None, 0
            if s["candidate_causes"]:
                _advance(conn, s)
            else:
                _escalate(conn, s, "fix_failed")
        _save(conn, s)
        return _view(conn, s)


def escalate(session_id):
    """The operator can always hand the case to a technician."""
    with db.connect() as conn:
        s = _load(conn, session_id)
        if s["state"] in ("resolved", "escalated"):
            raise ValueError("This case has already ended.")
        _escalate(conn, s, "operator")
        _save(conn, s)
        return _view(conn, s)


# ---------- what the UI sees ----------

def _steps(conn, cause_id):
    return [dict(r) for r in conn.execute("SELECT seq, text, is_warning FROM fix_steps WHERE cause_id = ? ORDER BY seq", (cause_id,))]


def _view(conn, s):
    start = datetime.fromisoformat(s["started_at"])
    end = datetime.fromisoformat(s["ended_at"]) if s["ended_at"] else _now()
    v = {"session_id": s["id"], "state": s["state"], "description": s["description"],
         "elapsed_s": round((end - start).total_seconds(), 1), "fault": None, "cause": None,
         "question": None, "step": None, "awaiting_result": False, "reason": None, "questions_asked": len(s["asked"])}
    v["machine"] = conn.execute("SELECT machine FROM manuals WHERE id = ?", (s["manual_id"],)).fetchone()[0]
    if s["fault_id"]:
        v["fault"] = conn.execute("SELECT id, title, source_pages FROM faults WHERE id = ?", (s["fault_id"],)).fetchone()
        v["fault"] = dict(v["fault"])
    if s["cause_id"]:
        v["cause"] = conn.execute("SELECT id, description, needs_technician, reason FROM causes WHERE id = ?", (s["cause_id"],)).fetchone()
        v["cause"] = dict(v["cause"])

    if s["state"] == "choose_fault":
        faults = [dict(conn.execute("SELECT id, title FROM faults WHERE id = ?", (f,)).fetchone()) for f in s["candidate_faults"]]
        v["question"] = {"kind": "fault", "text": "Which of these is closest to your problem?",
                         "options": [{"id": f["id"], "label": f["title"]} for f in faults] + [{"id": NONE_OF_THESE, "label": "None of these"}]}
    elif s["state"] == "questioning":
        g = _graph(conn, s["fault_id"])
        qid = _next_question(g, s["candidate_causes"], {a["question_id"] for a in s["asked"]})
        q = g["questions"][qid]
        v["question"] = {"kind": "cause", "text": q["text"], "number": len(s["asked"]) + 1, "options": q["options"]}
    elif s["state"] == "fixing":
        steps = _steps(conn, s["cause_id"])
        if s["step_idx"] < len(steps):
            st = steps[s["step_idx"]]
            v["step"] = {"number": s["step_idx"] + 1, "total": len(steps), "text": st["text"], "is_warning": bool(st["is_warning"])}
        else:
            v["awaiting_result"] = True
    elif s["state"] == "escalated":
        r = conn.execute("SELECT payload FROM session_log WHERE session_id = ? AND kind = 'escalate' ORDER BY id DESC LIMIT 1", (s["id"],)).fetchone()
        v["reason"] = json.loads(r[0])["text"]
    return v


def fault_time_stats():
    """Per-fault resolution time from the fault_time_stats view (resolved sessions only)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT t.fault_id, f.title, t.runs, t.avg_s, t.min_s, t.max_s FROM fault_time_stats t "
            "LEFT JOIN faults f ON f.id = t.fault_id ORDER BY t.avg_s DESC").fetchall()
        return [dict(r) for r in rows]

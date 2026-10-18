"""Promotion loop: escalated cases that get resolved are queued for a supervisor.

Approving adds the case to the fault graph:
  - the case matched a fault  -> the operator's wording becomes a new symptom of that fault
  - the case matched nothing  -> a new fault with one cause, and the resolution note as its fix steps
Nothing changes in the graph until a supervisor approves.
"""
import json
import re
from datetime import datetime, timezone

from app import db, llm

NEW_CAUSE_TEXT = "Cause not documented in the manual; fix recorded on site."


def _now():
    return datetime.now(timezone.utc).isoformat()


def _steps(note):
    """One step per non-empty line; leading numbers and bullets are stripped."""
    lines = (re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip() for line in note.splitlines())
    return [line for line in lines if line]


def submit(session_id, note):
    note = note.strip()
    if not note:
        raise ValueError("Describe how it was fixed first.")
    with db.connect() as conn:
        s = conn.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if s is None:
            raise LookupError(f"No such session: {session_id}")
        if s["state"] != "escalated":
            raise ValueError("Only escalated cases can be sent for review.")
        if conn.execute("SELECT 1 FROM review_queue WHERE session_id = ?", (session_id,)).fetchone():
            raise ValueError("This case has already been sent for review.")
        review_id = conn.execute("INSERT INTO review_queue (session_id, resolution_note) VALUES (?, ?)",
                                 (session_id, note)).lastrowid
    return {"review_id": review_id, "status": "pending"}


def _plan(conn, session, note):
    """What approving this case would do to the graph."""
    fault = None
    if session["fault_id"]:
        fault = conn.execute("SELECT id, title FROM faults WHERE id = ?", (session["fault_id"],)).fetchone()
    if fault:
        return {"kind": "symptom", "fault_id": fault["id"],
                "summary": f"Add “{session['description']}” as a new way of describing the fault “{fault['title']}”."}
    steps = _steps(note)
    return {"kind": "fault", "fault_id": None,
            "summary": f"Create a new fault “{session['description'][:80]}” with one fix path of {len(steps)} step(s)."}


def list_reviews(pending=True):
    cond = "r.status = 'pending'" if pending else "r.status != 'pending'"
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT r.id, r.session_id, r.resolution_note, r.status, r.reviewed_at FROM review_queue r "
            f"WHERE {cond} ORDER BY r.id {'ASC' if pending else 'DESC'}").fetchall()
        out = []
        for r in rows:
            s = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (r["session_id"],)).fetchone())
            log = [(k, json.loads(p)) for k, p in
                   conn.execute("SELECT kind, payload FROM session_log WHERE session_id = ? ORDER BY id", (s["id"],))]
            machine = conn.execute("SELECT machine FROM manuals WHERE id = ?", (s["manual_id"],)).fetchone()
            fault = conn.execute("SELECT title FROM faults WHERE id = ?", (s["fault_id"],)).fetchone() if s["fault_id"] else None
            out.append({
                "id": r["id"], "status": r["status"], "reviewed_at": r["reviewed_at"], "session_id": s["id"],
                "machine": machine[0] if machine else "Unknown", "description": s["description"],
                "fault": fault[0] if fault else None,
                "reason": next((p["text"] for k, p in reversed(log) if k == "escalate"), ""),
                "questions": [{"question": p["question"], "answer": p["answer"]} for k, p in log if k == "answer"],
                "steps_tried": [p["text"] for k, p in log if k == "step"],
                "resolution_note": r["resolution_note"],
                "plan": _plan(conn, s, r["resolution_note"]),
            })
        return out


def decide(review_id, approve):
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM review_queue WHERE id = ?", (review_id,)).fetchone()
        if r is None:
            raise LookupError(f"No such review: {review_id}")
        if r["status"] != "pending":
            raise ValueError(f"This case was already {r['status']}.")
        if approve:
            s = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (r["session_id"],)).fetchone())
            plan = _plan(conn, s, r["resolution_note"])
            vec = db.vec_to_blob(llm.embed([s["description"]])[0])
            if plan["kind"] == "symptom":
                if not conn.execute("SELECT 1 FROM symptoms WHERE fault_id = ? AND text = ?", (plan["fault_id"], s["description"])).fetchone():
                    conn.execute("INSERT INTO symptoms (fault_id, text, embedding) VALUES (?, ?, ?)", (plan["fault_id"], s["description"], vec))
            else:
                fault_id = conn.execute("INSERT INTO faults (manual_id, title) VALUES (?, ?)", (s["manual_id"], s["description"][:80])).lastrowid
                conn.execute("INSERT INTO symptoms (fault_id, text, embedding) VALUES (?, ?, ?)", (fault_id, s["description"], vec))
                cause_id = conn.execute("INSERT INTO causes (fault_id, description) VALUES (?, ?)", (fault_id, NEW_CAUSE_TEXT)).lastrowid
                for seq, text in enumerate(_steps(r["resolution_note"]), 1):
                    conn.execute("INSERT INTO fix_steps (cause_id, seq, text, is_warning) VALUES (?, ?, ?, ?)",
                                 (cause_id, seq, text, int(text.upper().startswith("WARNING"))))
        status = "approved" if approve else "rejected"
        conn.execute("UPDATE review_queue SET status = ?, reviewed_at = ? WHERE id = ?", (status, _now(), review_id))
    return {"review_id": review_id, "status": status}

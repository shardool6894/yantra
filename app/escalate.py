"""Escalation report for a technician. Built from the session log with a fixed template, no LLM."""
import json
from datetime import datetime

from app import db


def _duration(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def build(session_id):
    """Return {"report": {...}, "text": "..."} for an escalated session."""
    with db.connect() as conn:
        s = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if s is None:
            raise LookupError(f"No such session: {session_id}")
        if s["state"] != "escalated":
            raise ValueError("Only escalated cases have a report.")

        log = [(r["kind"], json.loads(r["payload"])) for r in
               conn.execute("SELECT kind, payload FROM session_log WHERE session_id = ? ORDER BY id", (session_id,))]
        machine = conn.execute("SELECT machine FROM manuals WHERE id = ?", (s["manual_id"],)).fetchone()
        fault = conn.execute("SELECT title, source_pages FROM faults WHERE id = ?", (s["fault_id"],)).fetchone() if s["fault_id"] else None
        cause = conn.execute("SELECT description, reason FROM causes WHERE id = ?", (s["cause_id"],)).fetchone() if s["cause_id"] else None
        failed = [conn.execute("SELECT description FROM causes WHERE id = ?", (p["cause_id"],)).fetchone()[0]
                  for kind, p in log if kind == "fix_result" and not p["fixed"]]

    duration = (datetime.fromisoformat(s["ended_at"]) - datetime.fromisoformat(s["started_at"])).total_seconds()
    report = {
        "case_id": s["id"],
        "opened": datetime.fromisoformat(s["started_at"]).strftime("%Y-%m-%d %H:%M UTC"),
        "machine": machine[0] if machine else "Unknown",
        "symptom": s["description"],
        "fault": fault["title"] if fault else None,
        "source_pages": fault["source_pages"] if fault and fault["source_pages"] else None,
        "cause": cause["description"] if cause else None,
        "cause_note": cause["reason"] if cause and cause["reason"] else None,
        "reason": next((p["text"] for kind, p in reversed(log) if kind == "escalate"), ""),
        "questions": [{"question": p["question"], "answer": p["answer"]} for kind, p in log if kind == "answer"],
        "steps_tried": [p["text"] for kind, p in log if kind == "step"],
        "failed_causes": failed,
        "time_on_case": _duration(duration),
    }
    return {"report": report, "text": to_text(report)}


def to_text(r):
    lines = [
        "YANTRA ESCALATION REPORT",
        f"Case {r['case_id']}, opened {r['opened']}, time on case {r['time_on_case']}",
        "",
        f"Machine: {r['machine']}",
        f"Reported problem: {r['symptom']}",
        f"Matched fault: {r['fault'] or 'none (no confident match)'}",
    ]
    if r["source_pages"]:
        lines.append(f"Manual reference: page {r['source_pages']}")
    if r["cause"]:
        lines.append(f"Likely cause: {r['cause']}")
    if r["cause_note"]:
        lines.append(f"Why a technician is needed: {r['cause_note']}")
    lines.append(f"Escalated because: {r['reason']}")
    lines += ["", "Questions and answers:"]
    lines += [f"  {i}. {q['question']} -> {q['answer']}" for i, q in enumerate(r["questions"], 1)] or ["  none asked"]
    lines += ["", "Steps tried:"]
    lines += [f"  {i}. {t}" for i, t in enumerate(r["steps_tried"], 1)] or ["  none"]
    if r["failed_causes"]:
        lines += ["", "Fixes that did not work:"] + [f"  - {c}" for c in r["failed_causes"]]
    return "\n".join(lines)

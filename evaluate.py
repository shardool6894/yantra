"""Run tests.json through the diagnosis flow with a simulated operator and print a results table.

    python evaluate.py

Each case names the fault and cause that should be found. The simulated operator answers every
question the way that cause would, follows the steps and says the fix worked. It runs on a scratch
copy of the database built from the sample manual, so live sessions and promoted cases are untouched.

Case types ("expect"):
  fix         -> must end resolved on the expected fault and cause
  technician  -> must escalate because the expected cause needs a technician
  no_match    -> must escalate with no fault matched
"""
import json
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from app import db, diagnose
from app.ingest import ingest
from config import MANUALS_DIR, ROOT

NONE_OF_THESE = diagnose.NONE_OF_THESE


def consistent_option(conn, cause_id, question):
    """First option of the question that fits the cause (a cause with no link to the question fits any)."""
    ids = [o["id"] for o in question["options"]]
    linked = {r[0] for r in conn.execute(
        f"SELECT option_id FROM cause_option WHERE cause_id = ? AND option_id IN ({','.join('?' * len(ids))})", [cause_id] + ids)}
    return next((i for i in ids if not linked or i in linked), ids[0])


def run_case(conn, manual_id, case):
    """Drive one case to the end. Returns (final view, questions asked)."""
    cause_id = None
    if case["cause"]:
        cause_id = conn.execute("SELECT c.id FROM causes c JOIN faults f ON f.id = c.fault_id "
                                "WHERE f.manual_id = ? AND f.title = ? AND c.description = ?",
                                (manual_id, case["fault"], case["cause"])).fetchone()
        if cause_id is None:
            raise SystemExit(f"tests.json refers to a cause that is not in the graph: {case['cause']!r}")
        cause_id = cause_id[0]

    v = diagnose.start(manual_id, case["description"])
    sid = v["session_id"]
    while v["state"] in ("choose_fault", "questioning"):
        q = v["question"]
        if q["kind"] == "fault":
            pick = next((o["id"] for o in q["options"] if o["label"] == case["fault"]), NONE_OF_THESE)
        else:
            pick = consistent_option(conn, cause_id, q)
        v = diagnose.answer(sid, pick)
    while v["state"] == "fixing":
        v = diagnose.step_done(sid) if v["step"] else diagnose.fix_result(sid, True)
    return v


def passed(case, v):
    fault = v["fault"]["title"] if v["fault"] else None
    cause = v["cause"]["description"] if v["cause"] else None
    if case["expect"] == "fix":
        return v["state"] == "resolved" and fault == case["fault"] and cause == case["cause"]
    if case["expect"] == "technician":
        return v["state"] == "escalated" and fault == case["fault"] and cause == case["cause"]
    return v["state"] == "escalated" and fault is None  # no_match


def main():
    spec = json.loads((ROOT / "tests.json").read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db.DB_PATH = Path(tmp) / "eval.db"  # scratch database, discarded afterwards
        info = ingest(MANUALS_DIR / spec["manual"])
        results = []
        with closing(db.connect()) as conn:  # close it, or Windows cannot delete the scratch file
            for n, case in enumerate(spec["cases"], 1):
                v = run_case(conn, info["manual_id"], case)
                dur = conn.execute("SELECT duration_s FROM resolution_times WHERE session_id = ?", (v["session_id"],)).fetchone()[0]
                results.append({
                    "n": n, "description": case["description"], "expect": case["expect"], "passed": passed(case, v),
                    "outcome": v["state"], "reason": v["reason"],
                    "fault": v["fault"]["title"] if v["fault"] else None,
                    "cause": v["cause"]["description"] if v["cause"] else None,
                    "expected_fault": case["fault"], "expected_cause": case["cause"],
                    "questions_asked": v["questions_asked"], "duration_s": dur,
                })

    fixed = [r for r in results if r["outcome"] == "resolved" and r["passed"]]  # correctly resolved cases only
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    summary = {
        "faults": info["faults"], "causes": info["causes"], "fix_paths": info["fix_paths"],
        "tests_passed": sum(r["passed"] for r in results), "tests_total": len(results),
        "avg_questions_to_fix": avg([r["questions_asked"] for r in fixed]),
        "avg_time_to_resolution_s": avg([r["duration_s"] for r in fixed]),
        "cases_resolved": len(fixed),
    }
    (ROOT / "results.json").write_text(json.dumps({"summary": summary, "cases": results}, indent=2), encoding="utf-8")

    rows = [
        ("Faults in graph", summary["faults"]),
        ("Causes in graph", summary["causes"]),
        ("Fix paths in graph", summary["fix_paths"]),
        ("Test cases passed", f"{summary['tests_passed']} / {summary['tests_total']}"),
        ("Avg cross-questions to a fix", summary["avg_questions_to_fix"]),
        ("Avg time to resolution", f"{summary['avg_time_to_resolution_s']} s"),
    ]
    width = max(len(k) for k, _ in rows)
    print("\nYANTRA evaluation\n" + "-" * (width + 12))
    for k, val in rows:
        print(f"{k:<{width}}  {val:>8}")
    print("-" * (width + 12))
    print(f"Time is system time with a simulated operator; averages cover the {len(fixed)} correctly resolved cases.\n")

    for r in results:
        if not r["passed"]:
            print(f"FAIL #{r['n']} [{r['expect']}] {r['description']}")
            print(f"     expected: {r['expected_fault']} / {r['expected_cause']}")
            print(f"     got:      {r['outcome']} | {r['fault']} / {r['cause']} | {r['reason']}")
    print("Saved results.json")
    sys.exit(0 if summary["tests_passed"] == summary["tests_total"] else 1)


if __name__ == "__main__":
    main()

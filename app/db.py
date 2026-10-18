import sqlite3

import numpy as np

from config import DB_PATH

SCHEMA = """
-- Fault graph: faults -> symptoms, causes -> (questions/options), fix steps
CREATE TABLE IF NOT EXISTS manuals (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    machine TEXT NOT NULL,
    source_file TEXT,
    sha TEXT
);
CREATE TABLE IF NOT EXISTS faults (
    id INTEGER PRIMARY KEY,
    manual_id INTEGER NOT NULL REFERENCES manuals(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source_pages TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS symptoms (
    id INTEGER PRIMARY KEY,
    fault_id INTEGER NOT NULL REFERENCES faults(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS causes (
    id INTEGER PRIMARY KEY,
    fault_id INTEGER NOT NULL REFERENCES faults(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    needs_technician INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    fault_id INTEGER NOT NULL REFERENCES faults(id) ON DELETE CASCADE,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cause_option (
    cause_id INTEGER NOT NULL REFERENCES causes(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES options(id) ON DELETE CASCADE,
    PRIMARY KEY (cause_id, option_id)
);
CREATE TABLE IF NOT EXISTS fix_steps (
    id INTEGER PRIMARY KEY,
    cause_id INTEGER NOT NULL REFERENCES causes(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    is_warning INTEGER NOT NULL DEFAULT 0
);

-- Runtime tables (plain integer ids, no FKs, so re-ingesting a manual never breaks history)
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    manual_id INTEGER,
    description TEXT NOT NULL,
    fault_id INTEGER,
    cause_id INTEGER,
    state TEXT NOT NULL,
    candidate_faults TEXT NOT NULL DEFAULT '[]',
    candidate_causes TEXT NOT NULL DEFAULT '[]',
    asked TEXT NOT NULL DEFAULT '[]',
    step_idx INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS session_log (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    resolution_note TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS resolution_times (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    fault_id INTEGER,
    cause_id INTEGER,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_s REAL NOT NULL,
    outcome TEXT NOT NULL,
    questions_asked INTEGER NOT NULL DEFAULT 0
);
CREATE VIEW IF NOT EXISTS fault_time_stats AS
SELECT fault_id,
       COUNT(*) AS runs,
       AVG(duration_s) AS avg_s,
       MIN(duration_s) AS min_s,
       MAX(duration_s) AS max_s
FROM resolution_times
WHERE outcome = 'resolved'
GROUP BY fault_id;
"""


class _Conn(sqlite3.Connection):
    """`with connect() as conn` commits (or rolls back) and then closes, so no file handle is left open."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, factory=_Conn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def vec_to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vec(blob):
    return np.frombuffer(blob, dtype=np.float32)


def manual_counts(conn, manual_id):
    """Faults / causes / fix paths / questions / steps for one manual."""
    q = lambda sql: conn.execute(sql, (manual_id,)).fetchone()[0]
    return {
        "faults": q("SELECT COUNT(*) FROM faults WHERE manual_id = ?"),
        "causes": q("SELECT COUNT(*) FROM causes c JOIN faults f ON f.id = c.fault_id WHERE f.manual_id = ?"),
        # a fix path is one cause with its ordered steps (technician causes escalate instead)
        "fix_paths": q(
            "SELECT COUNT(DISTINCT c.id) FROM causes c JOIN faults f ON f.id = c.fault_id "
            "JOIN fix_steps s ON s.cause_id = c.id WHERE f.manual_id = ?"
        ),
        "questions": q("SELECT COUNT(*) FROM questions qq JOIN faults f ON f.id = qq.fault_id WHERE f.manual_id = ?"),
        "steps": q(
            "SELECT COUNT(*) FROM fix_steps s JOIN causes c ON c.id = s.cause_id "
            "JOIN faults f ON f.id = c.fault_id WHERE f.manual_id = ?"
        ),
    }

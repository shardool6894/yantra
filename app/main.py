from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db, diagnose, escalate as escalation, llm, review
from app.ingest import ingest
from config import MANUALS_DIR, ROOT


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    llm.embed(["warm up"])  # load the embedding model now, not on the first operator request
    yield


app = FastAPI(title="YANTRA", lifespan=lifespan)


@app.get("/api/manuals")
def list_manuals():
    """Ingested manuals with graph counts, plus pre-loaded PDFs that have not been ingested yet."""
    with db.connect() as conn:
        rows = conn.execute("SELECT id, name, machine, source_file FROM manuals ORDER BY name").fetchall()
        ingested = [{**dict(r), **db.manual_counts(conn, r["id"])} for r in rows]
    done = {m["source_file"] for m in ingested}
    available = sorted(p.name for p in MANUALS_DIR.glob("*.pdf") if p.name not in done)
    return {"ingested": ingested, "preloaded": available}


@app.post("/api/ingest")
def ingest_manual(file: UploadFile | None = File(None), preloaded: str | None = Form(None)):
    """Upload a PDF, or name a PDF already in data/manuals. Returns the ingestion summary."""
    MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    if file is not None:
        name = Path(file.filename or "").name
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "Please upload a PDF file.")
        path = MANUALS_DIR / name
        path.write_bytes(file.file.read())
    elif preloaded:
        path = MANUALS_DIR / Path(preloaded).name
        if not path.is_file():
            raise HTTPException(404, "No such pre-loaded manual.")
    else:
        raise HTTPException(400, "Send a PDF file or the name of a pre-loaded manual.")
    try:
        return ingest(path)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(422, str(e))


# ---------- diagnosis ----------

class StartBody(BaseModel):
    manual_id: int
    description: str


class AnswerBody(BaseModel):
    option_id: int


class FixBody(BaseModel):
    fixed: bool


def _run(fn, *args):
    """Call a diagnose function and turn its errors into HTTP errors."""
    try:
        return fn(*args)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/sessions")
def start_session(body: StartBody):
    return _run(diagnose.start, body.manual_id, body.description)


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int):
    return _run(diagnose.get, session_id)


@app.post("/api/sessions/{session_id}/answer")
def answer(session_id: int, body: AnswerBody):
    return _run(diagnose.answer, session_id, body.option_id)


@app.post("/api/sessions/{session_id}/step-done")
def step_done(session_id: int):
    return _run(diagnose.step_done, session_id)


@app.post("/api/sessions/{session_id}/fix-result")
def fix_result(session_id: int, body: FixBody):
    return _run(diagnose.fix_result, session_id, body.fixed)


@app.post("/api/sessions/{session_id}/escalate")
def escalate(session_id: int):
    return _run(diagnose.escalate, session_id)


@app.get("/api/sessions/{session_id}/report")
def report(session_id: int):
    return _run(escalation.build, session_id)


class NoteBody(BaseModel):
    note: str


@app.post("/api/sessions/{session_id}/resolve")
def resolve(session_id: int, body: NoteBody):
    """An escalated case was fixed later: queue it for supervisor review."""
    return _run(review.submit, session_id, body.note)


# ---------- supervisor review ----------

@app.get("/api/reviews")
def reviews(status: str = "pending"):
    return review.list_reviews(pending=status == "pending")


@app.post("/api/reviews/{review_id}/approve")
def approve(review_id: int):
    return _run(review.decide, review_id, True)


@app.post("/api/reviews/{review_id}/reject")
def reject(review_id: int):
    return _run(review.decide, review_id, False)


@app.get("/api/stats/faults")
def fault_stats():
    return diagnose.fault_time_stats()


# must be last: serves the UI at / and does not shadow the /api routes above
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")

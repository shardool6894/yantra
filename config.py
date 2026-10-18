import os

from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows: cache works fine without symlinks

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "yantra.db"
MANUALS_DIR = ROOT / "data" / "manuals"
EXTRACTIONS_DIR = ROOT / "data" / "extractions"

# --- Models (swap here) ---
LLM_PROVIDER = "gemini"  # ingestion only. "gemini" or "huggingface"
LLM_FALLBACKS = ["gemini-3.6-flash", "gemini-3.7-flash"]  # gemini only: tried in turn when a model's daily free quota is used up
LLM_MODEL = "gemini-3.5-flash"  # gemini free tier: 20 requests/day per model. For huggingface use e.g. "Qwen/Qwen2.5-7B-Instruct"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # local; used at ingestion and for runtime matching
HF_TOKEN = os.environ.get("HF_TOKEN")  # needed when LLM_PROVIDER = "huggingface"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")  # needed when LLM_PROVIDER = "gemini"

# --- Ingestion ---
CHUNK_CHARS = 900  # the embedding model only reads ~1000 characters, so keep chunks below that
FAULT_LIST_CHUNKS = 30  # chunks sent to the LLM to list fault titles
FAULT_DETAIL_CHUNKS = 2  # best-matching chunks per fault...
FAULT_NEIGHBOURS = 3  # ...plus this many chunks after each, so a whole fault section is included

# --- Diagnosis (fixed thresholds) ---
MATCH_MIN = 0.50  # below this cosine similarity -> escalate (measured: off-topic text tops out at 0.45)
MARGIN = 0.05  # top-2 faults closer than this -> ask which one
MAX_QUESTIONS = 6  # more than this -> escalate

"""The only module that calls language models (Gemini or Hugging Face) or loads the embedding model. Configure in config.py."""
import json
import re
import sys
import time
import urllib.error
import urllib.request

import numpy as np

from config import EMBED_MODEL, GEMINI_API_KEY, HF_TOKEN, LLM_FALLBACKS, LLM_MODEL, LLM_PROVIDER

_embedder = None


def embed(texts):
    """Unit-length float32 vectors, shape (len(texts), dim). Local model, deterministic."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        try:  # use the local copy if there is one, so startup makes no network calls
            _embedder = SentenceTransformer(EMBED_MODEL, local_files_only=True)
        except OSError:
            _embedder = SentenceTransformer(EMBED_MODEL)  # first run: download it
    return np.asarray(_embedder.encode(list(texts), normalize_embeddings=True), dtype=np.float32)


def generate(system, prompt):
    """Ingestion-time only. Never called while an operator is diagnosing."""
    if LLM_PROVIDER == "gemini":
        return _gemini(system, prompt)
    return _huggingface(system, prompt)


class DailyQuota(Exception):
    pass


_used_up = set()  # gemini models whose daily free quota ran out during this run


def _gemini(system, prompt):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Ingesting a new manual calls the LLM; set it to a Gemini API key. "
                           "(The pre-loaded sample manual works without it.)")
    for model in [LLM_MODEL, *LLM_FALLBACKS]:
        if model in _used_up:
            continue
        try:
            return _gemini_call(model, system, prompt)
        except DailyQuota:
            _used_up.add(model)
            print(f"daily free quota used up for {model}", file=sys.stderr, flush=True)
    raise RuntimeError("Daily free quota is used up for every configured model. Progress is saved; run the same "
                       "command again later, or add models to LLM_FALLBACKS in config.py.")


def _gemini_call(model, system, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 8192, "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingBudget": 0}},
    }).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                data = json.load(res)
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 429 and "PerDay" in detail:  # a daily quota will not clear by waiting
                raise DailyQuota()
            if e.code in (429, 500, 503) and attempt < 4:  # per-minute limit or overload: wait and retry
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini request failed ({e.code}): {detail[:300]}")
        except (TimeoutError, urllib.error.URLError):  # slow or dropped connection: wait and retry
            if attempt == 4:
                raise RuntimeError("Could not reach Gemini (timed out five times).")
            time.sleep(5 * (attempt + 1))
        except (KeyError, IndexError):
            raise RuntimeError("Gemini returned no text (the response may have been blocked).")


def _huggingface(system, prompt):
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN is not set. Ingesting a new manual calls the LLM; set it to a Hugging Face "
                           "access token. (The pre-loaded sample manual works without it.)")
    from huggingface_hub import InferenceClient

    client = InferenceClient(model=LLM_MODEL, token=HF_TOKEN)
    out = client.chat_completion(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=3000,
        temperature=0.01,
    )
    return out.choices[0].message.content


def generate_json(system, prompt, tries=3):
    """Ask for JSON, parse the first {...} block, retry on bad output."""
    last = None
    for _ in range(tries):
        text = generate(system, prompt)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            return json.loads(match.group(0))
        except (AttributeError, json.JSONDecodeError) as e:
            last = e
    raise ValueError(f"LLM did not return valid JSON after {tries} tries: {last}")

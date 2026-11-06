"""Minimal client for an OpenAI-compatible chat endpoint, with an on-disk cache.

Two things use it, both only for claim extraction: error injection (so omission can pick a
finding to drop and negation can insert a "no"; `generate_fallbacks.py inject --no-llm` skips it)
and scripts/precompute_links.py (sentence links for the hover highlighting). Retrieval never does.

Configure with environment variables:

    ANNOTATION_LLM_URL      base URL ending in /v1          (default http://localhost:8000/v1)
    ANNOTATION_LLM_MODEL    model name served at that URL   (required)
    ANNOTATION_LLM_KEY      bearer token, if the server wants one
    ANNOTATION_LLM_WORKERS  concurrent requests             (default 16)
    ANNOTATION_LLM_CACHE    sqlite cache file               (default data/.llm_cache.sqlite)
    ANNOTATION_LLM_MAX_TOKENS  claim-extraction answer budget (default 700); raise it, e.g. to 4096,
                            for a reasoning model, which spends the budget thinking first
    ANNOTATION_LLM_NO_THINK 1 = ask the server to skip the thinking phase (Qwen 3.x-style
                            `chat_template_kwargs`). Not every server honours it: a LiteLLM gateway
                            in front of glm-5.3-flash ignored it, so raise the budget instead

Report text is sent to that endpoint. Point it at a server you are allowed to send your reports
to -- for gated datasets that usually means one inside your own network.

Two properties matter:

* **Deterministic.** temperature=0 plus a cache keyed by the exact prompt, so a re-run reproduces
  the same claims instead of re-billing the endpoint.
* **Auditable.** Every call stores its prompt and raw response in the cache file.

A reasoning model works, but spends tokens thinking before it answers; if answers come back
empty, raise ANNOTATION_LLM_MAX_TOKENS or use a non-reasoning model.

Vendored from radmetric (MIT, same author); see reporttools/__init__.py.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent

BASE_URL = os.environ.get("ANNOTATION_LLM_URL", "http://localhost:8000/v1").rstrip("/")
MODEL = os.environ.get("ANNOTATION_LLM_MODEL", "")
API_KEY = os.environ.get("ANNOTATION_LLM_KEY", "EMPTY")
MAX_WORKERS = int(os.environ.get("ANNOTATION_LLM_WORKERS", "16"))
CACHE_PATH = os.environ.get("ANNOTATION_LLM_CACHE", str(_ROOT / "data" / ".llm_cache.sqlite"))
# Module-level so a caller can switch it on (precompute_links.py --no-think). Thinking and
# non-thinking answers are cached separately.
NO_THINK = os.environ.get("ANNOTATION_LLM_NO_THINK", "") == "1"


class _Cache:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # WAL plus a busy timeout so concurrent jobs can share one cache file.
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " k TEXT PRIMARY KEY, prompt TEXT, response TEXT, model TEXT, ts REAL)")
        self._conn.commit()

    def get(self, k: str) -> str | None:
        with self._lock:
            r = self._conn.execute("SELECT response FROM cache WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def put(self, k: str, prompt: str, response: str, model: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
                               (k, prompt, response, model, time.time()))
            self._conn.commit()


# Opened on first use, so importing this module (or running --no-llm) never creates a file.
_cache: _Cache | None = None
_cache_lock = threading.Lock()


def _get_cache() -> _Cache:
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _Cache(CACHE_PATH)
        return _cache


@dataclass
class LLMStats:
    calls: int = 0
    cache_hits: int = 0
    errors: int = 0
    empty: int = 0
    total_seconds: float = 0.0

    def as_dict(self) -> dict:
        return {"calls": self.calls, "cache_hits": self.cache_hits, "errors": self.errors,
                "empty_responses": self.empty,
                "mean_latency": round(self.total_seconds / self.calls, 3) if self.calls else 0.0}


STATS = LLMStats()
_stats_lock = threading.Lock()


def _key(prompt: str, system: str, model: str, max_tokens: int) -> str:
    h = hashlib.sha256()
    h.update(f"{model}\x00{max_tokens}\x00{system}\x00{prompt}".encode())
    return h.hexdigest()


def require_model() -> str:
    if not MODEL:
        raise RuntimeError(
            "No LLM configured. Set ANNOTATION_LLM_URL and ANNOTATION_LLM_MODEL (and "
            "ANNOTATION_LLM_KEY if needed), or run injection with --no-llm.")
    return MODEL


def chat(prompt: str, *, system: str = "", max_tokens: int = 256,
         model: str | None = None, use_cache: bool = True, timeout: int = 180) -> str:
    """One deterministic completion. Cached by exact prompt."""
    model = model or require_model()
    k = _key(prompt, system, model + ("#nothink" if NO_THINK else ""), max_tokens)
    cache = _get_cache() if use_cache else None
    if cache is not None:
        hit = cache.get(k)
        if hit is not None:
            with _stats_lock:
                STATS.cache_hits += 1
            return hit

    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0}
    if NO_THINK:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    t0 = time.time()
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(f"{BASE_URL}/chat/completions",
                              headers={"Authorization": f"Bearer {API_KEY}",
                                       "Content-Type": "application/json"},
                              json=payload, timeout=timeout)
            r.raise_for_status()
            # `content` is null on some servers when a reasoning model spends the whole budget
            # thinking; treat that as an empty answer rather than retrying a deterministic result.
            out = (r.json()["choices"][0]["message"].get("content") or "").strip()
            with _stats_lock:
                STATS.calls += 1
                STATS.total_seconds += time.time() - t0
                STATS.empty += not out
            if cache is not None and out:
                cache.put(k, prompt, out, model)
            return out
        except Exception as e:                      # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    with _stats_lock:
        STATS.errors += 1
    raise RuntimeError(f"LLM call failed after retries: {last_err}")


def chat_many(prompts: list[str], *, system: str = "", max_tokens: int = 256,
              model: str | None = None, workers: int = MAX_WORKERS) -> list[str]:
    """Parallel completions, preserving order. A failed call yields "" and is counted in STATS."""
    model = model or require_model()
    out: list[str] = [""] * len(prompts)

    def one(i_p):
        i, p = i_p
        try:
            return i, chat(p, system=system, max_tokens=max_tokens, model=model)
        except Exception:                            # noqa: BLE001
            return i, ""

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, res in ex.map(one, list(enumerate(prompts))):
            out[i] = res
    return out


def health() -> dict:
    """Is the endpoint reachable and answering? `ok` means the request succeeded.

    The budget is generous on purpose: a reasoning model spends a small one thinking and returns an
    empty answer, which with max_tokens=8 reported a working endpoint as down.
    """
    t0 = time.time()
    try:
        out = chat("Reply with exactly: OK", max_tokens=1024, use_cache=False)
        return {"ok": True, "answered": out.strip().upper().startswith("OK"),
                "latency": round(time.time() - t0, 3), "model": MODEL, "url": BASE_URL,
                "response": out}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": str(e), "url": BASE_URL, "model": MODEL,
                "latency": round(time.time() - t0, 3), "response": ""}

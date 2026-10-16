# engine/ai_client.py
# KNCS LeadGen Pro — v10.1 (Audit Compliant)
# Matches Master Technical Audit & Project Handover Document v1.0
#
# Fixes applied in this revision:
#   BUG-AI-01: sync_circuit_state_from_db() on import; sync_circuit_to_db()
#              on every state change
#   BUG-AI-02: _sanitise_for_log() applied to all sensitive log output
#   BUG-AI-03: Cache TTL via get_cached_ai_score() unix epoch (database.py)
#   BUG-AI-04: ensure_cache_schema() removed — init_database() sole authority
#   BUG-AI-05: secrets.randbelow() replaces random.uniform() for jitter
#   BUG-AI-06: Full backoff calculation inside single lock scope
#   BUG-AI-07: last_call_at set AFTER sleep completes
#   BUG-AI-08: Unified cache — get_cached_ai_score/save_ai_cache only
#   BUG-AI-09: get_last_ai_error() thread-local
#   BUG-AI-10: _sanitise_for_log() on all response body log output
#   BUG-AI-11: _extract_text() returns "" on unknown dict structure
#   BUG-AI-12: temperature rounded to 2dp before hashing
#   ISSUE-A:   _sync_circuit_to_db clears reason correctly on close
#   ISSUE-B:   save_ai_cache receives dict not pre-serialised string
#   ISSUE-D:   _open_circuit_internal() dead seconds param removed
#   ISSUE-E:   datetime.now(timezone.utc) throughout — timezone-aware
#   ISSUE-G:   Bearer token redaction pattern includes '.' for JWT tokens
#   ISSUE-I:   _consecutive_failures reset on circuit auto-close

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib3
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from dotenv import load_dotenv

# Suppress InsecureRequestWarning from verify=False workaround
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────
# Imports — database.py is SOLE cache/schema authority
# BUG-AI-04: ensure_cache_schema() removed from this file
# BUG-AI-08: get_cached_ai_score / save_ai_cache are the only
#            cache functions used — no competing implementation
# ─────────────────────────────────────────────────────────
try:
    from database import (
        get_cached_ai_score,
        get_system_state,
        mark_ai_cache_failed,
        save_ai_cache,
        set_system_state,
    )
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "database.py not available — cache/circuit breaker disabled"
    )

load_dotenv()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Config — read once at import (BUG-CHS-02 pattern)
# BUG-SC-09 / BUG-AI-09: CF token tries all three known env var names
# ─────────────────────────────────────────────────────────
CLOUDFLARE_ACCOUNT_ID: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN: str = (
    os.getenv("CLOUDFLARE_API_TOKEN")
    or os.getenv("CF_API_TOKEN")
    or os.getenv("CLOUDFLARE_API_KEY")
    or ""
).strip()
CLOUDFLARE_AI_MODEL: str = (
    os.getenv("CLOUDFLARE_AI_MODEL")
    or "@cf/meta/llama-3.1-8b-instruct"
).strip()

REQUEST_TIMEOUT    = float(os.getenv("AI_REQUEST_TIMEOUT",    "45"))
MIN_CALL_DELAY     = float(os.getenv("AI_MIN_CALL_DELAY",     "1.5"))
CACHE_TTL_SECONDS  = int(os.getenv("AI_CACHE_TTL_SECONDS",    "86400"))
FAILURE_THRESHOLD  = int(os.getenv("AI_FAILURE_THRESHOLD",    "3"))
CIRCUIT_OPEN_SECONDS = int(os.getenv("AI_CIRCUIT_OPEN_SECONDS", "120"))
BASE_BACKOFF_SECONDS = float(os.getenv("AI_BACKOFF_SECONDS",  "2.0"))
MAX_ATTEMPTS       = int(os.getenv("AI_MAX_ATTEMPTS",          "3"))
JITTER_SECONDS     = float(os.getenv("AI_JITTER_SECONDS",     "0.35"))


# ─────────────────────────────────────────────────────────
# Thread-local storage for last error (BUG-AI-09)
# ─────────────────────────────────────────────────────────
_thread_local = threading.local()


def _set_last_error(error: str) -> None:
    _thread_local.last_ai_error = str(error)


def _clear_last_error() -> None:
    _thread_local.last_ai_error = ""


def get_last_ai_error() -> str:
    """
    BUG-AI-09: Thread-local — safe under concurrent scoring.
    Returns error description or "" if no error.
    """
    return getattr(_thread_local, "last_ai_error", "")


# ─────────────────────────────────────────────────────────
# Log Sanitisation (BUG-AI-02, BUG-AI-10)
# ISSUE-G: Bearer pattern includes '.' for JWT-format tokens
# ─────────────────────────────────────────────────────────

def _sanitise_for_log(text: str, max_len: int = 300) -> str:
    """
    BUG-AI-02 / BUG-AI-10: Redacts sensitive strings from all log output.
    Removes Bearer tokens (including JWT with dots), Account IDs, API keys.
    ISSUE-G: Pattern [A-Za-z0-9_\\-.]{20,} covers JWT-format tokens.
    """
    if not text:
        return ""
    text = str(text)

    # ISSUE-G: Include '.' in character class to catch JWT tokens
    text = re.sub(
        r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
        "Bearer ***REDACTED***",
        text,
    )
    # Redact account IDs in URLs
    text = re.sub(
        r"/accounts/[A-Za-z0-9_\-]{20,}/",
        "/accounts/***/",
        text,
    )
    # Redact Authorization header values
    text = re.sub(
        r"Authorization:\s*Bearer\s+[A-Za-z0-9_\-\.]{20,}",
        "Authorization: Bearer ***REDACTED***",
        text,
    )
    # Truncate after redaction
    if len(text) > max_len:
        text = text[:max_len] + "...[truncated]"
    return text


# ─────────────────────────────────────────────────────────
# Circuit Breaker — Persistent via system_state (BUG-AI-01)
# ISSUE-E: datetime.now(timezone.utc) throughout — timezone-aware
# ISSUE-I: _consecutive_failures reset on auto-close
# ─────────────────────────────────────────────────────────
_CB_STATE_KEY     = "circuit_breaker_state"
_CB_REASON_KEY    = "circuit_breaker_reason"
_CB_OPENED_AT_KEY = "circuit_breaker_opened_at"

_cb_lock = threading.Lock()


def _sync_circuit_state_from_db() -> None:
    """
    BUG-AI-01: Called on import — logs current DB circuit state.
    is_circuit_open() always reads from DB directly, so no in-memory
    state needs restoring. This function exists to surface the state
    in logs at startup.
    """
    if not DB_AVAILABLE:
        return
    try:
        state = get_system_state(_CB_STATE_KEY)
        if state == "open":
            reason = get_system_state(_CB_REASON_KEY) or "restored from DB"
            logger.warning(
                "[ai_client] Circuit breaker restored OPEN from DB: %s",
                _sanitise_for_log(reason),
            )
    except Exception as exc:
        logger.warning(
            "[ai_client] Failed to sync circuit state from DB: %s",
            _sanitise_for_log(str(exc)),
        )


def _sync_circuit_to_db(state: str, reason: str = "") -> None:
    """
    BUG-AI-01: Persists circuit breaker state to system_state.
    ISSUE-A: reason field is explicitly passed — not derived from state string.
    """
    if not DB_AVAILABLE:
        return
    try:
        now_iso = datetime.now(timezone.utc).isoformat()  # ISSUE-E: UTC
        if state == "open":
            set_system_state(_CB_STATE_KEY, "open")
            set_system_state(_CB_OPENED_AT_KEY, now_iso)
            set_system_state(_CB_REASON_KEY, reason or "threshold_reached")
        else:
            set_system_state(_CB_STATE_KEY, "closed")
            set_system_state(_CB_OPENED_AT_KEY, "")
            set_system_state(_CB_REASON_KEY, "")  # ISSUE-A: clear reason on close
    except Exception as exc:
        logger.warning(
            "[ai_client] Failed to sync circuit state to DB: %s",
            _sanitise_for_log(str(exc)),
        )


def is_circuit_open() -> bool:
    """
    BUG-AI-01: Checks persistent circuit breaker state from system_state.
    Survives server restarts — never uses module globals.
    ISSUE-E: UTC-aware datetime comparison.
    ISSUE-I: Resets _consecutive_failures on auto-close.
    """
    if not DB_AVAILABLE:
        return False
    try:
        state = get_system_state(_CB_STATE_KEY)
        if state != "open":
            return False

        # Check if circuit has auto-expired
        opened_at_str = get_system_state(_CB_OPENED_AT_KEY)
        if opened_at_str:
            try:
                # ISSUE-E: parse as UTC-aware datetime
                opened_at = datetime.fromisoformat(opened_at_str)
                if opened_at.tzinfo is None:
                    # Legacy value stored without timezone — treat as UTC
                    from datetime import timezone as _tz
                    opened_at = opened_at.replace(tzinfo=_tz.utc)
                age_seconds = (
                    datetime.now(timezone.utc) - opened_at
                ).total_seconds()
                if age_seconds > CIRCUIT_OPEN_SECONDS:
                    logger.info(
                        "[ai_client] Circuit breaker auto-closed after %ss",
                        int(age_seconds),
                    )
                    _sync_circuit_to_db("closed", "")
                    # ISSUE-I: reset failure counter on auto-close
                    _reset_failure_count()
                    return False
            except Exception:
                pass
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────
# Throttle — last_call_at set AFTER sleep (BUG-AI-07)
# ─────────────────────────────────────────────────────────
_last_call_at   = 0.0
_throttle_lock  = threading.Lock()


def _throttle() -> None:
    """
    BUG-AI-07: Enforce minimum spacing between calls.
    _last_call_at is updated AFTER sleep completes, not before.
    This ensures the actual gap between calls is never shorter than
    MIN_CALL_DELAY.
    """
    global _last_call_at
    with _throttle_lock:
        now      = time.time()
        elapsed  = now - _last_call_at
        wait_for = max(0.0, MIN_CALL_DELAY - elapsed)
        if wait_for > 0:
            time.sleep(wait_for)
        _last_call_at = time.time()  # BUG-AI-07: set AFTER sleep


# ─────────────────────────────────────────────────────────
# Failure Recording
# BUG-AI-05: secrets.randbelow() for jitter
# BUG-AI-06: Full backoff calculation INSIDE lock scope
# ISSUE-I: _reset_failure_count() shared between success and auto-close
# ─────────────────────────────────────────────────────────
_consecutive_failures = 0
_failure_lock         = threading.Lock()


def _reset_failure_count() -> None:
    """Resets consecutive failure counter. Called on success and auto-close."""
    global _consecutive_failures
    with _failure_lock:
        _consecutive_failures = 0


def _record_failure(
    status_code: Optional[int] = None,
    retry_after: float = 0.0,
) -> float:
    """
    BUG-AI-06: Track failures and return backoff delay.
    Full backoff calculation INSIDE lock scope — no race condition.
    BUG-AI-05: secrets.randbelow() for cryptographically safe jitter.
    """
    global _consecutive_failures
    with _failure_lock:
        _consecutive_failures += 1
        failure_num = _consecutive_failures

        # Exponential backoff with cap
        backoff = BASE_BACKOFF_SECONDS * (2 ** max(0, failure_num - 1))
        backoff = min(backoff, 60.0)

        # BUG-AI-05: cryptographically secure jitter
        jitter_range = int(JITTER_SECONDS * 1000)
        if jitter_range > 0:
            backoff += secrets.randbelow(jitter_range) / 1000.0

        if retry_after > 0:
            backoff = max(backoff, retry_after)

        # Open circuit on 429 or failure threshold
        should_open = (
            status_code == 429
            or failure_num >= FAILURE_THRESHOLD
        )
        if should_open:
            reason = (
                "429_rate_limit"
                if status_code == 429
                else f"failure_threshold_{failure_num}"
            )
            # Release lock before DB write to avoid lock contention
            # (DB write is independent — no shared state modified)
            _consecutive_failures_snapshot = _consecutive_failures

    # DB write outside lock (avoids potential deadlock with DB lock)
    if should_open:
        _sync_circuit_to_db("open", reason)

    return backoff


def _record_success() -> None:
    """
    Called on successful AI call — resets failure count and closes circuit.
    BUG-SC-06 equivalent: circuit closed on every success.
    """
    _reset_failure_count()
    _sync_circuit_to_db("closed", "")


def _parse_retry_after(header_value: Optional[str]) -> float:
    if not header_value:
        return 0.0
    try:
        return max(0.0, float(header_value))
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────
# Cache — DELEGATED to database.py (BUG-AI-03, BUG-AI-08)
# BUG-AI-04: ensure_cache_schema() removed — init_database() sole authority
# ─────────────────────────────────────────────────────────

def _hash_prompt(
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """
    BUG-AI-12: temperature rounded to 2dp before hashing.
    Ensures 0.2 and 0.20 produce identical cache keys.
    """
    temp_key = f"{float(temperature):.2f}"
    raw = f"{model}|{max_tokens}|{temp_key}|{prompt}".encode(
        "utf-8", errors="ignore"
    )
    return hashlib.sha256(raw).hexdigest()


# ─────────────────────────────────────────────────────────
# Response Extraction (BUG-AI-11)
# ─────────────────────────────────────────────────────────

def _extract_text(payload: Any) -> str:
    """
    BUG-AI-11: Extracts text from Cloudflare AI response.
    Returns "" on unknown dict structure — never mistakes a
    Cloudflare error wrapper for valid AI output.
    """
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload).strip()
    if isinstance(payload, list):
        for item in payload:
            text = _extract_text(item)
            if text:
                return text
        return ""  # BUG-AI-11: unknown list → ""
    if isinstance(payload, dict):
        # Cloudflare wraps responses in {"result": ...}
        if "result" in payload:
            result = payload.get("result")
            text = _extract_text(result)
            if text:
                return text
        for key in ("response", "text", "output", "content", "message"):
            value = payload.get(key)
            text = _extract_text(value)
            if text:
                return text
        return ""  # BUG-AI-11: unknown dict → ""
    return ""


def _extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list):
            parts = []
            for item in errors:
                if isinstance(item, dict):
                    for key in ("message", "detail", "error"):
                        val = item.get(key)
                        if val:
                            parts.append(str(val))
                            break
                elif item:
                    parts.append(str(item))
            if parts:
                return " | ".join(parts)
        for key in ("message", "error", "detail"):
            val = payload.get(key)
            if val:
                return str(val)
    return ""


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 500, 502, 503, 504}


# ─────────────────────────────────────────────────────────
# Main AI Call
# ─────────────────────────────────────────────────────────

def call_ai(
    prompt: str,
    max_tokens: int = 600,
    use_cache: bool = True,
    fallback: str = "",
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> Optional[str]:
    """
    Throttled Cloudflare AI call with:
    - Unified cache via database.py (BUG-AI-03, BUG-AI-08)
    - Retry/backoff with secrets.randbelow() jitter (BUG-AI-05)
    - Persistent circuit breaker via system_state (BUG-AI-01)
    - Bearer token / account ID sanitisation in all logs (BUG-AI-02)
    - Thread-local last error (BUG-AI-09)
    - ISSUE-B: save_ai_cache receives dict not pre-serialised string

    Returns raw text from AI model, or fallback on failure.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return fallback

    chosen_model = (model or CLOUDFLARE_AI_MODEL).strip()
    prompt_hash  = _hash_prompt(prompt, chosen_model, max_tokens, temperature)

    # ── Cache check (BUG-AI-03, BUG-AI-08) ──────────────
    if use_cache and DB_AVAILABLE:
        cached = get_cached_ai_score(prompt_hash, CACHE_TTL_SECONDS)
        if cached:
            # get_cached_ai_score returns a dict from response_json,
            # or {"raw": response_text} — extract the actual text
            response_text = (
                cached.get("response_text")
                or cached.get("raw")
                or ""
            )
            if response_text:
                _clear_last_error()
                logger.debug(
                    "[ai_client] Cache hit for prompt_hash=%s",
                    prompt_hash[:16],
                )
                return response_text

    # ── Circuit breaker (BUG-AI-01) ──────────────────────
    if is_circuit_open():
        logger.warning("[ai_client] Circuit open — returning fallback")
        _set_last_error("circuit_open")
        return fallback

    # ── Credential check ─────────────────────────────────
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        logger.warning("[ai_client] Missing Cloudflare credentials")
        _set_last_error("missing_credentials")
        return fallback

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{chosen_model}"
    )
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    payload = {
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }

    last_error = ""
    # Track whether circuit should open — determined inside _record_failure
    # but we need to know outside the lock. _record_failure returns backoff;
    # circuit open is a side-effect handled internally.

    for attempt in range(MAX_ATTEMPTS):
        if is_circuit_open():
            logger.warning(
                "[ai_client] Circuit opened during retries — returning fallback"
            )
            _set_last_error("circuit_open_during_retry")
            return fallback

        _throttle()

        try:
            # verify=False: workaround for Windows cert store issues
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                verify=False,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning(
                "[ai_client] Request failed (attempt %s/%s): %s",
                attempt + 1,
                MAX_ATTEMPTS,
                _sanitise_for_log(last_error),  # BUG-AI-02
            )
            backoff = _record_failure()
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(backoff)
                continue
            _set_last_error(last_error)
            return fallback

        # ── 429 — rate limited ────────────────────────────
        if response.status_code == 429:
            retry_after = _parse_retry_after(
                response.headers.get("Retry-After")
            )
            try:
                data       = response.json()
                last_error = _extract_error_message(data) or response.text[:200]
            except Exception:
                last_error = response.text[:200]
            _record_failure(status_code=429, retry_after=retry_after)
            logger.warning(
                "[ai_client] 429 Too Many Requests (retry_after=%s): %s",
                retry_after,
                _sanitise_for_log(last_error),  # BUG-AI-02
            )
            _set_last_error(last_error)
            return fallback  # No retry on 429 — circuit opened

        # ── Non-OK response ───────────────────────────────
        if not response.ok:
            try:
                data       = response.json()
                last_error = _extract_error_message(data) or response.text[:200]
            except Exception:
                last_error = response.text[:200]
            logger.warning(
                "[ai_client] HTTP %s (attempt %s/%s): %s",
                response.status_code,
                attempt + 1,
                MAX_ATTEMPTS,
                _sanitise_for_log(last_error),  # BUG-AI-02
            )
            if (
                _is_retryable_status(response.status_code)
                and attempt < MAX_ATTEMPTS - 1
            ):
                backoff = _record_failure(status_code=response.status_code)
                time.sleep(backoff)
                continue
            _record_failure(status_code=response.status_code)
            _set_last_error(last_error)
            return fallback

        # ── Success path ──────────────────────────────────
        try:
            data = response.json()
        except Exception:
            data = response.text

        text = _extract_text(data)
        if not text:
            last_error = "Empty AI response"
            logger.warning(
                "[ai_client] Empty AI response (attempt %s/%s)",
                attempt + 1,
                MAX_ATTEMPTS,
            )
            backoff = _record_failure()
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(backoff)
                continue
            _set_last_error(last_error)
            return fallback

        # ── Write to cache (BUG-AI-08, ISSUE-B) ──────────
        if DB_AVAILABLE:
            # ISSUE-B: pass the raw dict to save_ai_cache, not a
            # pre-serialised string. save_ai_cache handles json.dumps()
            # internally. Passing a string caused double-encoding.
            response_dict: Optional[dict] = (
                data if isinstance(data, dict) else None
            )
            save_ai_cache(
                prompt_hash,
                chosen_model,
                text,
                response_dict,   # ISSUE-B: dict, not json string
                "ok",
            )

        _record_success()
        _clear_last_error()
        logger.debug(
            "[ai_client] AI call succeeded (attempt %s/%s, model=%s)",
            attempt + 1,
            MAX_ATTEMPTS,
            chosen_model,
        )
        return text

    # ── All attempts exhausted ────────────────────────────
    logger.warning(
        "[ai_client] AI call failed after %s attempts: %s",
        MAX_ATTEMPTS,
        _sanitise_for_log(last_error),  # BUG-AI-02
    )
    _set_last_error(last_error)
    return fallback


# ─────────────────────────────────────────────────────────
# Module Initialisation
# BUG-AI-01: Sync circuit breaker from DB on import
# Must be last — all functions must be defined first
# ─────────────────────────────────────────────────────────
_sync_circuit_state_from_db()
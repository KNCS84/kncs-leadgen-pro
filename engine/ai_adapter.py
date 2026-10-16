# ai_adapter.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Clean adapter layer over ai_client.py
# Provides consistent interface for all Phase 1 modules
# Dependencies: engine/ai_client.py
# Critical rules observed:
#   - is_circuit_open() before every AI call
#   - call_ai() handles caching internally
#   - Graceful degradation — always returns safe default
#   - logger not print()

import json
import logging
from typing import Optional

from engine.ai_client import is_circuit_open, call_ai

log = logging.getLogger(__name__)


def ai_is_available() -> bool:
    """
    Return True if the AI circuit is closed and calls are safe.
    Wraps is_circuit_open() with inverted logic for readability.
    """
    try:
        return not is_circuit_open()
    except Exception as exc:
        log.error("ai_adapter.ai_is_available error: %s", exc)
        return False


def call_ai_json(prompt: str, context: str = "") -> Optional[dict]:
    """
    Call AI and parse the response as JSON.

    call_ai() handles caching and retries internally.
    This wrapper adds JSON parsing and error handling.

    Args:
        prompt:  The full prompt string to send
        context: Optional context string for logging only

    Returns:
        Parsed dict on success.
        None if circuit open, AI fails, or JSON invalid.
    """
    if not ai_is_available():
        log.warning("ai_adapter: circuit open — skipping AI call [%s]", context)
        return None

    try:
        raw = call_ai(prompt)
        if not raw:
            log.warning("ai_adapter: empty AI response [%s]", context)
            return None

        # Strip markdown code fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.startswith("```")]
            clean = "\n".join(lines).strip()

        result = json.loads(clean)
        log.info("ai_adapter: parsed JSON response [%s]", context)
        return result

    except json.JSONDecodeError as exc:
        log.error(
            "ai_adapter: JSON parse error [%s]: %s | raw=%s",
            context, exc, str(raw)[:200]
        )
        return None
    except Exception as exc:
        log.error("ai_adapter: call_ai_json error [%s]: %s", context, exc)
        return None


def call_ai_score(prompt: str, context: str = "") -> Optional[int]:
    """
    Call AI and extract a numeric score from the response.

    Expects AI to return JSON with a 'score' key.
    Returns int 0-100 on success, None on failure.
    """
    result = call_ai_json(prompt, context)
    if result is None:
        return None

    try:
        score = int(result.get("score", -1))
        if 0 <= score <= 100:
            return score
        log.warning(
            "ai_adapter: score %s out of range [%s]", score, context
        )
        return None
    except (TypeError, ValueError) as exc:
        log.error(
            "ai_adapter: score extraction error [%s]: %s", context, exc
        )
        return None


def call_ai_text(prompt: str, context: str = "") -> Optional[str]:
    """
    Call AI and return raw text response.

    Returns string on success, None on failure.
    """
    if not ai_is_available():
        log.warning("ai_adapter: circuit open — skipping [%s]", context)
        return None

    try:
        raw = call_ai(prompt)
        if not raw:
            log.warning("ai_adapter: empty text response [%s]", context)
            return None
        return raw.strip()
    except Exception as exc:
        log.error("ai_adapter.call_ai_text error [%s]: %s", context, exc)
        return None
# engine/scrape_guard.py
"""
Scrape Risk Monitor + Block Detection for LeadGen Pro.

Tracks daily request counts per source.
Detects block pages immediately.
Calculates risk level with BLOCKED status.
Recommends when to stop scraping.
"""

from datetime import date, datetime
from database import get_connection
import re


# ─────────────────────────────────────────────────────────
# Block detection patterns
# ─────────────────────────────────────────────────────────

BLOCK_PATTERNS = {
    "yelp": [
        "you have been blocked",
        "behavior of the browser has caught our attention",
        "we've detected some unusual activity",
        "please verify you're a human",
        "security check",
    ],
    "google": [
        "unusual traffic from your computer network",
        "our systems have detected unusual traffic",
        "please show you're not a robot",
        "we're sorry, but your computer",
    ],
    "general": [
        "access denied",
        "forbidden",
        "captcha",
        "robot",
        "automated queries",
        "suspicious activity",
        "too many requests",
        "rate limit",
        "blocked",
        "verify you are human",
        "please complete the security check",
        "cloudflare",
        "challenge-platform",
    ],
}


# ─────────────────────────────────────────────────────────
# Risk thresholds per source
# ─────────────────────────────────────────────────────────

LIMITS = {
    "google_maps": {
        "safe":    50,
        "caution": 100,
        "warning": 150,
        "stop":    200,
    },
    "b2bhint": {
        "safe":    200,
        "caution": 400,
        "warning": 600,
        "stop":    800,
    },
    "yelp": {
        "safe":    30,
        "caution": 60,
        "warning": 100,
        "stop":    150,
    },
    "yellowpages": {
        "safe":    40,
        "caution": 80,
        "warning": 120,
        "stop":    160,
    },
    "google_search": {
        "safe":    30,
        "caution": 60,
        "warning": 100,
        "stop":    150,
    },
    "bing_search": {
        "safe":    50,
        "caution": 100,
        "warning": 150,
        "stop":    200,
    },
    "duckduckgo": {
        "safe":    50,
        "caution": 100,
        "warning": 150,
        "stop":    200,
    },
    "default": {
        "safe":    50,
        "caution": 100,
        "warning": 150,
        "stop":    200,
    },
}

# Risk level definitions
RISK_LEVELS = {
    "safe":    {
        "label": "🟢 SAFE",
        "color": "#10b981",
        "message": "Safe to continue scraping.",
        "action": "Continue",
    },
    "caution": {
        "label": "🟡 CAUTION",
        "color": "#f59e0b",
        "message": (
            "Moderate usage. "
            "Consider slowing down."
        ),
        "action": "Slow down",
    },
    "warning": {
        "label": "🟠 WARNING",
        "color": "#fb923c",
        "message": (
            "High usage detected. "
            "Switch to outreach soon."
        ),
        "action": "Switch to outreach",
    },
    "stop": {
        "label": "🔴 STOP",
        "color": "#ef4444",
        "message": (
            "Daily limit reached. "
            "Stop scraping. "
            "Focus on calling leads."
        ),
        "action": "Stop — call leads now",
    },
    "blocked": {
        "label": "⛔ BLOCKED",
        "color": "#dc2626",
        "message": (
            "Block detected or too many failures. "
            "Do not scrape this source today."
        ),
        "action": "BLOCKED — resume tomorrow",
    },
}


# ─────────────────────────────────────────────────────────
# Block detection
# ─────────────────────────────────────────────────────────

def detect_block_page(html_content: str, source: str = "general") -> dict:
    """
    Detect if HTML content is a block/CAPTCHA page.
    
    Returns:
        {
            "is_blocked": bool,
            "reason": str,
            "patterns_found": list,
        }
    """
    if not html_content:
        return {
            "is_blocked": False,
            "reason": "",
            "patterns_found": [],
        }

    html_lower = html_content.lower()
    patterns_found = []

    # Check source-specific patterns
    source_patterns = BLOCK_PATTERNS.get(source, [])
    for pattern in source_patterns:
        if pattern.lower() in html_lower:
            patterns_found.append(pattern)

    # Check general patterns
    for pattern in BLOCK_PATTERNS["general"]:
        if pattern.lower() in html_lower:
            if pattern not in patterns_found:
                patterns_found.append(pattern)

    is_blocked = len(patterns_found) > 0

    reason = ""
    if is_blocked:
        reason = f"Block page detected: {patterns_found[0]}"

    return {
        "is_blocked": is_blocked,
        "reason": reason,
        "patterns_found": patterns_found,
    }


def is_block_page(html_content: str, source: str = "general") -> bool:
    """Quick check if content is a block page."""
    result = detect_block_page(html_content, source)
    return result["is_blocked"]


# ─────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────

def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            source     TEXT,
            keyword    TEXT,
            location   TEXT,
            req_count  INTEGER DEFAULT 1,
            date       TEXT DEFAULT (date('now')),
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _ensure_failures_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_failures (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source       TEXT,
            keyword      TEXT,
            location     TEXT,
            error_type   TEXT,
            error_message TEXT,
            is_block     INTEGER DEFAULT 0,
            failed_at    TEXT DEFAULT (datetime('now')),
            date         TEXT DEFAULT (date('now'))
        )
    """)
    conn.commit()


def log_request(
    source: str,
    keyword: str = "",
    location: str = "",
    count: int = 1,
):
    """
    Log scraping requests for risk tracking.
    Call this after each scraping run.
    """
    conn = get_connection()

    try:
        _ensure_table(conn)

        conn.execute(
            """
            INSERT INTO scrape_requests
            (source, keyword, location, req_count, date)
            VALUES (?, ?, ?, ?, date('now'))
            """,
            (source, keyword, location, count),
        )
        conn.commit()

    except Exception as e:
        print(f"⚠️ scrape_guard log error: {e}")

    finally:
        conn.close()


def log_failure(
    source: str,
    keyword: str = "",
    location: str = "",
    error_type: str = "unknown",
    error_message: str = "",
    is_block: bool = False,
):
    """
    Log a scraping failure.
    Use this when scraper returns error or block page.
    """
    conn = get_connection()

    try:
        _ensure_failures_table(conn)

        conn.execute(
            """
            INSERT INTO scrape_failures
            (source, keyword, location, error_type, error_message, is_block, date)
            VALUES (?, ?, ?, ?, ?, ?, date('now'))
            """,
            (source, keyword, location, error_type, error_message, 1 if is_block else 0),
        )
        conn.commit()

        print(f"⚠️ Failure logged: {source} - {error_type}")

    except Exception as e:
        print(f"⚠️ log_failure error: {e}")

    finally:
        conn.close()


def log_success(
    source: str,
    keyword: str = "",
    location: str = "",
):
    """
    Log a successful scrape.
    Resets failure streak for this source.
    """
    conn = get_connection()

    try:
        _ensure_failures_table(conn)

        # Mark this as a success by logging a special entry
        conn.execute(
            """
            INSERT INTO scrape_failures
            (source, keyword, location, error_type, error_message, is_block, date)
            VALUES (?, ?, ?, 'SUCCESS', 'Scrape successful', 0, date('now'))
            """,
            (source, keyword, location),
        )
        conn.commit()

    except Exception as e:
        print(f"⚠️ log_success error: {e}")

    finally:
        conn.close()


def get_consecutive_failures(source: str, hours: int = 24) -> int:
    """
    Get count of consecutive failures for a source.
    Stops counting when it hits a success.
    """
    conn = get_connection()

    try:
        _ensure_failures_table(conn)

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT error_type
            FROM scrape_failures
            WHERE source = ?
            AND failed_at >= datetime('now', '-' || ? || ' hours')
            ORDER BY failed_at DESC
            """,
            (source, hours),
        )

        rows = cursor.fetchall()

        if not rows:
            return 0

        consecutive = 0
        for row in rows:
            error_type = row[0]
            if error_type == "SUCCESS":
                break
            consecutive += 1

        return consecutive

    except Exception:
        return 0

    finally:
        conn.close()


def get_recent_blocks(source: str, hours: int = 24) -> int:
    """
    Get count of block detections for a source in last N hours.
    """
    conn = get_connection()

    try:
        _ensure_failures_table(conn)

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM scrape_failures
            WHERE source = ?
            AND is_block = 1
            AND failed_at >= datetime('now', '-' || ? || ' hours')
            """,
            (source, hours),
        )

        row = cursor.fetchone()
        return row[0] if row else 0

    except Exception:
        return 0

    finally:
        conn.close()


def get_daily_count(source: str) -> int:
    """
    Get total requests made today for a source.
    """
    conn = get_connection()

    try:
        _ensure_table(conn)

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT COALESCE(SUM(req_count), 0)
            FROM scrape_requests
            WHERE source = ?
            AND date = date('now')
            """,
            (source,),
        )

        row = cursor.fetchone()

        return row[0] if row else 0

    except Exception:
        return 0

    finally:
        conn.close()


def get_all_daily_counts() -> dict:
    """
    Get today's request counts for all sources.
    """
    conn = get_connection()

    try:
        _ensure_table(conn)

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT source, COALESCE(SUM(req_count), 0)
            FROM scrape_requests
            WHERE date = date('now')
            GROUP BY source
            """
        )

        return {
            row[0]: row[1]
            for row in cursor.fetchall()
        }

    except Exception:
        return {}

    finally:
        conn.close()


def get_weekly_counts() -> dict:
    """
    Get request counts for the past 7 days.
    """
    conn = get_connection()

    try:
        _ensure_table(conn)

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT date, source,
                   COALESCE(SUM(req_count), 0)
            FROM scrape_requests
            WHERE date >= date('now', '-7 days')
            GROUP BY date, source
            ORDER BY date DESC
            """
        )

        results = {}

        for row in cursor.fetchall():
            d, s, count = row[0], row[1], row[2]
            if d not in results:
                results[d] = {}
            results[d][s] = count

        return results

    except Exception:
        return {}

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Risk calculation (UPDATED with BLOCKED status)
# ─────────────────────────────────────────────────────────

def get_risk_level(source: str) -> str:
    """
    Calculate risk level for a source.
    Returns: 'safe' | 'caution' | 'warning' | 'stop' | 'blocked'
    """
    # Check for blocks first
    recent_blocks = get_recent_blocks(source, hours=24)
    if recent_blocks > 0:
        return "blocked"

    # Check consecutive failures
    consecutive = get_consecutive_failures(source, hours=24)
    if consecutive >= 3:
        return "blocked"

    # Check request count thresholds
    count  = get_daily_count(source)
    limits = LIMITS.get(source, LIMITS["default"])

    if count >= limits["stop"]:
        return "stop"
    elif count >= limits["warning"]:
        return "warning"
    elif count >= limits["caution"]:
        return "caution"
    else:
        return "safe"


def get_overall_risk() -> str:
    """
    Get the highest risk level across all sources.
    """
    levels = ["safe", "caution", "warning", "stop", "blocked"]

    counts = get_all_daily_counts()

    highest = "safe"

    for source, count in counts.items():
        level = get_risk_level(source)
        if levels.index(level) > levels.index(highest):
            highest = level

    return highest


def get_risk_summary() -> dict:
    """
    Full risk summary for UI display.

    Returns dict with:
    - overall_risk
    - per_source breakdown
    - recommendations
    - leads to call count
    - block warnings
    """
    from database import get_dashboard_stats

    counts   = get_all_daily_counts()
    overall  = get_overall_risk()
    risk_def = RISK_LEVELS[overall]

    per_source = {}

    all_sources = set(list(counts.keys()) + [
        "google_maps", "b2bhint", "yelp", "yellowpages"
    ])

    for source in all_sources:
        count  = counts.get(source, 0)
        level  = get_risk_level(source)
        limits = LIMITS.get(source, LIMITS["default"])

        consecutive = get_consecutive_failures(source, hours=24)
        blocks = get_recent_blocks(source, hours=24)

        per_source[source] = {
            "count":      count,
            "level":      level,
            "label":      RISK_LEVELS[level]["label"],
            "color":      RISK_LEVELS[level]["color"],
            "safe_limit": limits["safe"],
            "stop_limit": limits["stop"],
            "remaining":  max(0, limits["stop"] - count),
            "consecutive_failures": consecutive,
            "recent_blocks": blocks,
        }

    try:
        stats = get_dashboard_stats()
        uncalled = (
            stats.get("total_leads", 0)
            - stats.get("leads_called", 0)
        )
    except Exception:
        uncalled = 0

    recommendations = []

    if overall == "blocked":
        recommendations.append(
            "⛔ BLOCKED: Block page or too many failures detected."
        )
        recommendations.append(
            "🛑 Do NOT scrape any sources today."
        )
        recommendations.append(
            f"📞 Call your {uncalled} uncalled leads instead."
        )
        recommendations.append(
            "⏰ Resume scraping tomorrow or use a different network."
        )
    elif overall == "safe":
        recommendations.append(
            "✅ Safe to continue scraping."
        )
    elif overall == "caution":
        recommendations.append(
            "🟡 Consider slowing down requests."
        )
        recommendations.append(
            f"📞 You have {uncalled} uncalled leads."
        )
    elif overall == "warning":
        recommendations.append(
            "🟠 High usage. Switch to outreach soon."
        )
        recommendations.append(
            f"📞 Call your {uncalled} uncalled leads now."
        )
    elif overall == "stop":
        recommendations.append(
            "🔴 Stop scraping for today."
        )
        recommendations.append(
            f"📞 Focus on {uncalled} uncalled leads."
        )
        recommendations.append(
            "⏰ Resume scraping tomorrow."
        )

    return {
        "overall_risk":    overall,
        "overall_label":   risk_def["label"],
        "overall_color":   risk_def["color"],
        "overall_message": risk_def["message"],
        "overall_action":  risk_def["action"],
        "per_source":      per_source,
        "recommendations": recommendations,
        "uncalled_leads":  uncalled,
        "today":           str(date.today()),
    }


def is_safe_to_scrape(source: str) -> bool:
    """
    Quick check — is it safe to scrape this source now?
    """
    level = get_risk_level(source)
    return level in ["safe", "caution"]


def should_stop_scraping(source: str) -> bool:
    """
    Should we stop scraping this source today?
    """
    level = get_risk_level(source)
    return level in ["stop", "blocked"]


def is_source_blocked(source: str) -> bool:
    """
    Is this source currently blocked?
    """
    level = get_risk_level(source)
    return level == "blocked"


def get_remaining_safe_requests(source: str) -> int:
    """
    How many more requests can we safely make today?
    """
    count  = get_daily_count(source)
    limits = LIMITS.get(source, LIMITS["default"])
    return max(0, limits["safe"] - count)
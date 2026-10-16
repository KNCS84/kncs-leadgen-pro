# website_health_scanner.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Website health scanner for MSP vertical
# Scans business websites and scores health 0-100
# Dependencies: database.py, engine/anti_detection.py
# Critical rules observed:
#   - get_connection() via database.py only
#   - Parameterized SQL only
#   - logger not print()
#   - requests.Session for all HTTP calls
#   - Follow anti_detection.py patterns
#   - Graceful degradation on all failures
#   - No raw exceptions exposed to caller
#   - Start from 100, deduct for problems found

import logging
import ssl
import socket
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.exceptions import (
    ConnectionError,
    SSLError,
    Timeout,
    TooManyRedirects,
)

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

REQUEST_TIMEOUT       = 15    # seconds
SLOW_LOAD_THRESHOLD   = 5.0   # seconds — deduct if exceeded
SSL_EXPIRY_WARN_DAYS  = 30    # days — deduct if expiring soon
MAX_HEALTH_SCORE      = 100

DEDUCTIONS = {
    "no_ssl":                  30,
    "ssl_expiring_30_days":    20,
    "load_time_over_5s":       15,
    "not_mobile_responsive":   20,
    "broken_homepage":         15,
    "no_contact_form":         10,
}

# Mobile responsive indicators checked in HTML
MOBILE_META_TAGS = [
    'name="viewport"',
    "name='viewport'",
]

# Contact form indicators checked in HTML
CONTACT_FORM_INDICATORS = [
    "<form",
    "contact-form",
    "wpcf7",           # WordPress Contact Form 7
    "ninja-forms",
    "gravity-form",
    'type="email"',
    "type='email'",
]

# Broken page indicators
BROKEN_PAGE_INDICATORS = [
    "404",
    "page not found",
    "error 404",
    "not found",
    "this page doesn't exist",
    "coming soon",
    "under construction",
    "website unavailable",
]

# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    """
    Build a requests Session with browser-like headers.
    Follows anti_detection patterns without importing
    the module directly to avoid circular dependencies.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return session


def _normalise_url(url: str) -> str:
    """
    Ensure URL has a scheme prefix.
    Adds https:// if missing.
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _extract_domain(url: str) -> str:
    """
    Extract domain from URL.
    Returns empty string on failure.
    """
    try:
        parsed = urlparse(_normalise_url(url))
        return parsed.netloc.lower().replace("www.", "")
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# INDIVIDUAL CHECK FUNCTIONS
# ─────────────────────────────────────────────────────────────

def get_ssl_status(url: str) -> dict:
    """
    Check SSL certificate status for a URL.

    Returns dict:
      has_ssl:       bool
      expiry_date:   str | None  (ISO format)
      days_remaining: int | None
      is_expiring:   bool  (within SSL_EXPIRY_WARN_DAYS)
      error:         str | None
    """
    result = {
        "has_ssl":        False,
        "expiry_date":    None,
        "days_remaining": None,
        "is_expiring":    False,
        "error":          None,
    }

    try:
        url_clean = _normalise_url(url)
        parsed    = urlparse(url_clean)
        hostname  = parsed.netloc.split(":")[0]

        if parsed.scheme != "https":
            result["error"] = "URL uses HTTP not HTTPS"
            return result

        ctx = ssl.create_default_context()
        with ctx.wrap_socket(
            socket.socket(),
            server_hostname=hostname
        ) as ssock:
            ssock.settimeout(REQUEST_TIMEOUT)
            ssock.connect((hostname, 443))
            cert = ssock.getpeercert()

        expiry_str  = cert.get("notAfter", "")
        expiry_date = datetime.strptime(
            expiry_str, "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)

        now            = datetime.now(timezone.utc)
        days_remaining = (expiry_date - now).days

        result["has_ssl"]        = True
        result["expiry_date"]    = expiry_date.isoformat()
        result["days_remaining"] = days_remaining
        result["is_expiring"]    = days_remaining <= SSL_EXPIRY_WARN_DAYS

        log.info(
            "website_health: SSL for %s — expires in %d days",
            hostname, days_remaining
        )

    except ssl.SSLError as exc:
        result["error"] = f"SSL error: {exc}"
        log.warning("website_health: SSL error for %s: %s", url, exc)

    except socket.timeout:
        result["error"] = "SSL check timed out"
        log.warning("website_health: SSL timeout for %s", url)

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("website_health: SSL check failed for %s: %s", url, exc)

    return result


def get_load_time(url: str) -> float:
    """
    Measure page load time in seconds using requests.

    Returns float seconds.
    Returns 0.0 on failure (not penalised if unreachable).
    Returns 99.0 if connection fails (will be penalised).
    """
    try:
        session    = _build_session()
        url_clean  = _normalise_url(url)
        start      = time.time()
        response   = session.get(
            url_clean,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        elapsed = time.time() - start
        log.info(
            "website_health: load time for %s = %.2fs",
            url, elapsed
        )
        return round(elapsed, 2)

    except Timeout:
        log.warning("website_health: load time timeout for %s", url)
        return 99.0  # Penalise — page took too long

    except (ConnectionError, SSLError, TooManyRedirects) as exc:
        log.warning(
            "website_health: load time check failed for %s: %s",
            url, exc
        )
        return 0.0  # Cannot reach — no penalty from load time

    except Exception as exc:
        log.error(
            "website_health.get_load_time error for %s: %s",
            url, exc
        )
        return 0.0


def check_mobile_responsive(html: str) -> bool:
    """
    Check if page HTML contains mobile viewport meta tag.

    Args:
        html: Raw HTML string from page response

    Returns True if mobile responsive indicators found.
    """
    if not html:
        return False
    html_lower = html.lower()
    return any(tag.lower() in html_lower for tag in MOBILE_META_TAGS)


def check_contact_form(html: str) -> bool:
    """
    Check if page HTML contains a contact form.

    Args:
        html: Raw HTML string from page response

    Returns True if contact form indicators found.
    """
    if not html:
        return False
    html_lower = html.lower()
    return any(
        indicator.lower() in html_lower
        for indicator in CONTACT_FORM_INDICATORS
    )


def check_broken_elements(html: str, status_code: int) -> bool:
    """
    Check if homepage appears broken or incomplete.

    Args:
        html:        Raw HTML string from page response
        status_code: HTTP status code from response

    Returns True if broken indicators found.
    """
    # Non-200 status codes indicate problems
    if status_code not in (200, 301, 302):
        return True

    if not html:
        return True

    html_lower = html.lower()
    return any(
        indicator in html_lower
        for indicator in BROKEN_PAGE_INDICATORS
    )


def _fetch_page(url: str) -> dict:
    """
    Fetch page content and return response details.

    Returns dict:
      html:        str | None
      status_code: int
      final_url:   str
      reachable:   bool
      error:       str | None
    """
    result = {
        "html":        None,
        "status_code": 0,
        "final_url":   url,
        "reachable":   False,
        "error":       None,
    }

    try:
        session   = _build_session()
        url_clean = _normalise_url(url)
        response  = session.get(
            url_clean,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        result["html"]        = response.text
        result["status_code"] = response.status_code
        result["final_url"]   = response.url
        result["reachable"]   = True
        log.info(
            "website_health: fetched %s — status %d",
            url, response.status_code
        )

    except Timeout:
        result["error"] = "Page fetch timed out"
        log.warning("website_health: fetch timeout for %s", url)

    except SSLError as exc:
        result["error"] = f"SSL error on fetch: {exc}"
        log.warning("website_health: SSL fetch error for %s", url)

    except ConnectionError as exc:
        result["error"] = f"Connection failed: {exc}"
        log.warning("website_health: connection error for %s", url)

    except Exception as exc:
        result["error"] = str(exc)
        log.error(
            "website_health._fetch_page error for %s: %s", url, exc
        )

    return result


# ─────────────────────────────────────────────────────────────
# SCORE CALCULATION
# ─────────────────────────────────────────────────────────────

def calculate_health_score(scan_results: dict) -> int:
    """
    Calculate website health score starting from 100,
    deducting for each problem found.

    Deductions:
      No SSL                    -30
      SSL expiring within 30d   -20
      Load time over 5s         -15
      Not mobile responsive     -20
      Broken homepage elements  -15
      No contact form           -10

    Returns int score clamped to 0-100.
    """
    score      = MAX_HEALTH_SCORE
    deductions = []

    # SSL checks
    ssl_info = scan_results.get("ssl", {})
    if not ssl_info.get("has_ssl", False):
        score -= DEDUCTIONS["no_ssl"]
        deductions.append(
            f"No SSL (-{DEDUCTIONS['no_ssl']})"
        )
    elif ssl_info.get("is_expiring", False):
        score -= DEDUCTIONS["ssl_expiring_30_days"]
        deductions.append(
            f"SSL expiring soon (-{DEDUCTIONS['ssl_expiring_30_days']})"
        )

    # Load time check
    load_time = scan_results.get("load_time", 0.0)
    if load_time > SLOW_LOAD_THRESHOLD:
        score -= DEDUCTIONS["load_time_over_5s"]
        deductions.append(
            f"Slow load {load_time:.1f}s "
            f"(-{DEDUCTIONS['load_time_over_5s']})"
        )

    # Mobile responsiveness
    if not scan_results.get("mobile_responsive", True):
        score -= DEDUCTIONS["not_mobile_responsive"]
        deductions.append(
            f"Not mobile responsive "
            f"(-{DEDUCTIONS['not_mobile_responsive']})"
        )

    # Broken homepage
    if scan_results.get("broken_homepage", False):
        score -= DEDUCTIONS["broken_homepage"]
        deductions.append(
            f"Broken homepage (-{DEDUCTIONS['broken_homepage']})"
        )

    # Contact form
    if not scan_results.get("has_contact_form", True):
        score -= DEDUCTIONS["no_contact_form"]
        deductions.append(
            f"No contact form (-{DEDUCTIONS['no_contact_form']})"
        )

    final = min(max(score, 0), MAX_HEALTH_SCORE)
    log.info(
        "website_health: score=%d deductions=%s",
        final, deductions
    )
    return final


# ─────────────────────────────────────────────────────────────
# MAIN SCAN FUNCTION
# ─────────────────────────────────────────────────────────────

def scan_website(url: str) -> dict:
    """
    Run full website health scan for a given URL.

    Performs:
      1. SSL certificate check
      2. Page load time measurement
      3. Mobile responsiveness check
      4. Contact form detection
      5. Broken homepage detection
      6. Health score calculation

    Returns dict:
      url, health_score, ssl, load_time,
      mobile_responsive, has_contact_form,
      broken_homepage, reachable,
      scanned_at, error

    Always returns a dict — never raises.
    """
    log.info("website_health.scan_website: scanning %s", url)

    result = {
        "url":               url,
        "health_score":      0,
        "ssl":               {},
        "load_time":         0.0,
        "mobile_responsive": False,
        "has_contact_form":  False,
        "broken_homepage":   True,
        "reachable":         False,
        "scanned_at":        datetime.utcnow().isoformat(),
        "error":             None,
    }

    if not url or not url.strip():
        result["error"] = "No URL provided"
        log.warning("website_health.scan_website: empty URL")
        return result

    try:
        # Step 1 — SSL check
        ssl_info        = get_ssl_status(url)
        result["ssl"]   = ssl_info

        # Step 2 — Load time (also checks reachability)
        load_time          = get_load_time(url)
        result["load_time"] = load_time

        # Step 3 — Fetch page HTML for content checks
        page = _fetch_page(url)
        result["reachable"] = page["reachable"]

        if page["reachable"] and page["html"]:
            html = page["html"]
            status_code = page["status_code"]

            # Step 4 — Mobile responsiveness
            result["mobile_responsive"] = check_mobile_responsive(html)

            # Step 5 — Contact form
            result["has_contact_form"] = check_contact_form(html)

            # Step 6 — Broken homepage
            result["broken_homepage"] = check_broken_elements(
                html, status_code
            )
        else:
            # Page unreachable — mark as broken
            result["broken_homepage"]   = True
            result["mobile_responsive"] = False
            result["has_contact_form"]  = False
            if page.get("error"):
                result["error"] = page["error"]

        # Step 7 — Calculate final score
        result["health_score"] = calculate_health_score(result)

        log.info(
            "website_health.scan_website: %s — score=%d",
            url, result["health_score"]
        )

    except Exception as exc:
        result["error"] = str(exc)
        log.error(
            "website_health.scan_website error for %s: %s",
            url, exc
        )

    return result


# ─────────────────────────────────────────────────────────────
# DATABASE PERSISTENCE
# ─────────────────────────────────────────────────────────────

def store_scan_results(lead_id: int, results: dict) -> bool:
    """
    Persist website health scan results to
    website_health_scans table.

    Uses INSERT OR REPLACE to handle re-scan runs.
    Returns True on success, False on failure.
    """
    try:
        ssl_info       = results.get("ssl", {})
        has_ssl        = ssl_info.get("has_ssl", False)
        ssl_expiry     = ssl_info.get("expiry_date")
        ssl_days       = ssl_info.get("days_remaining")
        ssl_expiring   = ssl_info.get("is_expiring", False)

        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO website_health_scans
                  (lead_id, url, health_score,
                   has_ssl, ssl_expiry_date, ssl_days_remaining,
                   ssl_is_expiring, load_time_seconds,
                   is_mobile_responsive, has_contact_form,
                   is_broken, is_reachable,
                   scan_error, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    results.get("url", ""),
                    results.get("health_score", 0),
                    int(has_ssl),
                    ssl_expiry,
                    ssl_days,
                    int(ssl_expiring),
                    results.get("load_time", 0.0),
                    int(results.get("mobile_responsive", False)),
                    int(results.get("has_contact_form", False)),
                    int(results.get("broken_homepage", True)),
                    int(results.get("reachable", False)),
                    results.get("error"),
                    results.get("scanned_at",
                                datetime.utcnow().isoformat()),
                )
            )
            conn.commit()
            log.info(
                "website_health: stored scan for lead %s "
                "score=%d",
                lead_id, results.get("health_score", 0)
            )
            return True
        finally:
            conn.close()

    except Exception as exc:
        log.error(
            "website_health.store_scan_results error "
            "lead %s: %s", lead_id, exc
        )
        return False


def scan_and_store(lead_id: int, url: str) -> dict:
    """
    Run full scan and store results in one call.

    Convenience function used by MSP opportunity engine.

    Returns scan results dict.
    health_score will be 0 if scan or storage failed.
    """
    results = scan_website(url)
    if results.get("reachable"):
        store_scan_results(lead_id, results)
    else:
        log.warning(
            "website_health.scan_and_store: "
            "lead %s URL %s not reachable — "
            "storing with score 0",
            lead_id, url
        )
        store_scan_results(lead_id, results)
    return results


def get_poor_health_sites(
    threshold: int = 50,
    limit: int = 50,
) -> list:
    """
    Return leads with website health score below threshold.

    Args:
        threshold: Score cutoff — return scores below this
        limit:     Max rows to return

    Returns list of dicts. Empty list on failure.
    """
    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    l.id,
                    l.name,
                    l.city,
                    l.website,
                    whs.health_score,
                    whs.has_ssl,
                    whs.ssl_is_expiring,
                    whs.load_time_seconds,
                    whs.is_mobile_responsive,
                    whs.has_contact_form,
                    whs.is_broken,
                    whs.scanned_at
                FROM leads l
                JOIN website_health_scans whs
                  ON whs.lead_id = l.id
                WHERE whs.health_score < ?
                ORDER BY whs.health_score ASC
                LIMIT ?
                """,
                (threshold, limit)
            )
            cols = [d[0] for d in cursor.description]
            rows = [
                dict(zip(cols, row))
                for row in cursor.fetchall()
            ]
            log.info(
                "website_health.get_poor_health_sites: "
                "threshold=%d returned %d rows",
                threshold, len(rows)
            )
            return rows
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "website_health.get_poor_health_sites error: %s", exc
        )
        return []


def get_scan_summary() -> dict:
    """
    Return aggregate website health statistics.

    Returns dict with:
      total_scanned, avg_health_score,
      no_ssl_count, slow_count,
      not_mobile_count, broken_count,
      score_critical, score_poor, score_fair, score_good

    Returns safe defaults on failure.
    """
    defaults = {
        "total_scanned":   0,
        "avg_health_score":0.0,
        "no_ssl_count":    0,
        "slow_count":      0,
        "not_mobile_count":0,
        "broken_count":    0,
        "score_critical":  0,   # 0-30
        "score_poor":      0,   # 31-50
        "score_fair":      0,   # 51-70
        "score_good":      0,   # 71-100
    }

    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    AVG(health_score) AS avg_score,
                    SUM(CASE WHEN has_ssl = 0
                             THEN 1 ELSE 0 END) AS no_ssl,
                    SUM(CASE WHEN load_time_seconds > 5.0
                             THEN 1 ELSE 0 END) AS slow,
                    SUM(CASE WHEN is_mobile_responsive = 0
                             THEN 1 ELSE 0 END) AS not_mobile,
                    SUM(CASE WHEN is_broken = 1
                             THEN 1 ELSE 0 END) AS broken,
                    SUM(CASE WHEN health_score <= 30
                             THEN 1 ELSE 0 END) AS critical,
                    SUM(CASE WHEN health_score BETWEEN 31 AND 50
                             THEN 1 ELSE 0 END) AS poor,
                    SUM(CASE WHEN health_score BETWEEN 51 AND 70
                             THEN 1 ELSE 0 END) AS fair,
                    SUM(CASE WHEN health_score > 70
                             THEN 1 ELSE 0 END) AS good
                FROM website_health_scans
                """
            )
            row = cursor.fetchone()
            if row:
                defaults["total_scanned"]    = row[0] or 0
                defaults["avg_health_score"] = round(row[1] or 0.0, 1)
                defaults["no_ssl_count"]     = row[2] or 0
                defaults["slow_count"]       = row[3] or 0
                defaults["not_mobile_count"] = row[4] or 0
                defaults["broken_count"]     = row[5] or 0
                defaults["score_critical"]   = row[6] or 0
                defaults["score_poor"]       = row[7] or 0
                defaults["score_fair"]       = row[8] or 0
                defaults["score_good"]       = row[9] or 0

            log.info(
                "website_health.get_scan_summary: %s", defaults
            )
            return defaults
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "website_health.get_scan_summary error: %s", exc
        )
        return defaults
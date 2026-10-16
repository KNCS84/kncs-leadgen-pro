# tech_stack_detector.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Technology stack detector for MSP vertical
# Detects CMS, analytics, chat tools and tech age from HTML/headers
# Dependencies: database.py, engine/anti_detection.py patterns
# Critical rules observed:
#   - get_connection() via database.py only
#   - Parameterized SQL only
#   - logger not print()
#   - requests.Session for all HTTP calls
#   - Graceful degradation on all failures
#   - No raw exceptions exposed to caller

import json
import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.exceptions import ConnectionError, SSLError, Timeout

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 15  # seconds

# ── CMS DETECTION ────────────────────────────────────────────
CMS_SIGNATURES = {
    "WordPress": [
        "/wp-content/",
        "/wp-includes/",
        "wp-json",
        'generator" content="WordPress',
        "wordpress",
    ],
    "Wix": [
        "wix.com",
        "wixstatic.com",
        "_wix_",
        "X-Wix-Published-Version",
    ],
    "Squarespace": [
        "squarespace.com",
        "squarespace-cdn.com",
        "Static-App: squarespace",
    ],
    "Shopify": [
        "cdn.shopify.com",
        "myshopify.com",
        "Shopify.theme",
        "/shopify/",
    ],
    "Magento": [
        "Mage.Cookies",
        "magento",
        "/skin/frontend/",
        "mage/",
    ],
    "Webflow": [
        "webflow.com",
        'generator" content="Webflow',
    ],
    "Joomla": [
        "/components/com_",
        "joomla",
        'generator" content="Joomla',
    ],
    "Drupal": [
        "Drupal.settings",
        "/sites/default/files/",
        'generator" content="Drupal',
    ],
    "GoDaddy": [
        "godaddy.com",
        "secureserver.net",
        "godaddy-website-builder",
    ],
}

# ── ANALYTICS DETECTION ───────────────────────────────────────
ANALYTICS_SIGNATURES = {
    "Google Analytics": [
        "google-analytics.com/analytics.js",
        "googletagmanager.com/gtag",
        "UA-",
        "G-",
        "gtag('config'",
        "GoogleAnalyticsObject",
    ],
    "Google Tag Manager": [
        "googletagmanager.com/gtm.js",
        "GTM-",
    ],
    "Hotjar": [
        "hotjar.com",
        "hjid:",
        "_hjSettings",
    ],
    "Mixpanel": [
        "mixpanel.com",
        "mixpanel.init",
    ],
    "Heap": [
        "heap.io",
        "heap.load",
    ],
    "Facebook Pixel": [
        "connect.facebook.net/en_US/fbevents.js",
        "fbq('init'",
    ],
    "HubSpot": [
        "js.hs-scripts.com",
        "hubspot.com",
        "hs-analytics",
    ],
}

# ── CHAT TOOL DETECTION ───────────────────────────────────────
CHAT_SIGNATURES = {
    "Intercom": [
        "intercomcdn.com",
        "widget.intercom.io",
        "Intercom(",
    ],
    "Drift": [
        "drift.com",
        "js.driftt.com",
        "drift.load",
    ],
    "Tawk.to": [
        "tawk.to",
        "embed.tawk.to",
    ],
    "Zendesk": [
        "zopim.com",
        "zendesk.com/embeddable_framework",
        "zE(",
    ],
    "Crisp": [
        "crisp.chat",
        "client.crisp.chat",
    ],
    "LiveChat": [
        "livechatinc.com",
        "__lc.license",
    ],
    "Freshchat": [
        "freshchat.com",
        "wchat.freshchat.com",
    ],
}

# ── EMAIL MARKETING DETECTION ─────────────────────────────────
EMAIL_TOOL_SIGNATURES = {
    "Mailchimp": [
        "list-manage.com",
        "mailchimp.com",
        "mc.js",
    ],
    "Klaviyo": [
        "klaviyo.com",
        "a.klaviyo.com",
    ],
    "ActiveCampaign": [
        "activecampaign.com",
        "trackcmp.net",
    ],
    "ConvertKit": [
        "convertkit.com",
        "ck.js",
    ],
    "Drip": [
        "getdrip.com",
    ],
}

# ── TECH AGE INDICATORS ───────────────────────────────────────
# jQuery versions — older = lower tech maturity
JQUERY_PATTERN = re.compile(
    r'jquery[.-](\d+\.\d+\.?\d*)(\.min)?\.js',
    re.IGNORECASE
)

# Bootstrap versions
BOOTSTRAP_PATTERN = re.compile(
    r'bootstrap[.-](\d+\.\d+\.?\d*)(\.min)?\.(?:css|js)',
    re.IGNORECASE
)

# PHP version in server header
PHP_PATTERN = re.compile(r'PHP/(\d+\.\d+)', re.IGNORECASE)

# Tech age score — higher score = older tech = worse maturity
TECH_AGE_SCORES = {
    "jquery_below_2":    30,   # Very old
    "jquery_2x":         20,   # Old
    "jquery_3x":          5,   # Acceptable
    "bootstrap_below_3": 30,   # Very old
    "bootstrap_3x":      15,   # Old
    "bootstrap_4x":       5,   # Acceptable
    "bootstrap_5x":       0,   # Current
    "php_below_7":       30,   # Dangerously old
    "php_7x":            10,   # Old but supported
    "php_8x":             0,   # Current
    "no_jquery":          0,   # No framework detected
}

# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    """Build a requests Session with browser-like headers."""
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
        "Connection":      "keep-alive",
    })
    return session


def _normalise_url(url: str) -> str:
    """Ensure URL has https:// prefix."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _fetch_page(url: str) -> dict:
    """
    Fetch page HTML and response headers.

    Returns dict:
      html:        str | None
      headers:     dict
      status_code: int
      reachable:   bool
      error:       str | None
    """
    result = {
        "html":        None,
        "headers":     {},
        "status_code": 0,
        "reachable":   False,
        "error":       None,
    }

    try:
        session  = _build_session()
        url_norm = _normalise_url(url)
        response = session.get(
            url_norm,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            verify=False,
        )
        result["html"]        = response.text
        result["headers"]     = dict(response.headers)
        result["status_code"] = response.status_code
        result["reachable"]   = True
        log.info(
            "tech_stack: fetched %s status=%d",
            url, response.status_code
        )

    except Timeout:
        result["error"] = "Fetch timed out"
        log.warning("tech_stack: timeout fetching %s", url)

    except SSLError as exc:
        result["error"] = f"SSL error: {exc}"
        log.warning("tech_stack: SSL error fetching %s", url)

    except ConnectionError as exc:
        result["error"] = f"Connection failed: {exc}"
        log.warning("tech_stack: connection error %s", url)

    except Exception as exc:
        result["error"] = str(exc)
        log.error("tech_stack._fetch_page error %s: %s", url, exc)

    return result


def _scan_signatures(
    html: str,
    headers: dict,
    signatures: dict,
) -> list:
    """
    Scan HTML and headers for signature strings.

    Args:
        html:       Page HTML as string
        headers:    Response headers as dict
        signatures: Dict of {tool_name: [sig1, sig2, ...]}

    Returns list of detected tool names.
    """
    detected  = []
    html_lower = html.lower() if html else ""
    headers_str = " ".join(
        f"{k}: {v}" for k, v in headers.items()
    ).lower()

    for tool_name, sigs in signatures.items():
        for sig in sigs:
            if (
                sig.lower() in html_lower
                or sig.lower() in headers_str
            ):
                detected.append(tool_name)
                break  # One match per tool is enough

    return detected


# ─────────────────────────────────────────────────────────────
# INDIVIDUAL DETECTION FUNCTIONS
# ─────────────────────────────────────────────────────────────

def identify_cms(html: str, headers: dict) -> str:
    """
    Identify the CMS from page HTML and response headers.

    Args:
        html:    Raw page HTML string
        headers: HTTP response headers dict

    Returns CMS name string or "Custom / Unknown".
    """
    if not html and not headers:
        return "Unknown"

    html_lower   = html.lower() if html else ""
    headers_str  = " ".join(
        f"{k}: {v}" for k, v in headers.items()
    ).lower()

    for cms_name, sigs in CMS_SIGNATURES.items():
        for sig in sigs:
            if (
                sig.lower() in html_lower
                or sig.lower() in headers_str
            ):
                log.info("tech_stack: CMS detected = %s", cms_name)
                return cms_name

    return "Custom / Unknown"


def identify_analytics(html: str) -> list:
    """
    Identify analytics tools from page HTML.

    Args:
        html: Raw page HTML string

    Returns list of detected analytics tool names.
    """
    if not html:
        return []
    detected = _scan_signatures(html, {}, ANALYTICS_SIGNATURES)
    log.info("tech_stack: analytics detected = %s", detected)
    return detected


def identify_chat_tools(html: str) -> list:
    """
    Identify chat/support tools from page HTML.

    Args:
        html: Raw page HTML string

    Returns list of detected chat tool names.
    """
    if not html:
        return []
    detected = _scan_signatures(html, {}, CHAT_SIGNATURES)
    log.info("tech_stack: chat tools detected = %s", detected)
    return detected


def identify_email_tools(html: str) -> list:
    """
    Identify email marketing tools from page HTML.

    Args:
        html: Raw page HTML string

    Returns list of detected email tool names.
    """
    if not html:
        return []
    detected = _scan_signatures(html, {}, EMAIL_TOOL_SIGNATURES)
    log.info("tech_stack: email tools detected = %s", detected)
    return detected


def assess_tech_age(
    html: str,
    headers: dict,
) -> dict:
    """
    Assess technology age from jQuery, Bootstrap versions
    and server headers.

    Args:
        html:    Raw page HTML string
        headers: HTTP response headers dict

    Returns dict:
      jquery_version:    str | None
      bootstrap_version: str | None
      php_version:       str | None
      age_score:         int  (higher = older tech)
      age_label:         str
    """
    result = {
        "jquery_version":    None,
        "bootstrap_version": None,
        "php_version":       None,
        "age_score":         0,
        "age_label":         "Unknown",
    }

    html_str    = html or ""
    headers_str = " ".join(
        f"{k}: {v}" for k, v in headers.items()
    )

    age_score = 0

    # jQuery version detection
    jquery_match = JQUERY_PATTERN.search(html_str)
    if jquery_match:
        version = jquery_match.group(1)
        result["jquery_version"] = version
        major = int(version.split(".")[0])
        if major < 2:
            age_score += TECH_AGE_SCORES["jquery_below_2"]
        elif major == 2:
            age_score += TECH_AGE_SCORES["jquery_2x"]
        elif major >= 3:
            age_score += TECH_AGE_SCORES["jquery_3x"]

    # Bootstrap version detection
    bootstrap_match = BOOTSTRAP_PATTERN.search(html_str)
    if bootstrap_match:
        version = bootstrap_match.group(1)
        result["bootstrap_version"] = version
        major = int(version.split(".")[0])
        if major < 3:
            age_score += TECH_AGE_SCORES["bootstrap_below_3"]
        elif major == 3:
            age_score += TECH_AGE_SCORES["bootstrap_3x"]
        elif major == 4:
            age_score += TECH_AGE_SCORES["bootstrap_4x"]
        elif major >= 5:
            age_score += TECH_AGE_SCORES["bootstrap_5x"]

    # PHP version from server header
    php_match = PHP_PATTERN.search(headers_str)
    if php_match:
        version = php_match.group(1)
        result["php_version"] = version
        major = int(version.split(".")[0])
        if major < 7:
            age_score += TECH_AGE_SCORES["php_below_7"]
        elif major == 7:
            age_score += TECH_AGE_SCORES["php_7x"]
        elif major >= 8:
            age_score += TECH_AGE_SCORES["php_8x"]

    result["age_score"] = age_score

    # Age label
    if age_score >= 50:
        result["age_label"] = "Very Outdated"
    elif age_score >= 30:
        result["age_label"] = "Outdated"
    elif age_score >= 10:
        result["age_label"] = "Ageing"
    else:
        result["age_label"] = "Modern"

    log.info(
        "tech_stack.assess_tech_age: score=%d label=%s",
        age_score, result["age_label"]
    )
    return result


# ─────────────────────────────────────────────────────────────
# MAIN DETECTION FUNCTION
# ─────────────────────────────────────────────────────────────

def detect_tech_stack(url: str) -> dict:
    """
    Run full technology stack detection for a URL.

    Performs:
      1. Fetch page HTML and headers
      2. Identify CMS
      3. Identify analytics tools
      4. Identify chat tools
      5. Identify email marketing tools
      6. Assess technology age

    Returns dict:
      url, cms, analytics, chat_tools, email_tools,
      tech_age, reachable, detected_at, error

    Always returns a dict — never raises.
    """
    log.info("tech_stack.detect_tech_stack: scanning %s", url)

    result = {
        "url":          url,
        "cms":          "Unknown",
        "analytics":    [],
        "chat_tools":   [],
        "email_tools":  [],
        "tech_age":     {},
        "reachable":    False,
        "detected_at":  datetime.utcnow().isoformat(),
        "error":        None,
    }

    if not url or not url.strip():
        result["error"] = "No URL provided"
        log.warning("tech_stack.detect_tech_stack: empty URL")
        return result

    try:
        page = _fetch_page(url)
        result["reachable"] = page["reachable"]

        if not page["reachable"]:
            result["error"] = page.get("error", "Page not reachable")
            log.warning(
                "tech_stack: %s not reachable", url
            )
            return result

        html    = page["html"] or ""
        headers = page["headers"]

        # Run all detections
        result["cms"]         = identify_cms(html, headers)
        result["analytics"]   = identify_analytics(html)
        result["chat_tools"]  = identify_chat_tools(html)
        result["email_tools"] = identify_email_tools(html)
        result["tech_age"]    = assess_tech_age(html, headers)

        log.info(
            "tech_stack.detect_tech_stack: %s — "
            "cms=%s analytics=%d chat=%d",
            url,
            result["cms"],
            len(result["analytics"]),
            len(result["chat_tools"]),
        )

    except Exception as exc:
        result["error"] = str(exc)
        log.error(
            "tech_stack.detect_tech_stack error %s: %s",
            url, exc
        )

    return result


# ─────────────────────────────────────────────────────────────
# DATABASE PERSISTENCE
# ─────────────────────────────────────────────────────────────

def store_tech_profile(lead_id: int, stack: dict) -> bool:
    """
    Persist technology stack profile to technology_profiles table.

    Uses INSERT OR REPLACE to handle re-detection runs.
    Returns True on success, False on failure.
    """
    try:
        tech_age    = stack.get("tech_age", {})
        analytics   = stack.get("analytics", [])
        chat_tools  = stack.get("chat_tools", [])
        email_tools = stack.get("email_tools", [])

        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO technology_profiles
                  (lead_id, url, cms,
                   analytics_tools, chat_tools, email_tools,
                   jquery_version, bootstrap_version,
                   php_version, tech_age_score, tech_age_label,
                   is_reachable, scan_error, detected_at)
                VALUES
                  (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    stack.get("url", ""),
                    stack.get("cms", "Unknown"),
                    json.dumps(analytics),
                    json.dumps(chat_tools),
                    json.dumps(email_tools),
                    tech_age.get("jquery_version"),
                    tech_age.get("bootstrap_version"),
                    tech_age.get("php_version"),
                    tech_age.get("age_score", 0),
                    tech_age.get("age_label", "Unknown"),
                    int(stack.get("reachable", False)),
                    stack.get("error"),
                    stack.get(
                        "detected_at",
                        datetime.utcnow().isoformat()
                    ),
                )
            )
            conn.commit()
            log.info(
                "tech_stack: stored profile for lead %s cms=%s",
                lead_id, stack.get("cms", "Unknown")
            )
            return True
        finally:
            conn.close()

    except Exception as exc:
        log.error(
            "tech_stack.store_tech_profile error "
            "lead %s: %s", lead_id, exc
        )
        return False


def detect_and_store(lead_id: int, url: str) -> dict:
    """
    Run full detection and store results in one call.

    Convenience function used by MSP opportunity engine.
    Returns stack detection results dict.
    """
    stack = detect_tech_stack(url)
    store_tech_profile(lead_id, stack)
    return stack


def get_outdated_tech_leads(
    min_age_score: int = 30,
    limit: int = 50,
) -> list:
    """
    Return leads with outdated technology stacks.

    Args:
        min_age_score: Minimum tech age score to include
        limit:         Max rows to return

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
                    tp.cms,
                    tp.tech_age_score,
                    tp.tech_age_label,
                    tp.jquery_version,
                    tp.bootstrap_version,
                    tp.php_version,
                    tp.analytics_tools,
                    tp.chat_tools,
                    tp.detected_at
                FROM leads l
                JOIN technology_profiles tp
                  ON tp.lead_id = l.id
                WHERE tp.tech_age_score >= ?
                ORDER BY tp.tech_age_score DESC
                LIMIT ?
                """,
                (min_age_score, limit)
            )
            cols = [d[0] for d in cursor.description]
            rows = [
                dict(zip(cols, row))
                for row in cursor.fetchall()
            ]
            log.info(
                "tech_stack.get_outdated_tech_leads: "
                "min_age=%d returned %d rows",
                min_age_score, len(rows)
            )
            return rows
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "tech_stack.get_outdated_tech_leads error: %s", exc
        )
        return []


def get_tech_summary() -> dict:
    """
    Return aggregate technology stack statistics.

    Returns dict with:
      total_scanned, cms_breakdown,
      avg_age_score, outdated_count,
      no_analytics_count, no_chat_count

    Returns safe defaults on failure.
    """
    defaults = {
        "total_scanned":    0,
        "avg_age_score":    0.0,
        "outdated_count":   0,
        "no_analytics_count": 0,
        "no_chat_count":    0,
        "wordpress_count":  0,
        "wix_count":        0,
        "custom_count":     0,
    }

    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    AVG(tech_age_score) AS avg_age,
                    SUM(CASE WHEN tech_age_score >= 30
                             THEN 1 ELSE 0 END) AS outdated,
                    SUM(CASE WHEN analytics_tools = '[]'
                             OR analytics_tools IS NULL
                             THEN 1 ELSE 0 END) AS no_analytics,
                    SUM(CASE WHEN chat_tools = '[]'
                             OR chat_tools IS NULL
                             THEN 1 ELSE 0 END) AS no_chat,
                    SUM(CASE WHEN cms = 'WordPress'
                             THEN 1 ELSE 0 END) AS wordpress,
                    SUM(CASE WHEN cms = 'Wix'
                             THEN 1 ELSE 0 END) AS wix,
                    SUM(CASE WHEN cms = 'Custom / Unknown'
                             THEN 1 ELSE 0 END) AS custom
                FROM technology_profiles
                """
            )
            row = cursor.fetchone()
            if row:
                defaults["total_scanned"]      = row[0] or 0
                defaults["avg_age_score"]       = round(
                    row[1] or 0.0, 1
                )
                defaults["outdated_count"]      = row[2] or 0
                defaults["no_analytics_count"]  = row[3] or 0
                defaults["no_chat_count"]       = row[4] or 0
                defaults["wordpress_count"]     = row[5] or 0
                defaults["wix_count"]           = row[6] or 0
                defaults["custom_count"]        = row[7] or 0

            log.info(
                "tech_stack.get_tech_summary: %s", defaults
            )
            return defaults
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "tech_stack.get_tech_summary error: %s", exc
        )
        return defaults
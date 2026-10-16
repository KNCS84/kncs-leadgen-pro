# cyber_risk_analyzer.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Passive cyber risk detection for MSP vertical
# Detects publicly visible security risks only
# NO penetration testing of any kind
# Dependencies: database.py, dnspython
# Critical rules observed:
#   - get_connection() via database.py only
#   - Parameterized SQL only
#   - logger not print()
#   - requests.Session for all HTTP calls
#   - Graceful degradation on all failures
#   - No raw exceptions exposed to caller
#   - Passive reconnaissance only

import logging
import socket
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.exceptions import ConnectionError, SSLError, Timeout

try:
    import dns.resolver
    import dns.exception
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "cyber_risk: dnspython not available — "
        "DNS checks will be skipped"
    )

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 10  # seconds

# Security headers to check
SECURITY_HEADERS = {
    "Strict-Transport-Security": {
        "key":      "hsts",
        "severity": "high",
        "label":    "HSTS Missing",
    },
    "Content-Security-Policy": {
        "key":      "csp",
        "severity": "medium",
        "label":    "CSP Missing",
    },
    "X-Frame-Options": {
        "key":      "x_frame_options",
        "severity": "medium",
        "label":    "X-Frame-Options Missing",
    },
    "X-Content-Type-Options": {
        "key":      "x_content_type",
        "severity": "low",
        "label":    "X-Content-Type-Options Missing",
    },
}

# Risk score values by severity
RISK_SCORE_MAP = {
    "critical": 30,
    "high":     20,
    "medium":   10,
    "low":       5,
}

# WordPress admin path — exposed admin panel = critical
WP_ADMIN_PATHS = [
    "/wp-admin/",
    "/wp-login.php",
]

# Server headers that reveal technology info
REVEALING_SERVER_HEADERS = [
    "Server",
    "X-Powered-By",
    "X-AspNet-Version",
    "X-AspNetMvc-Version",
]

# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    """
    Build a requests Session with browser-like headers.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
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


def _extract_domain(url: str) -> str:
    """
    Extract bare domain from URL.
    Returns empty string on failure.
    """
    try:
        parsed = urlparse(_normalise_url(url))
        domain = parsed.netloc.lower()
        # Remove port if present
        domain = domain.split(":")[0]
        # Remove www prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _dns_lookup(domain: str, record_type: str) -> list:
    """
    Perform DNS lookup for a given record type.

    Args:
        domain:      Domain to query
        record_type: TXT, MX, DMARC etc.

    Returns list of record strings.
    Returns empty list if DNS unavailable or lookup fails.
    """
    if not DNS_AVAILABLE:
        return []

    try:
        answers = dns.resolver.resolve(domain, record_type, lifetime=5)
        return [str(r) for r in answers]
    except dns.exception.DNSException:
        return []
    except Exception as exc:
        log.warning(
            "cyber_risk._dns_lookup: %s %s error: %s",
            domain, record_type, exc
        )
        return []


# ─────────────────────────────────────────────────────────────
# INDIVIDUAL CHECK FUNCTIONS
# ─────────────────────────────────────────────────────────────

def check_security_headers(url: str) -> dict:
    """
    Check HTTP response headers for missing security headers.

    Checks:
      Strict-Transport-Security (HSTS)
      Content-Security-Policy (CSP)
      X-Frame-Options
      X-Content-Type-Options
      Server header (information leakage)

    Returns dict with one key per header checked:
      hsts, csp, x_frame_options, x_content_type,
      server_info, raw_headers

    Each header key maps to dict:
      present:   bool
      value:     str | None
      severity:  str
      label:     str
    """
    result = {
        "hsts":            {"present": False, "value": None,
                            "severity": "high",
                            "label": "HSTS Missing"},
        "csp":             {"present": False, "value": None,
                            "severity": "medium",
                            "label": "CSP Missing"},
        "x_frame_options": {"present": False, "value": None,
                            "severity": "medium",
                            "label": "X-Frame-Options Missing"},
        "x_content_type":  {"present": False, "value": None,
                            "severity": "low",
                            "label": "X-Content-Type-Options Missing"},
        "server_info":     {"present": False, "value": None,
                            "severity": "low",
                            "label": "Server Info Exposed"},
        "raw_headers":     {},
        "error":           None,
    }

    try:
        session  = _build_session()
        url_norm = _normalise_url(url)
        response = session.get(
            url_norm,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            verify=False,  # We check SSL separately
        )

        headers      = response.headers
        headers_lower = {k.lower(): v for k, v in headers.items()}
        result["raw_headers"] = dict(headers)

        # Check each security header
        for header_name, config in SECURITY_HEADERS.items():
            key   = config["key"]
            found = headers.get(header_name) or headers.get(
                header_name.lower()
            )
            if found:
                result[key]["present"] = True
                result[key]["value"]   = found
                result[key]["label"]   = f"{header_name} Present"
            else:
                result[key]["present"]  = False
                result[key]["severity"] = config["severity"]
                result[key]["label"]    = config["label"]

        # Check for server info leakage
        for reveal_header in REVEALING_SERVER_HEADERS:
            val = headers.get(reveal_header)
            if val:
                result["server_info"]["present"] = True
                result["server_info"]["value"]   = val
                break

        log.info(
            "cyber_risk.check_security_headers: %s — "
            "hsts=%s csp=%s",
            url,
            result["hsts"]["present"],
            result["csp"]["present"],
        )

    except SSLError:
        # SSL error means no HTTPS — already captured in ssl check
        result["error"] = "SSL error — HTTPS not available"
        log.warning(
            "cyber_risk.check_security_headers: SSL error %s", url
        )

    except Timeout:
        result["error"] = "Request timed out"
        log.warning(
            "cyber_risk.check_security_headers: timeout %s", url
        )

    except ConnectionError as exc:
        result["error"] = f"Connection failed: {exc}"
        log.warning(
            "cyber_risk.check_security_headers: "
            "connection error %s: %s", url, exc
        )

    except Exception as exc:
        result["error"] = str(exc)
        log.error(
            "cyber_risk.check_security_headers error %s: %s",
            url, exc
        )

    return result


def check_dns_records(domain: str) -> dict:
    """
    Check DNS records for email security configuration.

    Checks:
      SPF record   (TXT record containing v=spf1)
      DMARC record (TXT record at _dmarc.domain)
      DKIM         (TXT record at common._domainkey.domain)
      MX records   (email server present)

    Returns dict:
      spf_present:     bool
      spf_record:      str | None
      dmarc_present:   bool
      dmarc_record:    str | None
      dkim_present:    bool
      mx_present:      bool
      dns_available:   bool
    """
    result = {
        "spf_present":   False,
        "spf_record":    None,
        "dmarc_present": False,
        "dmarc_record":  None,
        "dkim_present":  False,
        "mx_present":    False,
        "dns_available": DNS_AVAILABLE,
    }

    if not DNS_AVAILABLE:
        log.warning(
            "cyber_risk.check_dns_records: "
            "dnspython not available for %s", domain
        )
        return result

    if not domain:
        log.warning("cyber_risk.check_dns_records: empty domain")
        return result

    try:
        # SPF check — look for v=spf1 in TXT records
        txt_records = _dns_lookup(domain, "TXT")
        for record in txt_records:
            if "v=spf1" in record.lower():
                result["spf_present"] = True
                result["spf_record"]  = record
                break

        # DMARC check — _dmarc subdomain
        dmarc_domain   = f"_dmarc.{domain}"
        dmarc_records  = _dns_lookup(dmarc_domain, "TXT")
        for record in dmarc_records:
            if "v=dmarc1" in record.lower():
                result["dmarc_present"] = True
                result["dmarc_record"]  = record
                break

        # DKIM check — common selector names
        dkim_selectors = [
            "default", "google", "mail", "k1",
            "dkim", "selector1", "selector2",
        ]
        for selector in dkim_selectors:
            dkim_domain  = f"{selector}._domainkey.{domain}"
            dkim_records = _dns_lookup(dkim_domain, "TXT")
            if dkim_records:
                result["dkim_present"] = True
                break

        # MX check
        mx_records = _dns_lookup(domain, "MX")
        result["mx_present"] = len(mx_records) > 0

        log.info(
            "cyber_risk.check_dns_records: %s — "
            "spf=%s dmarc=%s dkim=%s mx=%s",
            domain,
            result["spf_present"],
            result["dmarc_present"],
            result["dkim_present"],
            result["mx_present"],
        )

    except Exception as exc:
        log.error(
            "cyber_risk.check_dns_records error %s: %s",
            domain, exc
        )

    return result


def check_ssl_expiry(url: str) -> dict:
    """
    Check if HTTPS is present and certificate is valid.

    Returns dict:
      has_https:     bool
      cert_valid:    bool
      error:         str | None
    """
    result = {
        "has_https":  False,
        "cert_valid": False,
        "error":      None,
    }

    try:
        session  = _build_session()
        url_norm = _normalise_url(url)

        if not url_norm.startswith("https://"):
            result["error"] = "URL does not use HTTPS"
            return result

        # verify=True means SSL must be valid
        response = session.get(
            url_norm,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            verify=True,
        )
        result["has_https"]  = True
        result["cert_valid"] = True

    except SSLError as exc:
        result["has_https"]  = True   # HTTPS attempted
        result["cert_valid"] = False  # But cert invalid
        result["error"]      = f"SSL certificate invalid: {exc}"
        log.warning(
            "cyber_risk.check_ssl_expiry: invalid cert %s", url
        )

    except (ConnectionError, Timeout) as exc:
        result["error"] = str(exc)
        log.warning(
            "cyber_risk.check_ssl_expiry: connection error %s: %s",
            url, exc
        )

    except Exception as exc:
        result["error"] = str(exc)
        log.error(
            "cyber_risk.check_ssl_expiry error %s: %s", url, exc
        )

    return result


def _check_exposed_admin(url: str) -> bool:
    """
    Check if WordPress admin panel is publicly accessible.

    Returns True if admin login page is exposed.
    Passive check only — reads page, does not attempt login.
    """
    try:
        session  = _build_session()
        base_url = _normalise_url(url).rstrip("/")

        for path in WP_ADMIN_PATHS:
            try:
                check_url = base_url + path
                response  = session.get(
                    check_url,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                    verify=False,
                )
                html_lower = response.text.lower()
                # WordPress login page has these markers
                if (
                    response.status_code == 200
                    and (
                        "wordpress" in html_lower
                        or "wp-login" in html_lower
                        or "log in" in html_lower
                    )
                ):
                    log.info(
                        "cyber_risk: exposed admin panel "
                        "detected at %s", check_url
                    )
                    return True
            except Exception:
                continue

        return False

    except Exception as exc:
        log.warning(
            "cyber_risk._check_exposed_admin error %s: %s",
            url, exc
        )
        return False


# ─────────────────────────────────────────────────────────────
# RISK SCORE CALCULATION
# ─────────────────────────────────────────────────────────────

def calculate_risk_score(analysis: dict) -> int:
    """
    Calculate cyber risk score from analysis results.

    Higher score = higher risk = better MSP opportunity.

    Risk contributions:
      No HTTPS                critical  +30
      Missing HSTS            high      +20
      Missing CSP             medium    +10
      No SPF record           high      +20
      No DMARC record         high      +20
      No DKIM record          medium    +10
      Exposed admin panel     critical  +30
      Missing X-Frame-Options medium    +10

    Returns int score. No upper clamp — raw risk sum.
    Caller can normalise to 0-100 if needed.
    """
    score  = 0
    issues = []

    ssl_check = analysis.get("ssl", {})
    if not ssl_check.get("has_https", True):
        score += RISK_SCORE_MAP["critical"]
        issues.append("No HTTPS")

    headers = analysis.get("headers", {})

    if not headers.get("hsts", {}).get("present", True):
        score += RISK_SCORE_MAP["high"]
        issues.append("HSTS missing")

    if not headers.get("csp", {}).get("present", True):
        score += RISK_SCORE_MAP["medium"]
        issues.append("CSP missing")

    if not headers.get("x_frame_options", {}).get("present", True):
        score += RISK_SCORE_MAP["medium"]
        issues.append("X-Frame-Options missing")

    dns = analysis.get("dns", {})

    if not dns.get("spf_present", True):
        score += RISK_SCORE_MAP["high"]
        issues.append("SPF record missing")

    if not dns.get("dmarc_present", True):
        score += RISK_SCORE_MAP["high"]
        issues.append("DMARC record missing")

    if not dns.get("dkim_present", True):
        score += RISK_SCORE_MAP["medium"]
        issues.append("DKIM record missing")

    if analysis.get("exposed_admin", False):
        score += RISK_SCORE_MAP["critical"]
        issues.append("Admin panel exposed")

    log.info(
        "cyber_risk.calculate_risk_score: score=%d issues=%s",
        score, issues
    )
    return score


def _get_risk_severity(score: int) -> str:
    """Map risk score to severity label."""
    if score >= 60:
        return "critical"
    elif score >= 40:
        return "high"
    elif score >= 20:
        return "medium"
    return "low"


def _get_top_issues(analysis: dict) -> list:
    """
    Extract top risk issues from analysis as a list of strings.
    Returns up to 5 issues ordered by severity.
    """
    issues = []

    ssl_check = analysis.get("ssl", {})
    if not ssl_check.get("has_https", True):
        issues.append("No HTTPS — data transmitted in plain text")

    headers = analysis.get("headers", {})
    if not headers.get("hsts", {}).get("present", True):
        issues.append("HSTS missing — vulnerable to SSL stripping")

    if not headers.get("csp", {}).get("present", True):
        issues.append(
            "No Content Security Policy — XSS attack risk"
        )

    if not headers.get("x_frame_options", {}).get("present", True):
        issues.append(
            "X-Frame-Options missing — clickjacking risk"
        )

    dns = analysis.get("dns", {})
    if not dns.get("spf_present", True):
        issues.append(
            "SPF record missing — domain can be spoofed for phishing"
        )

    if not dns.get("dmarc_present", True):
        issues.append(
            "DMARC missing — no email authentication policy"
        )

    if not dns.get("dkim_present", True):
        issues.append("DKIM missing — emails not cryptographically signed")

    if analysis.get("exposed_admin", False):
        issues.insert(0, "WordPress admin panel publicly accessible")

    return issues[:5]


# ─────────────────────────────────────────────────────────────
# MAIN ANALYSIS FUNCTION
# ─────────────────────────────────────────────────────────────

def analyze_cyber_risk(url: str, domain: str = "") -> dict:
    """
    Run full passive cyber risk analysis for a URL and domain.

    Performs:
      1. SSL/HTTPS check
      2. Security headers check
      3. DNS record checks (SPF, DMARC, DKIM)
      4. Exposed admin panel check
      5. Risk score calculation

    Args:
        url:    Full URL to scan (e.g. https://example.com)
        domain: Bare domain for DNS checks.
                If empty, extracted from url.

    Returns dict:
      url, domain, risk_score, severity,
      ssl, headers, dns, exposed_admin,
      top_issues, analyzed_at, error

    Always returns a dict — never raises.
    """
    if not domain:
        domain = _extract_domain(url)

    log.info(
        "cyber_risk.analyze_cyber_risk: url=%s domain=%s",
        url, domain
    )

    result = {
        "url":          url,
        "domain":       domain,
        "risk_score":   0,
        "severity":     "low",
        "ssl":          {},
        "headers":      {},
        "dns":          {},
        "exposed_admin":False,
        "top_issues":   [],
        "analyzed_at":  datetime.utcnow().isoformat(),
        "error":        None,
    }

    if not url or not url.strip():
        result["error"] = "No URL provided"
        log.warning("cyber_risk.analyze_cyber_risk: empty URL")
        return result

    try:
        # Step 1 — SSL check
        result["ssl"] = check_ssl_expiry(url)

        # Step 2 — Security headers
        result["headers"] = check_security_headers(url)

        # Step 3 — DNS records
        if domain:
            result["dns"] = check_dns_records(domain)
        else:
            log.warning(
                "cyber_risk: could not extract domain from %s", url
            )

        # Step 4 — Exposed admin check
        result["exposed_admin"] = _check_exposed_admin(url)

        # Step 5 — Risk score
        result["risk_score"] = calculate_risk_score(result)
        result["severity"]   = _get_risk_severity(
            result["risk_score"]
        )

        # Step 6 — Top issues list
        result["top_issues"] = _get_top_issues(result)

        log.info(
            "cyber_risk.analyze_cyber_risk: %s — "
            "score=%d severity=%s",
            url,
            result["risk_score"],
            result["severity"],
        )

    except Exception as exc:
        result["error"] = str(exc)
        log.error(
            "cyber_risk.analyze_cyber_risk error %s: %s",
            url, exc
        )

    return result


# ─────────────────────────────────────────────────────────────
# DATABASE PERSISTENCE
# ─────────────────────────────────────────────────────────────

def store_risk_analysis(lead_id: int, results: dict) -> bool:
    """
    Persist cyber risk analysis to cyber_risk_profiles table.

    Uses INSERT OR REPLACE to handle re-analysis runs.
    Returns True on success, False on failure.
    """
    try:
        ssl_info  = results.get("ssl", {})
        headers   = results.get("headers", {})
        dns       = results.get("dns", {})
        issues    = results.get("top_issues", [])

        import json
        issues_json = json.dumps(issues)

        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO cyber_risk_profiles
                  (lead_id, url, domain, risk_score, severity,
                   has_https, hsts_present, csp_present,
                   x_frame_options_present,
                   spf_present, dmarc_present, dkim_present,
                   exposed_admin, top_issues,
                   scan_error, analyzed_at)
                VALUES
                  (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?)
                """,
                (
                    lead_id,
                    results.get("url", ""),
                    results.get("domain", ""),
                    results.get("risk_score", 0),
                    results.get("severity", "low"),
                    int(ssl_info.get("has_https", False)),
                    int(headers.get("hsts", {})
                               .get("present", False)),
                    int(headers.get("csp", {})
                               .get("present", False)),
                    int(headers.get("x_frame_options", {})
                               .get("present", False)),
                    int(dns.get("spf_present", False)),
                    int(dns.get("dmarc_present", False)),
                    int(dns.get("dkim_present", False)),
                    int(results.get("exposed_admin", False)),
                    issues_json,
                    results.get("error"),
                    results.get(
                        "analyzed_at",
                        datetime.utcnow().isoformat()
                    ),
                )
            )
            conn.commit()
            log.info(
                "cyber_risk: stored analysis for lead %s "
                "score=%d",
                lead_id, results.get("risk_score", 0)
            )
            return True
        finally:
            conn.close()

    except Exception as exc:
        log.error(
            "cyber_risk.store_risk_analysis error "
            "lead %s: %s", lead_id, exc
        )
        return False


def analyze_and_store(
    lead_id: int,
    url: str,
    domain: str = "",
) -> dict:
    """
    Run full analysis and store results in one call.

    Convenience function used by MSP opportunity engine.
    Returns analysis results dict.
    """
    results = analyze_cyber_risk(url, domain)
    store_risk_analysis(lead_id, results)
    return results


def get_high_risk_leads(
    min_score: int = 40,
    limit: int = 50,
) -> list:
    """
    Return leads with cyber risk score above min_score.

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
                    crp.risk_score,
                    crp.severity,
                    crp.has_https,
                    crp.hsts_present,
                    crp.csp_present,
                    crp.spf_present,
                    crp.dmarc_present,
                    crp.exposed_admin,
                    crp.top_issues,
                    crp.analyzed_at
                FROM leads l
                JOIN cyber_risk_profiles crp
                  ON crp.lead_id = l.id
                WHERE crp.risk_score >= ?
                ORDER BY crp.risk_score DESC
                LIMIT ?
                """,
                (min_score, limit)
            )
            cols = [d[0] for d in cursor.description]
            rows = [
                dict(zip(cols, row))
                for row in cursor.fetchall()
            ]
            log.info(
                "cyber_risk.get_high_risk_leads: "
                "min_score=%d returned %d rows",
                min_score, len(rows)
            )
            return rows
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "cyber_risk.get_high_risk_leads error: %s", exc
        )
        return []


def get_risk_summary() -> dict:
    """
    Return aggregate cyber risk statistics.

    Returns dict with:
      total_analyzed, avg_risk_score,
      critical_count, high_count,
      medium_count, low_count,
      no_https_count, no_spf_count,
      no_dmarc_count, exposed_admin_count

    Returns safe defaults on failure.
    """
    defaults = {
        "total_analyzed":    0,
        "avg_risk_score":    0.0,
        "critical_count":    0,
        "high_count":        0,
        "medium_count":      0,
        "low_count":         0,
        "no_https_count":    0,
        "no_spf_count":      0,
        "no_dmarc_count":    0,
        "exposed_admin_count": 0,
    }

    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    AVG(risk_score) AS avg_score,
                    SUM(CASE WHEN severity = 'critical'
                             THEN 1 ELSE 0 END) AS critical,
                    SUM(CASE WHEN severity = 'high'
                             THEN 1 ELSE 0 END) AS high,
                    SUM(CASE WHEN severity = 'medium'
                             THEN 1 ELSE 0 END) AS medium,
                    SUM(CASE WHEN severity = 'low'
                             THEN 1 ELSE 0 END) AS low,
                    SUM(CASE WHEN has_https = 0
                             THEN 1 ELSE 0 END) AS no_https,
                    SUM(CASE WHEN spf_present = 0
                             THEN 1 ELSE 0 END) AS no_spf,
                    SUM(CASE WHEN dmarc_present = 0
                             THEN 1 ELSE 0 END) AS no_dmarc,
                    SUM(CASE WHEN exposed_admin = 1
                             THEN 1 ELSE 0 END) AS exposed
                FROM cyber_risk_profiles
                """
            )
            row = cursor.fetchone()
            if row:
                defaults["total_analyzed"]     = row[0] or 0
                defaults["avg_risk_score"]     = round(
                    row[1] or 0.0, 1
                )
                defaults["critical_count"]     = row[2] or 0
                defaults["high_count"]         = row[3] or 0
                defaults["medium_count"]       = row[4] or 0
                defaults["low_count"]          = row[5] or 0
                defaults["no_https_count"]     = row[6] or 0
                defaults["no_spf_count"]       = row[7] or 0
                defaults["no_dmarc_count"]     = row[8] or 0
                defaults["exposed_admin_count"]= row[9] or 0

            log.info(
                "cyber_risk.get_risk_summary: %s", defaults
            )
            return defaults
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "cyber_risk.get_risk_summary error: %s", exc
        )
        return defaults
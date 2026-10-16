# engine/website_auditor.py
# KNCS LeadGen Pro — Website Auditor
# v3.1 — Fixed audit_lead_website() to merge results back onto lead dict
#
# Exports (all required by outreach_dashboard.py):
#   WebsiteAuditor         — class with audit(), audit_website(), bulk_audit(), get_contact_info()
#   audit_website()        — module-level convenience
#   audit_lead_website()   — takes a lead dict, audits lead["website"]
#   get_website_score()    — returns 0-100 int
#   is_available()         — returns True (full implementation active)
#   INDUSTRY_PROFILES      — dict of industry profile configs
#   get_profile()          — returns profile dict for a lead
#   _detect_industry()     — detects industry string from lead dict
#
# BUG-EO-01: FIXED — all exports present
# BUG-EO-03: FIXED — audit_lead_website() now merges results onto lead dict
#                     with correct website_* key names
# No Playwright needed — requests + bs4 only

from __future__ import annotations

import logging
import re
import socket
import ssl
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT   = 10          # seconds per request
_DEFAULT_HEADERS   = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Regex patterns
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_PHONE_RE = re.compile(
    r"(?:(?:\+44|0044|0)\s*(?:\d[\s\-]?){9,10})"  # UK
    r"|(?:\+\d{1,3}[\s\-]?\d{6,14})"               # International
)


# ---------------------------------------------------------------------------
# INDUSTRY_PROFILES
# ---------------------------------------------------------------------------

INDUSTRY_PROFILES: dict[str, dict] = {

    "care_home": {
        "name":         "Care Home",
        "keywords":     ["care", "nursing", "residential", "elderly",
                         "dementia", "care home", "assisted living"],
        "checks":       ["ssl", "mobile", "contact_page",
                         "load_speed", "email_found", "cqc_mention"],
        "pain_points":  [
            "CQC compliance visibility",
            "staff recruitment",
            "occupancy rates",
            "family reassurance content",
        ],
        "email_hook":   "improve CQC online presence and family trust",
        "score_weights": {
            "reachable":     20,
            "ssl":           15,
            "mobile":        15,
            "contact_page":  20,
            "load_speed":    15,
            "email_found":   10,
            "cqc_mention":    5,
        },
    },

    "dental": {
        "name":         "Dental Practice",
        "keywords":     ["dental", "dentist", "orthodon", "implant",
                         "cosmetic teeth", "smile", "oral"],
        "checks":       ["ssl", "mobile", "contact_page",
                         "booking", "load_speed", "email_found"],
        "pain_points":  [
            "online patient acquisition",
            "booking system conversion",
            "Google reviews",
            "NHS vs private patient mix",
        ],
        "email_hook":   "attract more private patients online",
        "score_weights": {
            "reachable":    20,
            "ssl":          15,
            "mobile":       15,
            "contact_page": 15,
            "booking":      20,
            "email_found":  10,
            "load_speed":    5,
        },
    },

    "msp": {
        "name":         "IT / MSP",
        "keywords":     ["msp", "managed service", "it support",
                         "cybersecurity", "cloud", "helpdesk", "soc"],
        "checks":       ["ssl", "mobile", "contact_page",
                         "load_speed", "email_found", "https_redirect"],
        "pain_points":  [
            "lead generation",
            "client retention",
            "differentiating from competitors",
            "automation",
        ],
        "email_hook":   "generate more qualified B2B leads",
        "score_weights": {
            "reachable":        20,
            "ssl":              20,
            "mobile":           15,
            "contact_page":     15,
            "load_speed":       15,
            "email_found":      10,
            "https_redirect":    5,
        },
    },

    "restaurant": {
        "name":         "Restaurant / Hospitality",
        "keywords":     ["restaurant", "cafe", "bistro", "pub",
                         "hospitality", "takeaway", "eatery", "diner"],
        "checks":       ["ssl", "mobile", "contact_page",
                         "booking", "load_speed", "email_found"],
        "pain_points":  [
            "table booking conversion",
            "Google Maps visibility",
            "online menu UX",
            "reviews",
        ],
        "email_hook":   "increase covers and online bookings",
        "score_weights": {
            "reachable":    20,
            "ssl":          10,
            "mobile":       20,
            "contact_page": 15,
            "booking":      20,
            "email_found":  10,
            "load_speed":    5,
        },
    },

    "solicitor": {
        "name":         "Solicitor / Law Firm",
        "keywords":     ["solicitor", "law firm", "barrister",
                         "legal", "conveyancing", "lawyer", "llp"],
        "checks":       ["ssl", "mobile", "contact_page",
                         "load_speed", "email_found"],
        "pain_points":  [
            "client acquisition cost",
            "online trust signals",
            "case type visibility",
        ],
        "email_hook":   "attract higher-value clients online",
        "score_weights": {
            "reachable":    20,
            "ssl":          20,
            "mobile":       15,
            "contact_page": 20,
            "load_speed":   15,
            "email_found":  10,
        },
    },

    "general": {
        "name":         "General Business",
        "keywords":     [],
        "checks":       ["ssl", "mobile", "contact_page",
                         "load_speed", "email_found"],
        "pain_points":  [
            "online visibility",
            "lead generation",
            "website conversion",
        ],
        "email_hook":   "improve online performance and generate more leads",
        "score_weights": {
            "reachable":    25,
            "ssl":          20,
            "mobile":       20,
            "contact_page": 15,
            "load_speed":   10,
            "email_found":  10,
        },
    },
}


# ---------------------------------------------------------------------------
# _detect_industry
# ---------------------------------------------------------------------------

def _detect_industry(lead: dict) -> str:
    """
    Detect industry key from a lead dict.

    Searches lead["industry"], lead["business_name"], lead["company_name"],
    lead["sector"] against each profile's keyword list.
    Returns "general" if nothing matches.
    """
    search_text = " ".join([
        (lead.get("industry")      or ""),
        (lead.get("business_name") or ""),
        (lead.get("company_name")  or ""),
        (lead.get("sector")        or ""),
    ]).lower()

    if not search_text.strip():
        return "general"

    for profile_key, profile in INDUSTRY_PROFILES.items():
        if profile_key == "general":
            continue
        for keyword in profile.get("keywords", []):
            if keyword in search_text:
                logger.debug(
                    "_detect_industry: '%s' matched keyword '%s' → %s",
                    search_text[:40], keyword, profile_key,
                )
                return profile_key

    return "general"


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------

def get_profile(lead_or_key) -> dict:
    """
    Return the full industry profile dict for a lead dict or a string key.
    Always returns a valid dict — falls back to "general".
    Adds a "key" field to the returned dict.

    Accepts either:
        get_profile(lead_dict)   — detects industry from lead
        get_profile("care_home") — looks up by key string directly
    """
    if isinstance(lead_or_key, str):
        key = lead_or_key if lead_or_key in INDUSTRY_PROFILES else "general"
    else:
        key = _detect_industry(lead_or_key)

    profile        = INDUSTRY_PROFILES.get(key, INDUSTRY_PROFILES["general"]).copy()
    profile["key"] = key
    return profile


# ---------------------------------------------------------------------------
# WebsiteAuditor
# ---------------------------------------------------------------------------

class WebsiteAuditor:
    """
    Full website auditor using requests + BeautifulSoup4.

    Checks performed
    ----------------
    - Reachability (HTTP GET with timeout)
    - Load time (ms)
    - SSL / HTTPS
    - HTTPS redirect (HTTP → HTTPS)
    - Mobile friendliness (viewport meta tag)
    - Contact page link
    - Booking / appointment system signals
    - Email addresses in page HTML
    - Phone numbers in page HTML
    - CQC mention (care home specific)
    - Scoring 0-100 weighted by industry profile
    - Priority banding (URGENT / HIGH / MEDIUM / LOW)
    - Email hook generation
    - Recommendation text

    Usage
    -----
        auditor = WebsiteAuditor(industry_key="care_home")
        result  = auditor.audit_website("https://example.com", "Example Care")
        print(result["score"], result["priority"], result["issues"])
    """

    def __init__(
        self,
        headless:     bool = True,
        timeout:      int  = _DEFAULT_TIMEOUT,
        industry_key: str  = "general",
    ) -> None:
        self.headless     = headless
        self.timeout      = timeout
        self.industry_key = industry_key if industry_key in INDUSTRY_PROFILES else "general"
        self._session     = requests.Session()
        self._session.headers.update(_DEFAULT_HEADERS)
        logger.debug(
            "WebsiteAuditor v3.1 initialised (timeout=%ds, industry=%s)",
            timeout, self.industry_key,
        )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def audit_website(
        self,
        url:          str,
        business_name: str = "",
        industry_key: str  = "",
    ) -> dict:
        """
        Full audit of a single URL — returns dashboard-ready result dict.

        This is the primary method called by outreach_dashboard.py.
        Returns a dict with ALL keys the dashboard expects:
            score, grade, priority, industry, issues, strengths,
            recommendation, email_hook, should_email, emails_found,
            phone_found, load_time_ms, has_ssl, mobile_friendly, etc.

        Parameters
        ----------
        url           : str  — full URL or bare domain
        business_name : str  — used for email hook personalisation
        industry_key  : str  — override; falls back to self.industry_key
        """
        effective_industry = (
            industry_key
            if industry_key in INDUSTRY_PROFILES
            else self.industry_key
        )
        profile = INDUSTRY_PROFILES.get(
            effective_industry, INDUSTRY_PROFILES["general"]
        )

        # ── Run raw audit ─────────────────────────────────────────────
        raw = self.audit(url)

        # ── Score (0-100) ─────────────────────────────────────────────
        score_100 = self.score(raw, effective_industry)

        # ── Convert 0-100 → 0-10 for dashboard display ────────────────
        score_10  = round(score_100 / 10, 1)

        # ── Grade ─────────────────────────────────────────────────────
        grade = _score_to_grade(score_100)

        # ── Priority banding ──────────────────────────────────────────
        priority = _score_to_priority(score_100, raw.get("reachable", False))

        # ── Should email? ─────────────────────────────────────────────
        # Email if: reachable site with room to improve, OR unreachable (needs help)
        should_email = score_100 < 80

        # ── Email hook ────────────────────────────────────────────────
        name     = business_name.strip() or "your business"
        hook_tmpl = profile.get("email_hook", "improve your online presence")

        if not raw.get("reachable"):
            email_hook = (
                f"I tried visiting {name}'s website but couldn't reach it — "
                f"this means potential customers can't find you online either."
            )
        elif score_100 < 40:
            email_hook = (
                f"I audited {name}'s website and found several issues "
                f"that may be costing you customers — I can help you "
                f"{hook_tmpl}."
            )
        else:
            email_hook = (
                f"I noticed some quick wins on {name}'s website that could "
                f"help you {hook_tmpl}."
            )

        # ── Recommendation ────────────────────────────────────────────
        recs = raw.get("recommendations", [])
        if recs:
            recommendation = recs[0]
            if len(recs) > 1:
                recommendation += f" (plus {len(recs) - 1} more improvement{'s' if len(recs) > 2 else ''})"
        elif not raw.get("reachable"):
            recommendation = "Website is unreachable — fix hosting or build a new site immediately."
        else:
            recommendation = "Website looks solid — focus on conversion optimisation."

        # ── Strengths ─────────────────────────────────────────────────
        strengths = []
        if raw.get("has_ssl"):
            strengths.append("SSL certificate present")
        if raw.get("mobile_friendly"):
            strengths.append("Mobile-friendly viewport")
        if raw.get("has_contact_page"):
            strengths.append("Contact page found")
        if raw.get("has_booking"):
            strengths.append("Booking system detected")
        if raw.get("has_email"):
            strengths.append("Email address visible on site")
        if raw.get("load_speed_ok"):
            strengths.append(f"Fast load time ({raw.get('load_time_ms', 0)}ms)")
        if raw.get("cqc_mention"):
            strengths.append("CQC mentioned on site")
        if raw.get("https_redirect"):
            strengths.append("HTTP → HTTPS redirect active")

        # ── Assemble final result ─────────────────────────────────────
        result = {
            # Dashboard display keys
            "score":          score_10,
            "score_100":      score_100,
            "grade":          grade,
            "priority":       priority,
            "industry":       profile.get("name", effective_industry),
            "industry_key":   effective_industry,
            "issues":         raw.get("issues",   []),
            "strengths":      strengths,
            "recommendation": recommendation,
            "email_hook":     email_hook,
            "should_email":   should_email,

            # Contact data
            "emails_found":   raw.get("emails_found", []),
            "phone_found":    raw.get("phone_found",  ""),

            # Technical data
            "url":              raw.get("url",              url),
            "final_url":        raw.get("final_url",        url),
            "reachable":        raw.get("reachable",        False),
            "http_status":      raw.get("http_status",      0),
            "load_time_ms":     raw.get("load_time_ms",     0),
            "load_speed_ok":    raw.get("load_speed_ok",    False),
            "has_ssl":          raw.get("has_ssl",          False),
            "ssl_valid":        raw.get("ssl_valid",        False),
            "https_redirect":   raw.get("https_redirect",   False),
            "mobile_friendly":  raw.get("mobile_friendly",  False),
            "has_contact_page": raw.get("has_contact_page", False),
            "has_booking":      raw.get("has_booking",      False),
            "cqc_mention":      raw.get("cqc_mention",      False),
            "has_email":        raw.get("has_email",        False),
            "audited_at":       raw.get("audited_at",       None),
        }

        logger.info(
            "audit_website: %s → industry=%s score=%d/100 (%s/10) grade=%s priority=%s",
            url, effective_industry, score_100, score_10, grade, priority,
        )

        return result

    def audit(self, url: str) -> dict:
        """
        Raw audit of a single website URL.
        Returns technical check results — call audit_website() for
        the dashboard-ready version with score/grade/priority/hook.

        Parameters
        ----------
        url : str — Full URL including scheme, or bare domain.
        """
        url = self._normalise_url(url)

        if not url:
            return self._empty_result("", issues=["No URL provided"])

        logger.info("Raw audit: %s", url)
        result = self._empty_result(url)

        try:
            # ── Fetch page ────────────────────────────────────────────
            t_start  = time.monotonic()
            response = self._session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
            )
            load_ms  = int((time.monotonic() - t_start) * 1000)

            result["reachable"]    = True
            result["http_status"]  = response.status_code
            result["load_time_ms"] = load_ms
            result["final_url"]    = response.url

            # ── SSL check ─────────────────────────────────────────────
            result["has_ssl"]   = response.url.startswith("https://")
            result["ssl_valid"] = self._check_ssl(url)

            # ── HTTPS redirect check ──────────────────────────────────
            result["https_redirect"] = self._check_https_redirect(url)

            # ── Parse HTML ────────────────────────────────────────────
            soup = BeautifulSoup(response.text, "lxml")

            # ── Mobile friendly ───────────────────────────────────────
            result["mobile_friendly"] = self._check_mobile(soup)

            # ── Contact page ──────────────────────────────────────────
            result["has_contact_page"] = self._check_contact_page(
                soup, url
            )

            # ── Booking system ────────────────────────────────────────
            result["has_booking"] = self._check_booking(soup)

            # ── CQC mention ───────────────────────────────────────────
            result["cqc_mention"] = self._check_cqc(soup)

            # ── Email extraction ──────────────────────────────────────
            emails = self._extract_emails(soup, response.text)
            result["emails_found"] = emails
            result["has_email"]    = len(emails) > 0

            # ── Phone extraction ──────────────────────────────────────
            phone = self._extract_phone(soup, response.text)
            result["phone_found"] = phone

            # ── Load speed grade ──────────────────────────────────────
            result["load_speed_ok"] = load_ms < 3000  # under 3s = ok

            # ── Issues list ───────────────────────────────────────────
            result["issues"]          = self._build_issues(result)
            result["recommendations"] = self._build_recommendations(result)

        except requests.exceptions.SSLError:
            result["reachable"] = True   # site exists, SSL broken
            result["has_ssl"]   = False
            result["ssl_valid"] = False
            result["issues"].append("SSL certificate error")
            logger.warning("SSL error for %s", url)

        except requests.exceptions.ConnectionError:
            result["issues"].append("Could not connect to website")
            logger.warning("Connection error for %s", url)

        except requests.exceptions.Timeout:
            result["issues"].append(
                f"Website did not respond within {self.timeout}s"
            )
            logger.warning("Timeout for %s", url)

        except requests.exceptions.TooManyRedirects:
            result["issues"].append("Too many redirects")
            logger.warning("Too many redirects for %s", url)

        except Exception as exc:                    # noqa: BLE001
            result["issues"].append(f"Unexpected error: {exc}")
            logger.error("Audit error for %s: %s", url, exc)

        finally:
            result["stub"]       = False
            result["audited_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )

        return result

    def bulk_audit(self, urls: list[str]) -> list[dict]:
        """
        Audit multiple URLs sequentially.
        Returns results in the same order as input.
        """
        results = []
        for i, url in enumerate(urls, 1):
            logger.info("Bulk audit %d/%d: %s", i, len(urls), url)
            results.append(self.audit(url))
        return results

    def get_contact_info(self, url: str) -> dict:
        """
        Extract contact information from a website.
        Lighter than full audit — fetches page and extracts
        email + phone only.
        """
        url  = self._normalise_url(url)
        info = {"url": url, "email": "", "phone": "", "stub": False}

        if not url:
            return info

        try:
            response = self._session.get(
                url, timeout=self.timeout, allow_redirects=True
            )
            soup     = BeautifulSoup(response.text, "lxml")
            emails   = self._extract_emails(soup, response.text)
            phone    = self._extract_phone(soup, response.text)
            info["email"] = emails[0] if emails else ""
            info["phone"] = phone
        except Exception as exc:                    # noqa: BLE001
            logger.warning("get_contact_info failed for %s: %s", url, exc)

        return info

    def score(self, result: dict, industry_key: str = "general") -> int:
        """
        Calculate a 0-100 score for an audit result dict.
        Uses the industry profile's score_weights.
        """
        profile = INDUSTRY_PROFILES.get(
            industry_key, INDUSTRY_PROFILES["general"]
        )
        weights = profile.get("score_weights", {})
        total_w = sum(weights.values()) or 1
        earned  = 0

        mapping = {
            "reachable":      result.get("reachable",        False),
            "ssl":            result.get("has_ssl",           False),
            "ssl_valid":      result.get("ssl_valid",         False),
            "mobile":         result.get("mobile_friendly",   False),
            "contact_page":   result.get("has_contact_page",  False),
            "booking":        result.get("has_booking",       False),
            "load_speed":     result.get("load_speed_ok",     False),
            "email_found":    result.get("has_email",         False),
            "cqc_mention":    result.get("cqc_mention",       False),
            "https_redirect": result.get("https_redirect",    False),
        }

        for check, weight in weights.items():
            if mapping.get(check, False):
                earned += weight

        raw   = int((earned / total_w) * 100)
        score = max(0, min(100, raw))
        return score

    def close(self) -> None:
        """Close the requests session."""
        try:
            self._session.close()
        except Exception:                           # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # Private — checks                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalise_url(url: str) -> str:
        url = (url or "").strip()
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    @staticmethod
    def _check_mobile(soup: BeautifulSoup) -> bool:
        """True if a viewport meta tag is present."""
        for tag in soup.find_all("meta"):
            name = (tag.get("name") or "").lower()
            if name == "viewport":
                return True
        return False

    @staticmethod
    def _check_contact_page(soup: BeautifulSoup, base_url: str) -> bool:
        """True if any link text or href suggests a contact page."""
        contact_signals = [
            "contact", "get in touch", "reach us",
            "enquire", "enquiry", "find us",
        ]
        for a in soup.find_all("a", href=True):
            text = (a.get_text() or "").lower()
            href = (a["href"]    or "").lower()
            for signal in contact_signals:
                if signal in text or signal in href:
                    return True
        return False

    @staticmethod
    def _check_booking(soup: BeautifulSoup) -> bool:
        """True if booking/appointment signals are found."""
        booking_signals = [
            "book", "appointment", "reserve", "schedule",
            "booking", "calendly", "acuity", "online booking",
        ]
        text = soup.get_text(separator=" ").lower()
        for signal in booking_signals:
            if signal in text:
                return True
        for a in soup.find_all("a", href=True):
            href = (a["href"] or "").lower()
            for signal in ["book", "appointment", "calendly", "acuity"]:
                if signal in href:
                    return True
        return False

    @staticmethod
    def _check_cqc(soup: BeautifulSoup) -> bool:
        """True if CQC is mentioned anywhere on the page."""
        text = soup.get_text(separator=" ").lower()
        return "cqc" in text or "care quality commission" in text

    def _check_ssl(self, url: str) -> bool:
        """
        Attempt a real SSL handshake to verify certificate validity.
        Returns False on any SSL error.
        """
        try:
            parsed   = urlparse(url)
            hostname = parsed.hostname
            port     = parsed.port or 443
            ctx      = ssl.create_default_context()
            with socket.create_connection((hostname, port), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname):
                    return True
        except Exception:                           # noqa: BLE001
            return False

    def _check_https_redirect(self, url: str) -> bool:
        """
        Check if HTTP version of the URL redirects to HTTPS.
        """
        try:
            http_url = url.replace("https://", "http://", 1)
            if not http_url.startswith("http://"):
                http_url = "http://" + urlparse(url).netloc
            response = self._session.get(
                http_url, timeout=5, allow_redirects=True
            )
            return response.url.startswith("https://")
        except Exception:                           # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    # Private — extraction                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_emails(soup: BeautifulSoup, raw_html: str) -> list[str]:
        """
        Extract email addresses from:
        1. mailto: links
        2. Raw HTML text (regex)
        Returns deduplicated, lowercased list.
        """
        found: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"] or ""
            if href.lower().startswith("mailto:"):
                email = href[7:].split("?")[0].strip().lower()
                if email and _EMAIL_RE.match(email):
                    found.add(email)

        for match in _EMAIL_RE.finditer(raw_html):
            email = match.group().lower()
            if not any(fp in email for fp in [
                ".png", ".jpg", ".gif", ".css", ".js",
                "example.com", "yourdomain", "domain.com",
            ]):
                found.add(email)

        return sorted(found)

    @staticmethod
    def _extract_phone(soup: BeautifulSoup, raw_html: str) -> str:
        """
        Extract first phone number found from:
        1. tel: links
        2. Raw HTML regex
        Returns empty string if none found.
        """
        for a in soup.find_all("a", href=True):
            href = a["href"] or ""
            if href.lower().startswith("tel:"):
                number = href[4:].strip()
                if number:
                    return number

        match = _PHONE_RE.search(raw_html)
        if match:
            return match.group().strip()

        return ""

    # ------------------------------------------------------------------ #
    # Private — scoring helpers                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _load_speed_grade(ms: int) -> str:
        if ms == 0:   return "N/A"
        if ms < 1000: return "Excellent"
        if ms < 2000: return "Good"
        if ms < 3000: return "Average"
        if ms < 5000: return "Slow"
        return "Very Slow"

    @staticmethod
    def _build_issues(result: dict) -> list[str]:
        issues = []
        if not result.get("reachable"):
            issues.append("Website unreachable")
            return issues
        if not result.get("has_ssl"):
            issues.append("No SSL certificate (HTTP only)")
        if not result.get("ssl_valid"):
            issues.append("SSL certificate invalid or expired")
        if not result.get("mobile_friendly"):
            issues.append("Not mobile-friendly (no viewport meta tag)")
        if not result.get("has_contact_page"):
            issues.append("No contact page found")
        if not result.get("has_email"):
            issues.append("No email address visible on website")
        ms = result.get("load_time_ms", 0)
        if ms and ms > 3000:
            issues.append(f"Slow load time: {ms}ms (target <3000ms)")
        return issues

    @staticmethod
    def _build_recommendations(result: dict) -> list[str]:
        recs = []
        if not result.get("has_ssl"):
            recs.append("Add an SSL certificate — free via Let's Encrypt")
        if not result.get("mobile_friendly"):
            recs.append("Add a viewport meta tag for mobile compatibility")
        if not result.get("has_contact_page"):
            recs.append("Create a dedicated /contact page")
        if not result.get("has_email"):
            recs.append("Display an email address prominently")
        if not result.get("has_booking"):
            recs.append("Add an online booking/enquiry form")
        ms = result.get("load_time_ms", 0)
        if ms and ms > 3000:
            recs.append("Optimise images and enable caching to improve speed")
        return recs

    @staticmethod
    def _empty_result(url: str, issues: Optional[list[str]] = None) -> dict:
        return {
            "url":              url,
            "final_url":        url,
            "reachable":        False,
            "http_status":      0,
            "load_time_ms":     0,
            "load_speed_ok":    False,
            "has_ssl":          False,
            "ssl_valid":        False,
            "https_redirect":   False,
            "mobile_friendly":  False,
            "has_contact_page": False,
            "has_booking":      False,
            "cqc_mention":      False,
            "has_email":        False,
            "emails_found":     [],
            "phone_found":      "",
            "score":            0,
            "grade":            "N/A",
            "issues":           issues or [],
            "recommendations":  [],
            "industry":         "general",
            "profile":          {},
            "stub":             False,
            "audited_at":       None,
        }


# ---------------------------------------------------------------------------
# audit_lead_website   ← FIXED in v3.1
# ---------------------------------------------------------------------------

def audit_lead_website(lead: dict) -> dict:
    """
    Audit the website belonging to a lead dict.

    Reads lead["website"] (falls back to lead["url"]).
    Detects industry automatically from lead fields.
    Merges ALL audit results back onto the lead dict under
    website_* keys so outreach_dashboard.py can read them correctly.

    Handles three cases:
      1. No website URL → marked URGENT score=0, email hook generated
      2. Audit exception → marked HIGH, error recorded
      3. Successful audit → full results merged onto lead

    Parameters
    ----------
    lead : dict
        Lead record from the database (any shape — robust to missing keys).

    Returns
    -------
    dict — copy of original lead dict with website_* keys merged in.
           Original lead is never mutated.
    """
    # ── Work on a copy — never mutate the caller's dict ─────────────
    lead_out = dict(lead)

    url = (
        lead.get("website") or
        lead.get("url")     or
        ""
    ).strip()

    business_name = (
        lead.get("business_name") or
        lead.get("name")          or
        "Unknown"
    ).strip()

    industry_str = str(lead.get("industry") or "")

    # ── Case 1: No website URL ───────────────────────────────────────
    if not url:
        lead_out.update({
            "website_score":          0,
            "website_grade":          "F",
            "website_priority":       "URGENT",
            "website_industry":       industry_str or "Business",
            "website_issues":         ["No website found"],
            "website_strengths":      [],
            "website_recommendation": (
                "Build a website immediately. "
                "This business is invisible online."
            ),
            "website_email_hook": (
                f"I noticed {business_name} doesn't have a website yet — "
                f"most customers search online before calling."
            ),
            "website_should_email":   bool(lead.get("email", "").strip()),
            "website_audit_full":     {},
            "issues_count":           1,
        })
        logger.info(
            "audit_lead_website: %s — no URL, marked URGENT", business_name
        )
        return lead_out

    # ── Detect industry ──────────────────────────────────────────────
    industry_key = _detect_industry(lead)

    # ── Case 2 + 3: Run the audit ────────────────────────────────────
    auditor = WebsiteAuditor(industry_key=industry_key)

    try:
        result = auditor.audit_website(
            url,
            business_name,
            industry_key=industry_key,
        )
    except Exception as exc:
        logger.warning(
            "audit_lead_website failed for %s (%s): %s",
            business_name, url, exc,
        )
        lead_out.update({
            "website_score":          0,
            "website_grade":          "F",
            "website_priority":       "HIGH",
            "website_industry":       industry_str or "Business",
            "website_issues":         [f"Audit failed: {exc}"],
            "website_strengths":      [],
            "website_recommendation": "Website could not be audited — check URL.",
            "website_email_hook":     "",
            "website_should_email":   False,
            "website_audit_full":     {},
            "issues_count":           1,
        })
        return lead_out

    # ── Merge audit result onto lead dict with correct key names ─────
    lead_out.update({
        "website_score":          result.get("score",          0),
        "website_grade":          result.get("grade",          "F"),
        "website_priority":       result.get("priority",       "LOW"),
        "website_industry":       result.get("industry",       industry_str or "Business"),
        "website_issues":         result.get("issues",         []),
        "website_strengths":      result.get("strengths",      []),
        "website_recommendation": result.get("recommendation", ""),
        "website_email_hook":     result.get("email_hook",     ""),
        "website_should_email":   result.get("should_email",   False),
        "website_audit_full":     result,
        "issues_count":           len(result.get("issues",     [])),
    })

    logger.info(
        "audit_lead_website: %s (%s) → industry=%s score=%s grade=%s priority=%s",
        business_name,
        url,
        lead_out["website_industry"],
        lead_out["website_score"],
        lead_out["website_grade"],
        lead_out["website_priority"],
    )

    return lead_out


# ---------------------------------------------------------------------------
# audit_website  (module-level convenience)
# ---------------------------------------------------------------------------

def audit_website(url: str, business_name: str = "") -> dict:
    """
    Audit a URL directly without a lead dict.
    Uses general industry profile for scoring.
    Returns dashboard-ready result dict.
    """
    auditor = WebsiteAuditor(industry_key="general")
    return auditor.audit_website(url, business_name, industry_key="general")


# ---------------------------------------------------------------------------
# get_website_score
# ---------------------------------------------------------------------------

def get_website_score(url: str) -> int:
    """Return a 0-100 integer quality score for a URL."""
    auditor   = WebsiteAuditor()
    raw       = auditor.audit(url)
    score_100 = auditor.score(raw, "general")
    return int(score_100)


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """
    Returns True — full implementation is active.
    requests + BeautifulSoup4 are available in this environment.
    """
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_to_grade(score: int) -> str:
    if score >= 80: return "A"
    if score >= 60: return "B"
    if score >= 40: return "C"
    if score >= 20: return "D"
    return "F"


def _score_to_priority(score: int, reachable: bool) -> str:
    """
    Convert a 0-100 score to a priority band.

    URGENT  — site unreachable OR score 0-19
    HIGH    — score 20-39
    MEDIUM  — score 40-59
    LOW     — score 60-100
    """
    if not reachable:
        return "URGENT"
    if score < 20:
        return "URGENT"
    if score < 40:
        return "HIGH"
    if score < 60:
        return "MEDIUM"
    return "LOW"
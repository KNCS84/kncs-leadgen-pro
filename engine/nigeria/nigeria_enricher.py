"""
KNCS LeadGen Pro — engine/nigeria/nigeria_enricher.py — v10.1

Audit fixes applied (v10.1):

  BUG-N01  (HIGH) New session per request / no user-agent rotation.
            Module-level shared requests.Session via get_session().
            make_headers() rotates User-Agent from pool of 4 on every
            request.

  BUG-N02  (HIGH) No block detection for challenge pages.
            All HTTP responses checked by is_challenge_response() before
            parsing. DuckDuckGo HTML endpoint replaces direct Google search
            (NEW-N01) — far less likely to challenge.

  BUG-N03  (HIGH) No redirect limit — 360-second hang.
            Session has max_redirects=5. requests.TooManyRedirects caught
            explicitly on every HTTP call.

  BUG-N04  (HIGH) Error path did not initialise all output keys.
            make_empty_enriched(lead) factory initialises ALL expected
            output keys on both success and error paths in enrich_batch().

  BUG-N05  (MEDIUM) Inconsistent wa_outreach_url key.
            Canonical key 'wa_outreach_url' used consistently in
            _build_whatsapp_url() and written in enrich_lead().

  BUG-N06  (MEDIUM) data_sources.append("deep_web_phone") fires multiple
            times. Consolidated — appended at most once per enrichment run
            via a local seen_sources set.

  NEW-N01  (CRITICAL) Google Search replaced with DuckDuckGo HTML endpoint
            — Google blocks bots aggressively; DDG is the registered engine.

  NEW-N02  (CRITICAL) Phone/email regex on raw Google HTML removed.
            DuckDuckGo response is cleaner; regex tuned for plain-text
            result snippets not JS-heavy Google pages.

  NEW-N03  (HIGH) normalize_nigerian_phone() called before storing any
            phone value — raw regex matches with spaces/dashes normalised.

  NEW-N04  (HIGH) Website extraction added to _search_web() — result URL
            from DuckDuckGo result links extracted and stored.

  NEW-N05  (HIGH) Enrichment gate: enrichment_status checked before
            enrichment; set to 'enriched' on completion.

  NEW-N06  (HIGH) enrich_batch() implemented with make_empty_enriched()
            factory — all output keys initialised on both success and
            error paths.

  NEW-N07  (HIGH) User-Agent rotation: make_headers() selects from pool
            of 4 agents on every request.

  NEW-N08  (MEDIUM) wa_outreach_url built and written for every Nigerian
            lead that has a phone number.

  NEW-N09  (MEDIUM) Email exclusion list expanded — schema.org, w3.org,
            google.com, example.com, sentry.io, cloudflare.com filtered.

  NEW-N10  (MEDIUM) get_session() factory: module-level Session with
            connection pooling and HTTPAdapter retry.

  NEW-N11  (LOW) Success logging added — fields populated are logged
            at DEBUG level for auditability.

  NEW-N12  (LOW) urllib.parse / urllib.request replaced with requests.
"""

import logging
import random
import re
import time
import urllib.parse
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

# ── Optional database phone normaliser ───────────────────────────────────────
try:
    from database import normalize_phone as _db_normalize_phone
    _HAVE_DB_NORMALIZE = True
except ImportError:
    _db_normalize_phone = None
    _HAVE_DB_NORMALIZE  = False

# ── Optional Nigeria Maps client phone normaliser ────────────────────────────
try:
    from engine.nigeria.google_maps_client import normalize_nigerian_phone
except ImportError:
    def normalize_nigerian_phone(raw: str) -> str:   # type: ignore[misc]
        """Fallback normaliser when google_maps_client is unavailable."""
        if not raw:
            return ""
        has_plus = str(raw).strip().startswith("+")
        digits   = re.sub(r"[^\d]", "", raw)
        if len(digits) < 7:
            return ""
        if digits.startswith("234") and len(digits) >= 12:
            return "+" + digits
        if digits.startswith("0") and len(digits) == 11:
            return "+234" + digits[1:]
        return ("+" + digits) if has_plus else digits


# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

# BUG-N01 / NEW-N07: user-agent rotation pool
_USER_AGENTS: List[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
]

# BUG-N03: redirect cap
MAX_REDIRECTS = 5

# NEW-N09: expanded email exclusion domains
_EMAIL_NOISE_DOMAINS: set = {
    "google.com", "schema.org", "w3.org", "example.com",
    "sentry.io", "cloudflare.com", "facebook.com",
    "twitter.com", "linkedin.com", "instagram.com",
    "wikipedia.org", "youtube.com", "apple.com",
}

# BUG-N02: challenge page markers
_CHALLENGE_MARKERS: List[str] = [
    "captcha", "robot check", "security check",
    "cloudflare", "access denied", "verify you are human",
    "just a moment", "ddos-guard", "please enable cookies",
    "too many requests",
]


# ─────────────────────────────────────────────────────────
# Module-level session (BUG-N01 / NEW-N10)
# ─────────────────────────────────────────────────────────

_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    """
    BUG-N01 / NEW-N10: return the shared module-level requests.Session.
    BUG-N03: max_redirects=5 — no redirect-loop hangs.
    Lazy-initialised on first call.
    """
    global _session
    if _session is None:
        s = requests.Session()
        s.max_redirects = MAX_REDIRECTS
        adapter = HTTPAdapter(max_retries=1)
        s.mount("https://", adapter)
        s.mount("http://",  adapter)
        _session = s
    return _session


def make_headers() -> dict:
    """BUG-N01 / NEW-N07: rotate User-Agent on every call."""
    return {
        "User-Agent":                random.choice(_USER_AGENTS),
        "Accept":                    (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language":           "en-GB,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


# ─────────────────────────────────────────────────────────
# Challenge detection (BUG-N02)
# ─────────────────────────────────────────────────────────

def is_challenge_response(html: str) -> bool:
    """
    BUG-N02: return True if the page is a CAPTCHA or rate-limit challenge.
    Only inspect first 8 KB — avoids large-string allocation.
    """
    preview = html[:8192].lower()
    return any(marker in preview for marker in _CHALLENGE_MARKERS)


# ─────────────────────────────────────────────────────────
# Output key factory (BUG-N04)
# ─────────────────────────────────────────────────────────

def make_empty_enriched(lead: dict) -> dict:
    """
    BUG-N04: initialise ALL expected output keys so callers never get
    KeyError on the error path.

    Merges all input lead keys with a complete set of enrichment output
    keys — missing keys default to empty string or appropriate sentinel.
    """
    base: Dict = {
        # Core identity (may already be set from scraper)
        "business_name":     lead.get("business_name", ""),
        "phone":             lead.get("phone", ""),
        "email":             lead.get("email", ""),
        "website":           lead.get("website", ""),
        "address":           lead.get("address", ""),
        "city":              lead.get("city", ""),
        "country":           lead.get("country", "Nigeria"),
        "industry":          lead.get("industry", ""),
        "niche":             lead.get("niche", ""),
        "source":            lead.get("source", ""),
        "place_id":          lead.get("place_id", ""),
        "maps_url":          lead.get("maps_url", ""),
        "rating":            lead.get("rating", ""),
        "lat":               lead.get("lat", ""),
        "lng":               lead.get("lng", ""),

        # Enrichment outputs
        "decision_maker_name":  lead.get("decision_maker_name", ""),
        "decision_maker_title": lead.get("decision_maker_title", ""),
        "linkedin_url":         lead.get("linkedin_url", ""),

        # BUG-N05: canonical wa_outreach_url key
        "wa_outreach_url":      lead.get("wa_outreach_url", ""),
        "wa_status":            lead.get("wa_status", ""),

        # Enrichment metadata
        "enrichment_status":    lead.get("enrichment_status", ""),
        "enrichment_notes":     lead.get("enrichment_notes", ""),
        "data_sources":         lead.get("data_sources", ""),

        # Fit / urgency (set by scorer, not enricher — but key must exist)
        "fit_score":            lead.get("fit_score", ""),
        "urgency_score":        lead.get("urgency_score", ""),
    }
    # Preserve any additional keys from the original lead
    for k, v in lead.items():
        if k not in base:
            base[k] = v
    return base


# ─────────────────────────────────────────────────────────
# WhatsApp URL builder (BUG-N05)
# ─────────────────────────────────────────────────────────

def _build_whatsapp_url(phone: str) -> str:
    """
    BUG-N05: always use canonical key 'wa_outreach_url'.
    Build a wa.me link from a normalised Nigerian phone number.
    Returns empty string if phone is empty or not normalised.
    """
    if not phone:
        return ""
    # wa.me requires digits only — strip leading +
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) < 10:
        return ""
    return f"https://wa.me/{digits}"


# ─────────────────────────────────────────────────────────
# Low-level web search
# ─────────────────────────────────────────────────────────

def _search_web(business_name: str) -> dict:
    """
    BUG-N01 / NEW-N01: DuckDuckGo HTML endpoint replaces direct Google
    search. Google blocks bots aggressively — DDG is registered engine.

    BUG-N02: is_challenge_response() checked before parsing.
    BUG-N03: session.max_redirects=5, TooManyRedirects caught.
    NEW-N04: website extracted from DDG result links.
    NEW-N07: User-Agent rotated via make_headers().
    NEW-N09: expanded email noise filter.
    """
    result: dict = {"phone": "", "email": "", "website": ""}

    try:
        query = urllib.parse.quote(
            f"{business_name} Nigeria contact phone email"
        )
        url = "https://html.duckduckgo.com/html/?q=" + query

        session  = get_session()
        response = session.get(
            url,
            headers=make_headers(),
            timeout=(10, 20),   # connect + read
        )
        html = response.text

        # BUG-N02: block detection
        if is_challenge_response(html):
            log.warning(
                "_search_web: DuckDuckGo challenge page detected for '%s'",
                business_name,
            )
            return result

        # ── Phone extraction ─────────────────────────────────────────────
        phone_match = re.search(
            r"(\+?234[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{4}"
            r"|0[789][01]\d{8}"
            r"|\+234\d{10})",
            html,
        )
        if phone_match:
            raw_phone = phone_match.group(0).strip()
            # NEW-N03: normalise before storage
            normalised = normalize_nigerian_phone(raw_phone)
            result["phone"] = normalised or raw_phone

        # ── Email extraction ─────────────────────────────────────────────
        email_match = re.search(
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
            html,
        )
        if email_match:
            email = email_match.group(0).lower()
            domain = email.split("@")[-1]
            # NEW-N09: expanded noise filter
            if domain not in _EMAIL_NOISE_DOMAINS and not any(
                noise in domain
                for noise in ["google", "schema", "w3.org", "example"]
            ):
                result["email"] = email

        # ── Website extraction (NEW-N04) ─────────────────────────────────
        # DDG result links are in <a class="result__url"> tags
        website_match = re.search(
            r'<a[^>]+class="[^"]*result__url[^"]*"[^>]*>\s*(https?://[^<\s]+)',
            html, re.IGNORECASE,
        )
        if not website_match:
            # Fallback: any http(s) link not from DDG/Google
            website_match = re.search(
                r'href="(https?://(?!(?:duckduckgo|google|bing|'
                r'facebook|twitter|linkedin|instagram))[^"]+)"',
                html, re.IGNORECASE,
            )
        if website_match:
            candidate = website_match.group(1).strip().rstrip("/")
            if len(candidate) > 10:
                result["website"] = candidate

    except requests.TooManyRedirects:
        # BUG-N03: explicit catch
        log.warning(
            "_search_web: too many redirects for '%s' — skipping",
            business_name,
        )
    except Exception as exc:
        log.debug("_search_web failed for '%s': %s", business_name, exc)

    return result


# ─────────────────────────────────────────────────────────
# NigeriaEnricher class
# ─────────────────────────────────────────────────────────

class NigeriaEnricher:
    """
    Enrich Nigerian business leads with contact information.

    All registered bugs (BUG-N01 through BUG-N06) addressed.
    See module docstring for full fix details.
    """

    def enrich_lead(self, lead: dict) -> dict:
        """
        Enrich a single lead dict.

        BUG-N04: make_empty_enriched() ensures all output keys exist.
        BUG-N05: wa_outreach_url written with canonical key.
        NEW-N05: enrichment_status gate — skip already-enriched leads.
        NEW-N11: debug logging of populated fields.
        """
        # BUG-N04: initialise all output keys first
        enriched = make_empty_enriched(lead)

        name = enriched.get("business_name", "")
        if not name:
            enriched["enrichment_status"] = "failed"
            enriched["enrichment_notes"]  = "missing business_name"
            return enriched

        # NEW-N05: idempotency gate
        current_status = enriched.get("enrichment_status", "")
        if current_status in ("enriched", "complete"):
            log.debug(
                "enrich_lead: '%s' already enriched — skipping", name
            )
            return enriched

        populated: List[str] = []

        try:
            web_data = _search_web(name)

            if web_data.get("phone") and not enriched.get("phone"):
                enriched["phone"] = web_data["phone"]
                populated.append("phone")

            if web_data.get("email") and not enriched.get("email"):
                enriched["email"] = web_data["email"]
                populated.append("email")

            if web_data.get("website") and not enriched.get("website"):
                enriched["website"] = web_data["website"]
                populated.append("website")

            # BUG-N05 / NEW-N08: build wa_outreach_url with canonical key
            if enriched.get("phone") and not enriched.get("wa_outreach_url"):
                wa_url = _build_whatsapp_url(enriched["phone"])
                if wa_url:
                    enriched["wa_outreach_url"] = wa_url
                    enriched["wa_status"]       = "estimated"
                    populated.append("wa_outreach_url")

            enriched["enrichment_status"] = "enriched"

            # NEW-N11: audit log
            if populated:
                log.debug(
                    "enrich_lead: '%s' populated fields: %s",
                    name, ", ".join(populated),
                )
            else:
                log.debug(
                    "enrich_lead: '%s' — no new fields found", name
                )

        except Exception as exc:
            log.warning("NigeriaEnricher.enrich_lead error for '%s': %s", name, exc)
            enriched["enrichment_status"] = "failed"
            enriched["enrichment_notes"]  = str(exc)[:200]

        return enriched

    def enrich_batch(self, leads: List[dict]) -> List[dict]:
        """
        BUG-N04: all output keys guaranteed via make_empty_enriched().
        BUG-N06: data_sources.append("deep_web_phone") fires at most
                 once per lead via seen_sources set.

        Processes each lead individually — error in one lead does not
        affect others.
        """
        results: List[dict] = []

        for lead in leads:
            # BUG-N04: error path gets all keys via make_empty_enriched()
            try:
                enriched = self.enrich_lead(lead)

                # BUG-N06: data_sources appended at most once per run
                seen_sources: set = set(
                    (enriched.get("data_sources") or "").split(",")
                )
                if enriched.get("phone") and "deep_web_phone" not in seen_sources:
                    seen_sources.discard("")
                    seen_sources.add("deep_web_phone")
                    enriched["data_sources"] = ",".join(sorted(seen_sources))

                results.append(enriched)

            except Exception as exc:
                # BUG-N04: error path — all keys still initialised
                log.error(
                    "enrich_batch: unhandled error for '%s': %s",
                    lead.get("business_name", "?"), exc,
                )
                error_lead = make_empty_enriched(lead)
                error_lead["enrichment_status"] = "failed"
                error_lead["enrichment_notes"]  = str(exc)[:200]
                results.append(error_lead)

            # Polite pause between leads (BUG-N01 rate limiting)
            time.sleep(random.uniform(0.5, 1.5))

        return results
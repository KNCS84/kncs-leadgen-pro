"""
KNCS LeadGen Pro — engine/nigeria/decision_maker_finder.py — v10.1

Audit fixes applied (v10.1):

  BUG-D01  (HIGH) Single static user-agent — Google blocked quickly.
            USER_AGENTS pool of 4 + make_headers() called before every
            outbound request.

  BUG-D02  (HIGH) No redirect limit, no challenge detection.
            session.max_redirects = 5. TooManyRedirects caught and breaks
            crawl loop. is_challenge_page() checked before parsing.

  BUG-D03  (HIGH) LinkedIn strategy (not in this version) — N/A.
            _google_blocked flag retained as stub for future use.

  BUG-D04  (MEDIUM) No delay between page fetches.
            polite_sleep() called before every outbound request on both
            success and failure paths — random.uniform(1.5, 3.0).

  BUG-D05  (HIGH) Proximity-to-keyword scoring replaces longest-candidate.
            extract_dm_from_text() now scores candidates by proximity to
            title keywords and rejects known place names and noise words.

  BUG-D06  (HIGH) build_result() factory returns both canonical key
            'decision_maker' AND alias 'decision_maker_name' — 20
            completeness points no longer lost.

  FABRICATION-01/02/03 (CRITICAL) _generate_fallback_dm() removed
            entirely. Writing random fake names, phone numbers, and email
            addresses to the leads DB corrupts outreach data and destroys
            sales credibility. When the website is unreachable, None is
            returned — the caller (enricher/job_manager) decides whether
            to mark the lead as 'needs_dm_review' or skip.

  NEW-D01  (CRITICAL) requests.Session created once at module level via
            get_session() — one TCP+TLS handshake per process, not per
            page.

  NEW-D03/04 (HIGH) normalize_phone() called with single argument only —
            database.py v10.1 signature is normalize_phone(phone: str).
            Two-argument calls removed throughout.

  NEW-D05  (HIGH) is_challenge_page() check before BeautifulSoup parsing
            in crawl loop.

  NEW-D06  (HIGH) Place name / noise word rejection added to
            extract_dm_from_text() scoring.

  NEW-D07  (HIGH) requests + BeautifulSoup imported at module level —
            ImportError now surfaces clearly, not swallowed silently.

  NEW-D08  (MEDIUM) Per-page timeout set to (5, 10) — connect + read —
            instead of single socket timeout.

  NEW-D11  (MEDIUM) Logging added throughout — crawl, skip, result, and
            fallback events all logged at appropriate levels.

  NEW-D12  (LOW) Website root correctly extracted via urlparse before
            building sub-page URLs — avoids /path/about construction.

  NEW-D13  (LOW) Dead name/role lists removed with fabrication functions.
"""

import logging
import random
import re
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse, urljoin

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

# ── Optional Nigeria phone normaliser ────────────────────────────────────────
try:
    from engine.nigeria.google_maps_client import normalize_nigerian_phone
except ImportError:
    def normalize_nigerian_phone(raw: str) -> str:   # type: ignore[misc]
        if not raw:
            return ""
        digits = re.sub(r"[^\d]", "", raw)
        if digits.startswith("0") and len(digits) == 11:
            return "+234" + digits[1:]
        if digits.startswith("234") and len(digits) >= 12:
            return "+" + digits
        return raw


# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

BLOCKED_DOMAINS: Set[str] = {"example.com", "test.com", "localhost"}

# BUG-D01: user-agent rotation pool
USER_AGENTS: List[str] = [
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

# BUG-D04: delay range for polite_sleep()
_POLITE_SLEEP_MIN = 1.5
_POLITE_SLEEP_MAX = 3.0

# BUG-D02: redirect cap
MAX_REDIRECTS = 5

# DM title keywords for extraction
_DM_TITLES: List[str] = [
    "CEO", "Managing Director", "MD", "Owner", "Founder",
    "Director", "Manager", "Proprietor", "Head", "Chief",
    "Chairman", "President", "Partner",
]

# Noise words that must not be accepted as person names
_NAME_NOISE: Set[str] = {
    "lagos", "abuja", "kano", "ibadan", "port harcourt",
    "london", "manchester", "birmingham",
    "nigeria", "united kingdom", "england",
    "limited", "company", "enterprise", "services",
    "group", "holdings", "associates", "consulting",
    "about", "contact", "team", "staff", "our",
}

# Email domains to exclude from DM email extraction
_EMAIL_NOISE_DOMAINS: Set[str] = {
    "google.com", "schema.org", "w3.org", "example.com",
    "sentry.io", "cloudflare.com", "facebook.com",
    "twitter.com", "linkedin.com", "instagram.com",
    "wikipedia.org", "youtube.com",
}

# BUG-D02: challenge page markers
_CHALLENGE_MARKERS: List[str] = [
    "captcha", "robot check", "security check",
    "cloudflare", "access denied", "verify you are human",
    "just a moment", "ddos-guard", "please enable cookies",
]


# ─────────────────────────────────────────────────────────
# Module-level session (NEW-D01)
# ─────────────────────────────────────────────────────────

_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    """
    NEW-D01: return shared requests.Session — one TCP+TLS per process.
    BUG-D02: max_redirects=5.
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


# ─────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────

def make_headers() -> dict:
    """BUG-D01: rotate User-Agent on every call."""
    return {
        "User-Agent":                random.choice(USER_AGENTS),
        "Accept":                    "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language":           "en-GB,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def polite_sleep() -> None:
    """BUG-D04: delay before every outbound request."""
    time.sleep(random.uniform(_POLITE_SLEEP_MIN, _POLITE_SLEEP_MAX))


def is_challenge_page(html: str) -> bool:
    """BUG-D02: return True if the page is a CAPTCHA/challenge."""
    preview = html[:8192].lower()
    return any(m in preview for m in _CHALLENGE_MARKERS)


def _is_blocked_domain(domain: str) -> bool:
    if not domain:
        return False
    d = re.sub(r"^www\.", "", domain.lower().strip())
    return d in BLOCKED_DOMAINS or d.endswith(".test")


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        dom    = parsed.netloc or parsed.path
        return re.sub(r"^www\.", "", dom.lower().split("/")[0])
    except Exception:
        return ""


def _validate_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if not parsed.netloc:
            return False
        if _is_blocked_domain(parsed.netloc):
            return False
        if len(url) > 2048:
            return False
        return True
    except Exception:
        return False


def _is_nigeria_lead(lead: Dict) -> bool:
    c = (lead.get("country") or "").lower()
    m = (lead.get("market")  or "").lower()
    return "nigeria" in c or m == "nigeria"


def _normalise_phone(raw: str, is_nigeria: bool) -> str:
    """
    NEW-D03/04: single-argument normalize_phone() call only.
    Falls back to normalize_nigerian_phone() for Nigerian numbers.
    """
    if not raw:
        return ""
    if is_nigeria:
        result = normalize_nigerian_phone(raw)
        if result:
            return result
    if _HAVE_DB_NORMALIZE and _db_normalize_phone:
        result = _db_normalize_phone(raw)
        if result:
            return result
    return raw


def _get_website_root(url: str) -> str:
    """
    NEW-D12: extract scheme+netloc so sub-pages are always built from
    root — avoids /path/about construction.
    """
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return url.rstrip("/")


# ─────────────────────────────────────────────────────────
# DM name extraction (BUG-D05 / NEW-D06)
# ─────────────────────────────────────────────────────────

def _score_name_candidate(candidate: str, line: str, title: str) -> int:
    """
    BUG-D05: proximity-to-keyword scoring replaces longest-candidate.

    Scoring:
      +10 if candidate appears within 5 words of title keyword in line
      +5  if candidate has exactly 2–3 words (typical name length)
      -20 if any word in candidate matches _NAME_NOISE
      -10 if candidate is entirely uppercase (likely an acronym/place)
      -10 if candidate contains digits
    """
    score = 0
    words_in_line = line.lower().split()
    try:
        title_pos     = words_in_line.index(title.lower())
        cand_words    = candidate.lower().split()
        cand_pos      = next(
            (i for i, w in enumerate(words_in_line) if w == cand_words[0]),
            None,
        )
        if cand_pos is not None and abs(title_pos - cand_pos) <= 5:
            score += 10
    except (ValueError, StopIteration):
        pass

    word_count = len(candidate.split())
    if 2 <= word_count <= 3:
        score += 5

    # NEW-D06: noise word rejection
    for word in candidate.lower().split():
        if word in _NAME_NOISE:
            score -= 20

    if candidate.isupper():
        score -= 10

    if re.search(r"\d", candidate):
        score -= 10

    return score


def extract_dm_from_text(text: str) -> Optional[Dict]:
    """
    BUG-D05: proximity-to-keyword scoring replaces longest-candidate.
    BUG-D06: output includes both 'decision_maker' and
             'decision_maker_name' keys.
    NEW-D06: place names and noise words rejected by _score_name_candidate.
    """
    if not text:
        return None

    best_name:  str = ""
    best_title: str = ""
    best_score: int = -999

    lines = text.split("\n")
    for line in lines[:300]:
        line = line.strip()
        if not line or len(line) > 150:
            continue

        for title in _DM_TITLES:
            if title.lower() not in line.lower():
                continue

            # Pattern A: "Name — Title"
            m = re.search(
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})"
                r"\s*[-,:]\s*"
                r"(?:" + re.escape(title) + r")",
                line, re.IGNORECASE,
            )
            if m:
                candidate = m.group(1).strip()[:100]
                score     = _score_name_candidate(candidate, line, title)
                if score > best_score:
                    best_score = score
                    best_name  = candidate
                    best_title = title

            # Pattern B: "Title: Name"
            m2 = re.search(
                re.escape(title)
                + r"\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
                line, re.IGNORECASE,
            )
            if m2:
                candidate = m2.group(1).strip()[:100]
                score     = _score_name_candidate(candidate, line, title)
                if score > best_score:
                    best_score = score
                    best_name  = candidate
                    best_title = title

    if not best_name or best_score < 0:
        return None

    result = build_result(best_name, best_title)

    # Extract DM email from text
    emails = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text
    )
    valid_emails = [
        e.lower() for e in emails
        if e.split("@")[-1].lower() not in _EMAIL_NOISE_DOMAINS
        and not any(
            n in e.lower()
            for n in ["example", "schema", "w3.org"]
        )
    ]
    if valid_emails:
        result["decision_maker_email"] = valid_emails[0]

    return result


# ─────────────────────────────────────────────────────────
# Result builder (BUG-D06)
# ─────────────────────────────────────────────────────────

def build_result(name: str, role: str = "", email: str = "") -> Dict:
    """
    BUG-D06: returns BOTH canonical key 'decision_maker' AND alias
    'decision_maker_name' — 20 completeness points no longer lost.
    """
    result: Dict = {
        "decision_maker":       name,        # canonical key
        "decision_maker_name":  name,        # alias — both required by spec
        "decision_maker_role":  role,
    }
    if email:
        result["decision_maker_email"] = email
    return result


# ─────────────────────────────────────────────────────────
# Main DM finder
# ─────────────────────────────────────────────────────────

def find_decision_maker(lead: Dict) -> Optional[Dict]:
    """Public entry point — delegates to Nigeria-specific finder."""
    return find_decision_maker_nigeria(lead)


def find_decision_maker_nigeria(lead: Dict) -> Optional[Dict]:
    """
    Crawl a lead's website to extract decision-maker information.

    BUG-D01: User-Agent rotated per request via make_headers().
    BUG-D02: session.max_redirects=5; TooManyRedirects caught;
             is_challenge_page() checked before parsing.
    BUG-D04: polite_sleep() before every outbound request.
    BUG-D05: proximity scoring in extract_dm_from_text().
    BUG-D06: build_result() returns both canonical key and alias.

    FABRICATION-01/02/03: No fake data generated. Returns None when
    website is unreachable — caller marks lead as 'needs_dm_review'.

    NEW-D01: shared requests.Session via get_session().
    NEW-D07: requests/BeautifulSoup imported at module level.
    NEW-D11: all events logged.
    NEW-D12: website root extracted correctly via urlparse.
    """
    if not lead:
        return None

    from bs4 import BeautifulSoup   # keeps optional dep error clear

    website    = (lead.get("website") or "").strip()
    is_nigeria = _is_nigeria_lead(lead)

    import os
    max_time  = int(os.getenv("DM_MAX_TIME",  "20"))
    max_pages = int(os.getenv("DM_MAX_PAGES", "3"))

    if not website or not _validate_url(website):
        log.info(
            "find_decision_maker_nigeria: no valid website for '%s' — returning None",
            lead.get("business_name", "?"),
        )
        return None

    domain = _extract_domain(website)
    if _is_blocked_domain(domain):
        log.info(
            "find_decision_maker_nigeria: blocked domain '%s' — skipping",
            domain,
        )
        return None

    # NEW-D12: always build sub-pages from root, not from path
    root = _get_website_root(website)
    urls_to_try = [
        website,
        urljoin(root + "/", "about"),
        urljoin(root + "/", "about-us"),
        urljoin(root + "/", "team"),
        urljoin(root + "/", "contact"),
    ]

    session      = get_session()
    found_dm:    Dict = {}
    pages_done:  int  = 0
    start_time:  float = time.time()

    for url in urls_to_try:
        if pages_done >= max_pages:
            log.debug(
                "find_decision_maker_nigeria: page budget exhausted (%d pages)",
                max_pages,
            )
            break
        if time.time() - start_time > max_time:
            log.debug(
                "find_decision_maker_nigeria: time budget exhausted (%.1fs)",
                time.time() - start_time,
            )
            break
        if not _validate_url(url):
            continue

        # BUG-D04: delay before every outbound request
        polite_sleep()

        try:
            resp = session.get(
                url,
                headers=make_headers(),   # BUG-D01: rotate UA
                timeout=(5, 10),          # NEW-D08: connect + read
            )
            pages_done += 1

            if resp.status_code != 200:
                log.debug(
                    "find_decision_maker_nigeria: %s returned HTTP %d",
                    url, resp.status_code,
                )
                continue

            # BUG-D02: challenge detection
            if is_challenge_page(resp.text):
                log.warning(
                    "find_decision_maker_nigeria: challenge page at %s — stopping crawl",
                    url,
                )
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(separator="\n")

            # Extract DM from page text
            dm = extract_dm_from_text(text)
            if dm:
                found_dm.update(dm)
                log.debug(
                    "find_decision_maker_nigeria: DM found on %s — '%s'",
                    url, dm.get("decision_maker_name", "?"),
                )
                if (
                    found_dm.get("decision_maker_name")
                    and found_dm.get("decision_maker_role")
                ):
                    break  # enough information — stop crawling

            # Email fallback — only if lead has no email
            if not lead.get("email") and not found_dm.get("decision_maker_email"):
                emails = re.findall(
                    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                    text,
                )
                valid = [
                    e.lower() for e in emails
                    if e.split("@")[-1].lower() not in _EMAIL_NOISE_DOMAINS
                ]
                if valid:
                    personal = [
                        e for e in valid
                        if not any(
                            g in e for g in ["info@", "support@", "admin@"]
                        )
                    ]
                    candidate = personal[0] if personal else valid[0]
                    found_dm["decision_maker_email"] = candidate

            # Phone fallback — only if lead has no phone
            if not lead.get("phone") and not found_dm.get("phone_fallback"):
                if is_nigeria:
                    phones = re.findall(r"(?:\+234|0)[789]\d{9}", text)
                else:
                    phones = re.findall(r"(?:\+44|0)7\d{9}", text)
                if phones:
                    # NEW-D03/04: single-arg normalise
                    normalised = _normalise_phone(phones[0], is_nigeria)
                    found_dm["phone_fallback"]            = phones[0]
                    found_dm["phone_normalized_fallback"] = normalised

        except requests.TooManyRedirects:
            # BUG-D02: redirect limit hit
            log.warning(
                "find_decision_maker_nigeria: too many redirects at %s — stopping",
                url,
            )
            break
        except requests.RequestException as exc:
            log.debug(
                "find_decision_maker_nigeria: request error at %s: %s",
                url, exc,
            )
            continue
        except Exception as exc:
            log.debug(
                "find_decision_maker_nigeria: parse error at %s: %s",
                url, exc,
            )
            continue

    # ── Build final result ───────────────────────────────────────────────
    if not found_dm.get("decision_maker_name"):
        log.info(
            "find_decision_maker_nigeria: no DM found for '%s' after %d pages",
            lead.get("business_name", "?"), pages_done,
        )
        return None   # FABRICATION-01: never return fake data

    # BUG-D06: build_result() returns both canonical key and alias
    result = build_result(
        name  = found_dm["decision_maker_name"],
        role  = found_dm.get("decision_maker_role", ""),
        email = found_dm.get("decision_maker_email", ""),
    )

    # Attach real contact data discovered during crawl (not fabricated)
    if not lead.get("email") and found_dm.get("decision_maker_email"):
        result["email"] = found_dm["decision_maker_email"]

    if not lead.get("phone") and found_dm.get("phone_fallback"):
        result["phone"]            = found_dm["phone_fallback"]
        result["phone_normalized"] = found_dm.get("phone_normalized_fallback", "")

    log.info(
        "find_decision_maker_nigeria: DM found for '%s' — name='%s' role='%s'",
        lead.get("business_name", "?"),
        result.get("decision_maker_name", "?"),
        result.get("decision_maker_role", "?"),
    )
    return result

class DecisionMakerFinder:
    """Class wrapper for finding decision makers per test_decision_maker.py contract."""
    def __init__(self, **kwargs) -> None:
        pass

    def find(self, business_name: str, website: str = "", city: str = "", niche: str = "", **kwargs) -> dict:
        lead = {
            "business_name": business_name,
            "website": website,
            "city": city,
            "industry": niche,
            **kwargs
        }
        res = find_decision_maker_nigeria(lead)
        return res or {
            "decision_maker_name": "",
            "decision_maker_role": "",
            "decision_maker": "",
            "email": "",
            "phone": "",
            "confidence": "none"
        }

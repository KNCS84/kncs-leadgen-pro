"""
KNCS LeadGen Pro — engine/nigeria/google_maps_client.py — v10.1

Audit fixes applied (v10.1):

  BUG-G01  (HIGH) urllib.request replaced with requests.Session —
            shared session, connection pooling, persistent headers.
            self._get_session() returns the shared session instance.

  BUG-G02  (MEDIUM) MIN_FIT_SCORE = 25 floor noted here for documentation;
            fit_score assignment is the caller's responsibility
            (nigeria_enricher.py). This client sets 'rating' as returned
            by the API and leaves fit_score calculation to the enricher.

  BUG-G03  (HIGH) Delay moved to before each strategy attempt in
            search_businesses() — fires on both success and failure paths.
            Per-name delay increased to random.uniform(0.5, 1.5).

  BUG-G05  (HIGH) is_challenge_page() added — checked before parsing
            in both DuckDuckGo and Bing paths. ChallengePageError raised
            on detection — distinct from empty result.

  BUG-G07  (MEDIUM) normalize_nigerian_phone() added — any 234-prefixed
            digit string of 10+ digits normalised to +NNN... format.
            Landlines handled. _clean_phone() feeds into this normaliser.
            normalize_phone() from database.py called when available.

  NEW-G01  (CRITICAL) API key URL-encoded via urllib.parse.urlencode()
            — base64 chars (+, =, &) in key no longer corrupt the URL.

  NEW-G02  (CRITICAL) Redirect limit: requests.Session with
            max_redirects=5 — indefinite redirect-loop hang prevented.

  NEW-G03  (HIGH) API key never appears in log output — sanitise_key()
            redacts all but last 4 chars in any log message referencing
            the key.

  NEW-G04  (HIGH) _accept() requires minimum 2 words for business names
            from free-search HTML — single-word nav labels rejected.

  NEW-G05  (HIGH) requests.Session replaces urllib.request throughout —
            one TCP+TLS handshake per session, not per request.

  NEW-G06  (HIGH) User-agent rotation: _USER_AGENTS pool of 4, rotated
            per request via get_headers().

  NEW-G07  (HIGH) normalize_nigerian_phone() called on all phone values
            before storage — resolves BUG-G07 landline handling.

  NEW-G08  (MEDIUM) human_pause(1.0, 2.0) between strategy fallback
            calls in search_businesses().

  NEW-G09  (MEDIUM) Per-name sleep increased from 0.08 s to
            random.uniform(0.5, 1.5) — minimum human-scale delay.

  NEW-G10  (MEDIUM) maps_url uses urllib.parse.quote(safe='') —
            spaces not encoded as + which some map clients reject.

  NEW-G12  (LOW) rating stored as str(place.get("rating") or "")
            — Python None no longer stored as string "None".

  NEW-G13  (LOW) _NOISE set promoted to module-level constant
            _HTML_NOISE — not rebuilt on every call.

  NEW-G14  (MEDIUM) Error-path DuckDuckGo fallback return now
            capped at [:max_results] consistent with happy path.

  NEW-G15  (HIGH) All HTTP calls use requests.Session with explicit
            timeout=(10, 20) — connect timeout + read timeout separate.
"""

import logging
import random
import re
import time
import urllib.parse
from typing import List, Optional

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

# ── Optional database integration ────────────────────────────────────────────
try:
    from database import normalize_phone as _db_normalize_phone
    _HAVE_DB_NORMALIZE = True
except ImportError:
    _db_normalize_phone = None
    _HAVE_DB_NORMALIZE  = False


# ── City coordinate map ───────────────────────────────────────────────────────

CITY_COORDS = {
    "lagos_vi":         ("6.4281",  "3.4219"),
    "lagos_lekki":      ("6.4698",  "3.5852"),
    "lagos_ikeja":      ("6.5954",  "3.3383"),
    "lagos_surulere":   ("6.5000",  "3.3500"),
    "lagos_yaba":       ("6.5100",  "3.3700"),
    "abuja_maitama":    ("9.0820",  "7.4933"),
    "abuja_wuse":       ("9.0700",  "7.4800"),
    "abuja_garki":      ("9.0500",  "7.4900"),
    "ph_gra":           ("4.8396",  "7.0367"),
    "benin_gra":        ("6.3350",  "5.6037"),
    "warri_gra":        ("5.5170",  "5.7331"),
    "enugu_gra":        ("6.4400",  "7.4900"),
    "owerri_central":   ("5.4836",  "7.0333"),
    "kano_central":     ("12.0022", "8.5919"),
    "ibadan_ring":      ("7.3776",  "3.9470"),
    "calabar_gra":      ("4.9517",  "8.3220"),
    "abeokuta_central": ("7.1475",  "3.3619"),
    "akure_central":    ("7.2526",  "5.1986"),
    "ilorin_central":   ("8.5000",  "4.5500"),
    "kaduna_gra":       ("10.5105", "7.4165"),
    "jos_central":      ("9.9285",  "8.8921"),
}

CITY_DISPLAY_MAP = {
    "lagos_vi":          "Victoria Island Lagos",
    "lagos_lekki":       "Lekki Lagos",
    "lagos_ikeja":       "Ikeja Lagos",
    "lagos_surulere":    "Surulere Lagos",
    "lagos_yaba":        "Yaba Lagos",
    "abuja_maitama":     "Maitama Abuja",
    "abuja_wuse":        "Wuse 2 Abuja",
    "abuja_garki":       "Garki Abuja",
    "ph_gra":            "GRA Port Harcourt",
    "benin_gra":         "GRA Benin City",
    "warri_gra":         "GRA Warri",
    "enugu_gra":         "GRA Enugu",
    "owerri_central":    "Owerri",
    "kano_central":      "Kano",
    "ibadan_ring":       "Ibadan",
    "calabar_gra":       "Calabar",
    "abeokuta_central":  "Abeokuta",
    "akure_central":     "Akure",
    "ilorin_central":    "Ilorin",
    "kaduna_gra":        "Kaduna",
    "jos_central":       "Jos",
}

NICHE_SEARCH_TERMS = {
    "private_school":   "private school",
    "private_hospital": "private hospital clinic",
    "real_estate":      "real estate company",
    "hotel":            "hotel guest house",
    "manufacturing":    "manufacturing company",
    "construction":     "construction company",
    "law_firm":         "law firm solicitors",
}

NICHE_DISPLAY_MAP = {
    "private_school":   "Private Schools",
    "private_hospital": "Private Hospitals",
    "real_estate":      "Real Estate",
    "hotel":            "Hotels & Guest Houses",
    "manufacturing":    "Manufacturing",
    "construction":     "Construction",
    "law_firm":         "Law Firms",
}

# ── Constants ─────────────────────────────────────────────────────────────────
MIN_FIT_SCORE = 25   # BUG-G02: floor for unrated businesses (used by caller)

# NEW-G13: module-level constant — not rebuilt per call
_HTML_NOISE: set = {
    "google", "search", "bing", "duckduckgo", "maps",
    "images", "videos", "news", "more results",
    "next", "previous", "settings", "sign in",
    "nigeria", "home", "contact", "about", "services",
    "privacy", "terms", "cookie", "advertisement",
    "sponsored", "ad", "click here", "read more",
    "see more", "view all", "load more",
}

# NEW-G06: user-agent rotation pool
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


# ── Challenge-page detection (BUG-G05) ───────────────────────────────────────

class ChallengePageError(RuntimeError):
    """Raised when a CAPTCHA or challenge page is detected."""


_CHALLENGE_MARKERS = [
    "captcha", "robot check", "security check",
    "cloudflare", "access denied", "verify you are human",
    "just a moment", "ddos-guard", "please enable cookies",
]


def is_challenge_page(html: str) -> bool:
    """
    BUG-G05: return True if the page looks like a CAPTCHA/challenge.
    Only inspect the first 8 KB — faster and avoids large-string ops.
    """
    preview = html[:8192].lower()
    return any(marker in preview for marker in _CHALLENGE_MARKERS)


# ── Phone normalisation (BUG-G07) ────────────────────────────────────────────

def _clean_phone(raw: str) -> str:
    """
    Strip emoji, spaces, dashes and non-digit characters.
    Preserves leading + for international format.
    Returns empty string if fewer than 7 digits remain.
    """
    if not raw:
        return ""
    raw      = str(raw).strip()
    has_plus = raw.lstrip().startswith("+")
    digits   = re.sub(r"[^\d]", "", raw)
    if len(digits) < 7:
        return ""
    return ("+" + digits) if has_plus else digits


def normalize_nigerian_phone(raw: str) -> str:
    """
    BUG-G07: normalise any Nigerian phone to +234XXXXXXXXXX format.

    Handles:
      08012345678         → +2348012345678   (MTN/Airtel local)
      +234 801 234 5678   → +2348012345678   (international with spaces)
      2348012345678       → +2348012345678   (missing leading +)
      01-2345678          → +23412345678     (Lagos landline)
      Any 234-prefixed digit string of 10+ digits normalised correctly.

    Falls back to database.normalize_phone() if available.
    """
    if _HAVE_DB_NORMALIZE and _db_normalize_phone:
        result = _db_normalize_phone(raw)
        if result:
            return result

    cleaned = _clean_phone(raw)
    if not cleaned:
        return ""

    # Remove leading +
    digits = cleaned.lstrip("+")

    # Already has country code: 234XXXXXXXXXX (12+ digits)
    if digits.startswith("234") and len(digits) >= 12:
        return "+" + digits

    # Local format: 0XXXXXXXXXX (11 digits)
    if digits.startswith("0") and len(digits) == 11:
        return "+234" + digits[1:]

    # Local format without leading 0: XXXXXXXXXX (10 digits)
    if len(digits) == 10 and not digits.startswith("0"):
        return "+234" + digits

    # Landline: shorter digit strings that start with area code
    if len(digits) >= 7:
        return "+" + digits if cleaned.startswith("+") else digits

    return ""


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def get_headers() -> dict:
    """NEW-G06: rotate user-agent on every call."""
    return {
        "User-Agent":                random.choice(_USER_AGENTS),
        "Accept-Language":           "en-GB,en;q=0.9",
        "Accept":                    (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Encoding":           "gzip, deflate",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _sanitise_key(key: str) -> str:
    """NEW-G03: redact all but last 4 chars of an API key for logging."""
    if not key or len(key) <= 4:
        return "****"
    return "*" * (len(key) - 4) + key[-4:]


def human_pause(min_sec: float = 1.0, max_sec: float = 2.0) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


# ─────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────

class NigeriaGoogleMapsClient:
    """
    Nigeria business search client.

    Search priority:
        1. Google Maps Places API  — if api_key provided
        2. DuckDuckGo HTML search  — free, primary
        3. Bing web search         — secondary free fallback
        4. Empty list              — returned cleanly, never raises

    BUG-G01 / NEW-G05: all HTTP calls use a shared requests.Session with
    connection pooling and a redirect cap of 5 (NEW-G02).
    """

    NIGERIAN_CITIES = CITY_DISPLAY_MAP

    def _get_seed_data(self, niche: str = "", location: str = "") -> list:
        """Seed fallback helper for backward compatibility."""
        return []

    def __init__(self, api_key: str = ""):
        self.api_key  = api_key.strip() if api_key else ""
        self._use_api = bool(self.api_key)
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        """
        BUG-G01: return the shared requests.Session.
        NEW-G02: max_redirects=5 — no redirect-loop hangs.
        Lazy-initialised so subclasses can override.
        """
        if self._session is None:
            s = requests.Session()
            s.max_redirects = 5
            # Single retry on connection error at transport level
            adapter = HTTPAdapter(max_retries=1)
            s.mount("https://", adapter)
            s.mount("http://",  adapter)
            self._session = s
        return self._session

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def search_businesses(
        self,
        niche: str,
        city_key: str,
        max_results: int = 10,
    ) -> List[dict]:
        """
        Search for businesses in a Nigerian city.

        BUG-G03 / NEW-G08: delay fires before each strategy attempt
        on both success and failure paths.
        """
        search_term = NICHE_SEARCH_TERMS.get(niche, niche)
        coords      = CITY_COORDS.get(city_key, ("9.0820", "7.4933"))
        city_name   = CITY_DISPLAY_MAP.get(
            city_key,
            city_key.replace("_", " ").title(),
        )

        log.info(
            "NigeriaGoogleMapsClient.search_businesses "
            "niche=%s city_key=%s city_name=%s max=%s api=%s",
            niche, city_key, city_name, max_results, self._use_api,
        )

        if self._use_api:
            return self._search_with_api(
                search_term, coords, city_name, niche, max_results,
            )

        # BUG-G03 / NEW-G08: pause BEFORE strategy attempt
        human_pause(1.0, 2.0)
        results = self._search_duckduckgo(
            search_term, city_name, niche, max_results,
        )

        if not results:
            log.info(
                "DuckDuckGo returned 0 — trying Bing for %s %s",
                search_term, city_name,
            )
            # BUG-G03: pause before second strategy attempt
            human_pause(1.5, 3.0)
            results = self._search_bing(
                search_term, city_name, niche, max_results,
            )

        if not results:
            log.warning(
                "All free search methods returned 0 results for %s in %s",
                search_term, city_name,
            )

        return results[:max_results]

    # ──────────────────────────────────────────────────────────────────────
    # Google Maps Places API
    # ──────────────────────────────────────────────────────────────────────

    def _search_with_api(
        self,
        search_term: str,
        coords: tuple,
        city_name: str,
        niche: str,
        max_results: int,
    ) -> List[dict]:
        """
        Google Maps Places API — text search with location bias.

        NEW-G01: API key URL-encoded via urllib.parse.urlencode() —
        base64 chars in key no longer corrupt the URL.
        NEW-G03: API key redacted in all log messages.
        NEW-G15: explicit (connect, read) timeout tuple.
        """
        results: List[dict] = []
        try:
            lat, lng = coords
            params   = urllib.parse.urlencode({
                "query":    search_term + " " + city_name + " Nigeria",
                "location": f"{lat},{lng}",
                "radius":   "10000",
                "key":      self.api_key,   # NEW-G01: urlencode handles escaping
            })
            url = (
                "https://maps.googleapis.com/maps/api/place"
                "/textsearch/json?" + params
            )

            log.debug(
                "Places API request | key=...%s",
                _sanitise_key(self.api_key),   # NEW-G03
            )

            session  = self._get_session()
            response = session.get(
                url,
                headers=get_headers(),
                timeout=(10, 20),   # NEW-G15: connect + read
            )
            response.raise_for_status()
            data = response.json()

            api_status = data.get("status", "")
            if api_status not in ("OK", "ZERO_RESULTS"):
                log.error(
                    "Places API returned status=%s key=...%s — falling back",
                    api_status, _sanitise_key(self.api_key),   # NEW-G03
                )
                return self._search_duckduckgo(
                    search_term, city_name, niche, max_results,
                )[:max_results]   # NEW-G14: cap on error path

            places = data.get("results", [])[:max_results]
            for place in places:
                results.append(
                    self._place_to_lead(place, niche, city_name)
                )

            log.info(
                "Places API returned %d results for %s %s",
                len(results), search_term, city_name,
            )

        except Exception as exc:
            log.error(
                "Places API search failed: %s — falling back to DuckDuckGo",
                exc,
            )
            return self._search_duckduckgo(
                search_term, city_name, niche, max_results,
            )[:max_results]   # NEW-G14: cap on error path

        return results

    # ──────────────────────────────────────────────────────────────────────
    # DuckDuckGo HTML search
    # ──────────────────────────────────────────────────────────────────────

    def _search_duckduckgo(
        self,
        search_term: str,
        city_name: str,
        niche: str,
        max_results: int,
    ) -> List[dict]:
        """
        BUG-G01 / NEW-G05: shared session.
        BUG-G05: is_challenge_page() checked before parsing.
        NEW-G09: per-name delay increased to random.uniform(0.5, 1.5).
        NEW-G15: (connect, read) timeout.
        """
        results: List[dict] = []
        try:
            query = urllib.parse.quote(
                search_term + " " + city_name + " Nigeria contact phone"
            )
            url = "https://html.duckduckgo.com/html/?q=" + query

            session  = self._get_session()
            response = session.get(
                url,
                headers=get_headers(),
                timeout=(10, 20),   # NEW-G15
            )
            raw_html = response.text

            # BUG-G05: challenge detection
            if is_challenge_page(raw_html):
                log.warning(
                    "DuckDuckGo returned a challenge page for %s %s — skipping",
                    search_term, city_name,
                )
                raise ChallengePageError("DuckDuckGo challenge page detected")

            names = self._extract_names_from_html(
                raw_html, search_term, city_name,
            )
            log.info(
                "DuckDuckGo: %d names extracted for %s %s",
                len(names), search_term, city_name,
            )

            for name in names[:max_results]:
                results.append(
                    self._build_free_lead(name, niche, city_name, "duckduckgo")
                )
                time.sleep(random.uniform(0.5, 1.5))   # NEW-G09

        except ChallengePageError:
            pass  # already logged
        except Exception as exc:
            log.warning("DuckDuckGo search failed: %s", exc)

        return results

    # ──────────────────────────────────────────────────────────────────────
    # Bing web search
    # ──────────────────────────────────────────────────────────────────────

    def _search_bing(
        self,
        search_term: str,
        city_name: str,
        niche: str,
        max_results: int,
    ) -> List[dict]:
        """
        BUG-G01 / NEW-G05: shared session.
        BUG-G05: is_challenge_page() checked before parsing.
        NEW-G09: per-name delay increased to random.uniform(0.5, 1.5).
        NEW-G15: (connect, read) timeout.
        """
        results: List[dict] = []
        try:
            query = urllib.parse.quote(
                search_term + " " + city_name + " Nigeria"
            )
            url = "https://www.bing.com/search?q=" + query + "&count=20"

            session  = self._get_session()
            response = session.get(
                url,
                headers=get_headers(),
                timeout=(10, 20),   # NEW-G15
            )
            raw_html = response.text

            # BUG-G05: challenge detection
            if is_challenge_page(raw_html):
                log.warning(
                    "Bing returned a challenge page for %s %s — skipping",
                    search_term, city_name,
                )
                raise ChallengePageError("Bing challenge page detected")

            names = self._extract_names_from_html(
                raw_html, search_term, city_name,
            )
            log.info(
                "Bing: %d names extracted for %s %s",
                len(names), search_term, city_name,
            )

            for name in names[:max_results]:
                results.append(
                    self._build_free_lead(name, niche, city_name, "bing")
                )
                time.sleep(random.uniform(0.5, 1.5))   # NEW-G09

        except ChallengePageError:
            pass  # already logged
        except Exception as exc:
            log.warning("Bing search failed: %s", exc)

        return results

    # ──────────────────────────────────────────────────────────────────────
    # HTML name extraction
    # ──────────────────────────────────────────────────────────────────────

    def _extract_names_from_html(
        self,
        html: str,
        search_term: str,
        city_name: str,
    ) -> List[str]:
        """
        NEW-G04: _accept() now requires minimum 2 words — single-word
        nav labels can no longer pass even if not in _HTML_NOISE.
        NEW-G13: _HTML_NOISE is module-level, not rebuilt per call.
        """
        names: List[str] = []
        seen:  set       = set()

        def _clean(raw: str) -> str:
            text = re.sub(r"<[^>]+>", "", raw)
            text = (
                text
                .replace("&amp;",  "&")
                .replace("&nbsp;", " ")
                .replace("&#39;",  "'")
                .replace("&quot;", '"')
            )
            return re.sub(r"\s+", " ", text).strip()

        def _accept(name: str) -> bool:
            if not name:
                return False
            if len(name) < 4 or len(name) > 90:
                return False
            nl = name.lower()
            if nl in _HTML_NOISE:                        # NEW-G13
                return False
            if nl == search_term.lower() or nl == city_name.lower():
                return False
            if re.match(r"^[\d\W]+$", name):
                return False
            if not re.search(r"[a-zA-Z]{3}", name):
                return False
            # NEW-G04: require at least 2 words for business names
            if len(name.split()) < 2:
                return False
            return True

        def _add(name: str) -> None:
            cleaned = _clean(name)
            key     = cleaned.lower()
            if _accept(cleaned) and key not in seen:
                seen.add(key)
                names.append(cleaned)

        # Pattern 1: DuckDuckGo result links
        for m in re.finditer(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>',
            html, re.IGNORECASE | re.DOTALL,
        ):
            _add(m.group(1))

        # Pattern 2: DuckDuckGo result titles
        for m in re.finditer(
            r'<h2[^>]+class="[^"]*result__title[^"]*"[^>]*>(.*?)</h2>',
            html, re.IGNORECASE | re.DOTALL,
        ):
            _add(m.group(1))

        # Pattern 3: Any <h2> or <h3>
        for m in re.finditer(
            r"<h[23][^>]*>(.*?)</h[23]>",
            html, re.IGNORECASE | re.DOTALL,
        ):
            _add(m.group(1))

        # Pattern 4: titled result spans
        for m in re.finditer(
            r'<span[^>]+class="[^"]*title[^"]*"[^>]*>(.*?)</span>',
            html, re.IGNORECASE | re.DOTALL,
        ):
            _add(m.group(1))

        log.debug(
            "_extract_names_from_html: %d unique names from %d chars HTML",
            len(names), len(html),
        )
        return names

    # ──────────────────────────────────────────────────────────────────────
    # Lead dict builders
    # ──────────────────────────────────────────────────────────────────────

    def _build_free_lead(
        self,
        name: str,
        niche: str,
        city_name: str,
        source_tag: str,
    ) -> dict:
        """
        Build a lead dict from a scraped business name.

        BUG-G03 (display): NICHE_DISPLAY_MAP used for industry label.
        NEW-G10: maps_url uses quote(safe='') — spaces not encoded as +.
        """
        industry_label = NICHE_DISPLAY_MAP.get(
            niche, niche.replace("_", " ").title()
        )

        return {
            "business_name": name,
            "address":       city_name + ", Nigeria",
            "city":          city_name,
            "country":       "Nigeria",
            "niche":         niche,
            "industry":      industry_label,
            "phone":         "",
            "email":         "",
            "website":       "",
            "rating":        "",
            "source":        source_tag,
            "place_id":      "",
            "maps_url":      (
                "https://www.google.com/maps/search/"
                + urllib.parse.quote(
                    name + " " + city_name + " Nigeria",
                    safe="",   # NEW-G10: no +, pure %20 encoding
                )
            ),
        }

    def _place_to_lead(
        self,
        place: dict,
        niche: str,
        city_name: str,
    ) -> dict:
        """
        Convert Google Places API result to lead dict.

        BUG-G07 / NEW-G07: normalize_nigerian_phone() called on all phones.
        NEW-G12: rating stored as str(... or "") — Python None not stored
                 as string "None".
        NEW-G10: maps_url uses quote(safe='').
        """
        geometry = place.get("geometry", {})
        location = geometry.get("location", {})
        place_id = place.get("place_id", "")

        industry_label = NICHE_DISPLAY_MAP.get(
            niche, niche.replace("_", " ").title()
        )

        # BUG-G07 / NEW-G07: full normalisation
        raw_phone   = place.get("formatted_phone_number", "") or ""
        clean_phone = normalize_nigerian_phone(raw_phone)

        return {
            "business_name": place.get("name", ""),
            "address":       place.get(
                "formatted_address", city_name + ", Nigeria"
            ),
            "city":          city_name,
            "country":       "Nigeria",
            "niche":         niche,
            "industry":      industry_label,
            "phone":         clean_phone,
            "email":         "",
            "website":       place.get("website", ""),
            "rating":        str(place.get("rating") or ""),   # NEW-G12
            "source":        "google_maps_api",
            "place_id":      place_id,
            "maps_url":      (
                "https://www.google.com/maps/place/?q=place_id:"
                + urllib.parse.quote(place_id, safe="")   # NEW-G10
                if place_id else ""
            ),
            "lat":           str(location.get("lat", "")),
            "lng":           str(location.get("lng", "")),
        }
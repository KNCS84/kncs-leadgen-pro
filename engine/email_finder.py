# engine/email_finder.py
# KNCS LeadGen Pro — Personal Email Discovery
# v3.3
#
# CHANGES FROM v3.2:
#
#   FIX-NO-WEBSITE: New _find_domain_for_care_home() function.
#     When CQC provides no website URL, we now search for the
#     domain using:
#       1. Google/Bing search for care home name + location
#       2. Extract domain from first relevant result
#     This means homes with no CQC website still get emails.
#
#   FIX-CACHE: New in-memory domain cache.
#     Same domain looked up once per session only.
#
#   All v3.2 fixes retained:
#     - DDG rate limit hardened
#     - pattern_guess guaranteed return
#     - SMTP disabled by default

import json
import logging
import os
import re
import smtplib
import socket
import subprocess
import urllib.request
import urllib.parse
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Environment ───────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
HUNTER_BASE    = "https://api.hunter.io/v2"

_SMTP_ENABLED = os.getenv("SMTP_VERIFY", "0").strip() == "1"
_SMTP_TIMEOUT = 6

_SKIP_SMTP_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com",
    "outlook.com", "live.com", "icloud.com",
}

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

_GENERIC_PREFIXES = (
    "info@", "admin@", "contact@", "hello@",
    "noreply@", "no-reply@", "enquiries@",
    "enquiry@", "reception@", "office@",
    "mail@", "post@", "support@",
)

# Domains that are NOT care home websites
_REJECT_DOMAINS = {
    "cqc.org.uk", "google.com", "google.co.uk",
    "bing.com", "facebook.com", "twitter.com",
    "linkedin.com", "youtube.com", "gov.uk",
    "nhs.uk", "carehome.co.uk", "carehomes.co.uk",
    "yell.com", "192.com", "companieshouse.gov.uk",
    "indeed.com", "reed.co.uk", "totaljobs.com",
}

# In-memory domain cache — avoids repeated lookups
_domain_cache: dict = {}


# ─────────────────────────────────────────────────────────
# Hunter.io
# ─────────────────────────────────────────────────────────

def _hunter_key_is_real() -> bool:
    key = HUNTER_API_KEY.strip()
    if not key:
        return False
    if "hunter.io" in key.lower():
        return False
    if len(key) < 20:
        return False
    if " " in key:
        return False
    return True


def _hunter_find_email(
    first_name: str,
    last_name:  str,
    domain:     str,
) -> str:
    if not _hunter_key_is_real():
        return ""
    if not all([first_name, last_name, domain]):
        return ""
    try:
        params = urllib.parse.urlencode({
            "domain":     domain,
            "first_name": first_name,
            "last_name":  last_name,
            "api_key":    HUNTER_API_KEY,
        })
        url = f"{HUNTER_BASE}/email-finder?{params}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                data  = json.loads(resp.read().decode())
                email = (
                    data.get("data", {})
                        .get("email", "") or ""
                ).strip().lower()
                score = (
                    data.get("data", {})
                        .get("score", 0) or 0
                )
                if email and score >= 50:
                    return email
    except Exception as exc:
        logger.debug("Hunter API error: %s", exc)
    return ""


# ─────────────────────────────────────────────────────────
# Domain extraction from URL
# ─────────────────────────────────────────────────────────

def _get_domain_from_url(website_url: str) -> str:
    if not website_url:
        return ""
    try:
        url = website_url.strip()
        if not url.startswith("http"):
            url = "https://" + url
        parsed = urllib.parse.urlparse(url)
        host   = parsed.netloc or parsed.path
        if host.startswith("www."):
            host = host[4:]
        host = host.split(":")[0]
        return host.lower().strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────
# NEW v3.3: Find domain when no website in CQC
# ─────────────────────────────────────────────────────────

def _find_domain_for_care_home(
    home_name: str,
    postcode:  str = "",
    city:      str = "",
) -> str:
    """
    Search for a care home website when CQC has no URL.

    Uses DuckDuckGo HTML search to find the care home's
    own website. Extracts the domain from search results.

    Returns domain string e.g. "gallaudethouse.co.uk"
    or "" if not found. Never raises.
    """
    if not home_name:
        return ""

    # Check cache first
    cache_key = home_name.lower().strip()
    if cache_key in _domain_cache:
        return _domain_cache[cache_key]

    location_hint = postcode or city or "UK"
    query = f'"{home_name}" care home {location_hint} official website'

    try:
        q   = urllib.parse.quote(query)
        url = f"https://duckduckgo.com/html/?q={q}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read(150_000).decode(
                "utf-8", errors="ignore"
            )

        # Extract URLs from search results
        url_pattern = re.compile(
            r'href=["\']?(https?://[^"\'>\s]+)',
            re.IGNORECASE,
        )
        found_urls = url_pattern.findall(html)

        for found_url in found_urls:
            try:
                parsed = urllib.parse.urlparse(found_url)
                host   = parsed.netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                if not host:
                    continue

                # Skip known non-care-home domains
                if any(
                    host == rd or host.endswith("." + rd)
                    for rd in _REJECT_DOMAINS
                ):
                    continue

                # Skip very short or generic domains
                if len(host) < 6:
                    continue

                # Prefer .co.uk or .org.uk (UK care homes)
                if host.endswith((".co.uk", ".org.uk", ".com")):
                    logger.info(
                        "_find_domain_for_care_home: "
                        "'%s' → %s", home_name, host,
                    )
                    _domain_cache[cache_key] = host
                    return host

            except Exception:
                continue

    except Exception as exc:
        err = str(exc).lower()
        if "ratelimit" in err or "202" in err or "429" in err:
            logger.debug(
                "_find_domain_for_care_home: DDG rate limited"
            )
        else:
            logger.debug(
                "_find_domain_for_care_home(%s): %s",
                home_name, exc,
            )

    _domain_cache[cache_key] = ""
    return ""


# ─────────────────────────────────────────────────────────
# Name parsing
# ─────────────────────────────────────────────────────────

def _name_to_parts(full_name: str) -> Tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[-1]


# ─────────────────────────────────────────────────────────
# Pattern generation
# ─────────────────────────────────────────────────────────

def _generate_email_patterns(
    first:  str,
    last:   str,
    domain: str,
) -> List[str]:
    f  = first.lower().strip()
    l  = last.lower().strip()
    d  = domain.lower().strip()
    fi = f[0] if f else ""

    if not f or not l or not d:
        return []

    return [
        f"{f}.{l}@{d}",
        f"{fi}.{l}@{d}",
        f"{f}@{d}",
        f"{f}{l}@{d}",
        f"{f}_{l}@{d}",
        f"{l}@{d}",
        f"{fi}{l}@{d}",
        f"{f}.{l[0]}@{d}",
    ]


# ─────────────────────────────────────────────────────────
# MX lookup
# ─────────────────────────────────────────────────────────

def _get_mx_server(domain: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["nslookup", "-type=MX", domain],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            ll = line.strip().lower()
            if "mail exchanger" in ll or (
                "mx" in ll and "=" in ll
            ):
                parts = line.strip().split()
                if parts:
                    candidate = parts[-1].rstrip(".")
                    if "." in candidate and len(candidate) > 3:
                        return candidate
    except Exception:
        pass
    for host in [f"mail.{domain}", f"smtp.{domain}", domain]:
        try:
            socket.getaddrinfo(host, 25, socket.AF_INET)
            return host
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────
# SMTP
# ─────────────────────────────────────────────────────────

def _smtp_verify_email(email: str, domain: str) -> bool:
    if not _SMTP_ENABLED:
        return False
    if not email or not domain:
        return False
    if domain in _SKIP_SMTP_DOMAINS:
        return False
    mx_host = _get_mx_server(domain)
    if not mx_host:
        return False

    def _try(host: str, port: int) -> bool:
        try:
            with smtplib.SMTP(
                host=host, port=port, timeout=_SMTP_TIMEOUT
            ) as smtp:
                smtp.ehlo("kncs.co.uk")
                code, _ = smtp.mail("verify@kncs.co.uk")
                if code != 250:
                    return False
                code, _ = smtp.rcpt(email)
                return code in (250, 251)
        except Exception:
            return False

    return _try(mx_host, 25) or _try(mx_host, 587)


def _detect_catch_all(domain: str) -> bool:
    if not _SMTP_ENABLED:
        return False
    import random
    import string
    ru = "".join(
        random.choices(
            string.ascii_lowercase + string.digits, k=24
        )
    )
    return _smtp_verify_email(f"{ru}@{domain}", domain)


# ─────────────────────────────────────────────────────────
# DuckDuckGo verify
# ─────────────────────────────────────────────────────────

def _ddg_verify_email(email: str) -> bool:
    if not email:
        return False
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(
                ddgs.text(f'"{email}"', max_results=3)
            )
        if results:
            logger.info("DDG verified: %s", email)
            return True
        return False
    except ImportError:
        pass
    except Exception as exc:
        err = str(exc).lower()
        if any(x in err for x in (
            "ratelimit", "202", "429",
            "rate limit", "too many", "stopping",
        )):
            logger.debug("DDG rate limited for %s", email)
        else:
            logger.debug("DDG error: %s", exc)
        return False

    try:
        q   = urllib.parse.quote(f'"{email}"')
        url = f"https://duckduckgo.com/html/?q={q}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                    " AppleWebKit/537.36 Chrome/120.0.0.0"
                ),
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read(80_000).decode(
                "utf-8", errors="ignore"
            )
        if email.lower() in text.lower():
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────
# CQC report PDF mining
# ─────────────────────────────────────────────────────────

def _mine_cqc_report_email(
    location_id:  str,
    manager_name: str = "",
) -> str:
    if not location_id:
        return ""
    try:
        import pdfplumber
    except ImportError:
        return ""

    import tempfile

    try:
        url = (
            f"https://api.cqc.org.uk/public/v1/"
            f"reports/{location_id}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "KNCS-LeadGen/3.3",
                "Accept":     "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return ""
            data = json.loads(resp.read().decode())

        reports = data.get("reports", [])
        if not reports:
            return ""

        latest  = reports[-1]
        link_id = (
            latest.get("linkId", "")
            or latest.get("reportUri", "")
            or ""
        )
        if not link_id:
            return ""

        pdf_url = (
            link_id if link_id.startswith("http")
            else (
                f"https://api.cqc.org.uk/public/v1/"
                f"reports/{link_id}/download"
            )
        )

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".pdf", delete=False
            ) as tmp:
                tmp_path = tmp.name

            urllib.request.urlretrieve(pdf_url, tmp_path)

            found_email  = ""
            manager_last = ""
            if manager_name:
                parts = manager_name.strip().split()
                if parts:
                    manager_last = parts[-1].lower()

            with pdfplumber.open(tmp_path) as pdf:
                for page in pdf.pages[:8]:
                    text   = page.extract_text() or ""
                    emails = _EMAIL_RE.findall(text)
                    for em in emails:
                        em = em.lower().strip()
                        if em.endswith((
                            ".png", ".jpg", ".gif",
                            ".svg", ".css", ".js",
                        )):
                            continue
                        if len(em) > 80:
                            continue
                        if any(
                            em.startswith(p)
                            for p in _GENERIC_PREFIXES
                        ):
                            continue
                        if manager_last and manager_last in em:
                            found_email = em
                            break
                        if not found_email:
                            found_email = em
                    if (
                        found_email and manager_last
                        and manager_last in found_email
                    ):
                        break

            return found_email

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    except Exception as exc:
        logger.debug(
            "_mine_cqc_report_email(%s): %s", location_id, exc
        )
        return ""


# ─────────────────────────────────────────────────────────
# Public API — simple
# ─────────────────────────────────────────────────────────

def find_personal_email(
    full_name:   str,
    website_url: str = "",
    domain:      str = "",
    location_id: str = "",
    home_name:   str = "",
    postcode:    str = "",
    city:        str = "",
) -> str:
    email, _ = find_personal_email_with_source(
        full_name   = full_name,
        website_url = website_url,
        domain      = domain,
        location_id = location_id,
        home_name   = home_name,
        postcode    = postcode,
        city        = city,
    )
    return email


# ─────────────────────────────────────────────────────────
# Public API — with source
# ─────────────────────────────────────────────────────────

def find_personal_email_with_source(
    full_name:   str,
    website_url: str = "",
    domain:      str = "",
    location_id: str = "",
    home_name:   str = "",
    postcode:    str = "",
    city:        str = "",
) -> Tuple[str, str]:
    """
    v3.3 — Finds email even when CQC has no website.

    Pipeline:
        1. Hunter.io (if key)          → "hunter"
        2. CQC PDF mining              → "cqc_report"
        3. Resolve domain:
           a. From website_url (CQC)
           b. Search web for home name → find domain
        4. Generate patterns
        5. SMTP verify (if enabled)    → "smtp_verified"
        6. DDG verify                  → "ddg_verified"
        7. pattern_guess               → "pattern_guess"

    Returns ("", "") only if:
        - Name cannot be parsed (single word)
        - No domain found AND web search fails
    """
    if not full_name or not full_name.strip():
        return "", ""

    # Parse name first — fail fast if unparseable
    first, last = _name_to_parts(full_name)
    if not first or not last:
        logger.debug(
            "find_personal_email: cannot parse '%s'",
            full_name,
        )
        return "", ""

    # 1. Hunter.io
    try:
        if _hunter_key_is_real():
            # Need domain for Hunter — try to get it first
            tmp_domain = domain.strip()
            if not tmp_domain and website_url:
                tmp_domain = _get_domain_from_url(website_url)
            if tmp_domain:
                result = _hunter_find_email(
                    first, last, tmp_domain
                )
                if result:
                    return result, "hunter"
    except Exception:
        pass

    # 2. CQC PDF mining — does not need domain
    try:
        if location_id:
            report_email = _mine_cqc_report_email(
                location_id  = location_id,
                manager_name = full_name,
            )
            if report_email:
                return report_email, "cqc_report"
    except Exception:
        pass

    # 3. Resolve domain
    email_domain = domain.strip()

    # 3a. From CQC website URL
    if not email_domain and website_url:
        email_domain = _get_domain_from_url(website_url)
        if email_domain:
            logger.debug(
                "Domain from CQC website: %s", email_domain
            )

    # 3b. Search web for care home website
    if not email_domain and home_name:
        logger.info(
            "No CQC website for '%s' — searching web",
            home_name,
        )
        email_domain = _find_domain_for_care_home(
            home_name = home_name,
            postcode  = postcode,
            city      = city,
        )
        if email_domain:
            logger.info(
                "Domain found via web search: %s → %s",
                home_name, email_domain,
            )

    if not email_domain:
        logger.debug(
            "find_personal_email: no domain found for '%s'",
            full_name,
        )
        return "", ""

    logger.info(
        "find_personal_email: %s %s @ %s",
        first, last, email_domain,
    )

    # 4. Generate patterns
    patterns = _generate_email_patterns(
        first, last, email_domain
    )
    if not patterns:
        return "", ""

    best_guess = patterns[0]  # firstname.lastname@domain

    # 5. SMTP (only if SMTP_VERIFY=1)
    try:
        if _SMTP_ENABLED:
            is_catch_all = _detect_catch_all(email_domain)
            if not is_catch_all:
                for pattern in patterns:
                    if _smtp_verify_email(
                        pattern, email_domain
                    ):
                        return pattern, "smtp_verified"
    except Exception as exc:
        logger.debug("SMTP block: %s", exc)

    # 6. DDG verify best guess
    try:
        if _ddg_verify_email(best_guess):
            return best_guess, "ddg_verified"
    except Exception as exc:
        logger.debug("DDG verify (ignored): %s", exc)

    # 7. GUARANTEED pattern_guess
    logger.info(
        "find_personal_email: pattern_guess → %s", best_guess
    )
    return best_guess, "pattern_guess"
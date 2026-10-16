# deep_research.py
# KNCS LeadGen Pro v10.11 — Deep Research Script
#
# Changes vs v10.10:
#   FIX-DR-31: City-first website search when CH postcode
#              does not match the lead's operating city.
#              find_website() now accepts ch_postcode as a
#              separate parameter. City queries always run
#              first. CH postcode used only as last-resort
#              fallback when city searches return nothing.
#              Fixes: Derby Cottage Clinic and all other
#              leads registered at out-of-city CH addresses.
#
#   FIX-DR-27: Email domain must match website domain.
#              _email_domain_matches_website() added.
#              _crawl_for_email() rejects emails from third-party
#              domains embedded in page assets.
#              _search_for_email() enforces domain match when
#              website is already known.
#
#   FIX-DR-28: Added to BLOCKED_EMAIL_DOMAINS:
#              mysite.com, wix.com, squarespace.com, weebly.com,
#              latofonts.com, fannoxnetwork.com, agcaregroup.com
#              Added .edu TLD block in _is_valid_email()
#
#   FIX-DR-29: Added _is_junk_dm_name() — rejects website
#              navigation text, company names, and placeholder
#              values stored as decision maker names.
#              Applied to Companies House results before writing.
#
#   FIX-DR-30: Main query now filters to UK-only leads.
#              Nigerian, Benin, and other non-UK leads are skipped
#              entirely — they cannot produce valid UK data and
#              waste SerpApi searches.
#              Filter: country must be NULL, empty, or one of
#              United Kingdom / UK / England / Wales / Scotland.
#
# Run with: python deep_research.py

import sys
import os
import re
import time
import random
import requests
from urllib.parse import urlparse, urljoin
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

from database import get_connection
from engine.enricher import (
    is_junk_email,
    is_foreign_email,
    clean_decision_maker_name,
    crawl_page,
    extract_emails_from_mailto_links,
    extract_emails_from_html,
    extract_phones_from_tel_links,
    extract_phones_from_html,
    extract_from_json_ld,
    get_headers,
    human_pause,
    is_bad_domain,
    normalize_domain,
    score_domain_relevance,
    business_name_tokens,
    _validate_website_url,
)

# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────

CH_API_KEY        = os.getenv("COMPANIES_HOUSE_API_KEY", "")
CH_AVAILABLE      = bool(CH_API_KEY)
SERPAPI_KEY       = os.getenv("SERPAPI_KEY", "")
SERPAPI_AVAILABLE = bool(SERPAPI_KEY)
LIMIT             = 30

# ─────────────────────────────────────────────────────────
# Blocked email domains
# FIX-DR-24: consumer, NHS, institutional
# FIX-DR-28: placeholder builders, asset domains
# ─────────────────────────────────────────────────────────

BLOCKED_EMAIL_DOMAINS = {
    # Consumer webmail
    "gmail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "hotmail.com",
    "hotmail.co.uk",
    "outlook.com",
    "outlook.co.uk",
    "icloud.com",
    "me.com",
    "live.com",
    "live.co.uk",
    "msn.com",
    "btinternet.com",
    "sky.com",
    "talktalk.net",
    "virginmedia.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    # NHS
    "nhs.net",
    "nhs.uk",
    # Placeholder / test / website builders
    "email.com",
    "example.com",
    "test.com",
    "mysite.com",
    "wix.com",
    "squarespace.com",
    "weebly.com",
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "throwam.com",
    "sharklasers.com",
    # Known directories used as email domains
    "wheree.com",
    "cylex.co.uk",
    "cylex-uk.co.uk",
    "yell.com",
    # Font / asset / platform domains found in page source
    "latofonts.com",
    "fannoxnetwork.com",
    "agcaregroup.com",
    "clinicia.com",       # booking platform
    "viva-birmingham.portals.com",
}

# Blocked local parts — role addresses, not decision maker contacts
BLOCKED_EMAIL_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "myname",
    "yourname",
    "example",
    "test",
    "postmaster",
    "mailer-daemon",
    "bounce",
    "bounces",
}

# ─────────────────────────────────────────────────────────
# Junk website patterns
# ─────────────────────────────────────────────────────────

JUNK_WEBSITE_PATTERNS = [
    # Dental portal platforms — not the practice's own website
    "portal.dental",
    "portals.com",
    # Search engines
    "bingj.com", "bingj.net",
    "googleusercontent.com", "gstatic.com",
    "bing.com", "google.com", "yahoo.com",
    "duckduckgo.com",
    # Directories and aggregators
    "yell.com", "yelp.com",
    "checkatrade.com", "trustpilot.com",
    "companies-uk.com", "companycheck.co.uk",
    "opencorporates.com", "companieshouse.gov.uk",
    "find-and-update.company-information.service.gov.uk",
    "endole.co.uk", "duedil.com",
    "192.com", "whitepages.com",
    "cylex-uk.co.uk", "business-dir.co.uk",
    "cylex.co.uk", "hotfrog.co.uk",
    "brownbook.net", "freeindex.co.uk",
    "thomsonlocal.com", "scoot.co.uk",
    "bark.com", "rated.people.com",
    "treatwell.co.uk", "booksy.com",
    "fresha.com", "vagaro.com",
    # Booking / consent platforms
    "facesconsent.com",
    # Financial adviser directories
    "financialadvisers.co.uk",
    "unbiased.co.uk",
    "vouchedfor.co.uk",
    "comparefinancialmarkets.com",
    # Aggregators with fake subdomains
    "wheree.com",
    # Social media
    "facebook.com", "instagram.com",
    "twitter.com", "linkedin.com",
    "tiktok.com", "youtube.com",
    # NHS and government
    "nhs.uk", "nhs.net",
    "gov.uk", "legislation.gov.uk",
    # National chains
    "laserclinics.co.uk",
    "sknclinics.co.uk",
    "reassure.co.uk",
    # Universities
    "birmingham.ac.uk",
    "le.ac.uk",
    "bham.ac.uk",
    # Website builders
    "mysite.com", "wix.com",
    "squarespace.com", "weebly.com",
    # Nigerian aggregator
    "nigeriaprivateschools.com",
]

# Foreign TLDs — reject for UK leads
FOREIGN_WEBSITE_TLDS = {
    ".fr", ".de", ".es", ".it", ".nl", ".pl", ".ru",
    ".cn", ".gr", ".pt", ".ro", ".hu", ".sk", ".hr",
    ".bg", ".lt", ".lv", ".ee", ".ua", ".tr", ".rs",
    ".si", ".at", ".se", ".no", ".dk", ".fi", ".cz",
    ".ch", ".au", ".com.au", ".nz", ".co.nz",
    ".ca", ".us", ".ie",
}

# Generic domains shared across many different businesses
GENERIC_DOMAINS = {
    "financialadvisers.co.uk",
    "godivafinancialplanning.co.uk",
    "circlefinancial.co.uk",
    "unbiased.co.uk",
    "vouchedfor.co.uk",
    "comparefinancialmarkets.com",
    "theadvisercentre.co.uk",
    "simplifybusiness.co.uk",
    "nigeriaprivateschools.com",
}

# Junk email local words — role addresses, not decision maker contacts
_JUNK_EMAIL_LOCAL_WORDS = {
    "limited", "ltd", "llp", "plc", "lp",
    "info", "contact", "hello", "admin",
    "enquiries", "enquiry", "noreply", "no-reply",
    "support", "sales", "accounts", "billing",
    "office", "reception", "mail", "post",
    "webmaster", "donotreply",
}

# Score thresholds — FIX-DR-25
_MIN_WEBSITE_SCORE        = 60
_MIN_WEBSITE_SCORE_NONUK  = 80
_MIN_TOKEN_LENGTH         = 5
_MIN_TOKEN_LENGTH_COUK    = 4

# Minimum website score to attempt email construction — FIX-DR-26
_MIN_SCORE_FOR_CONSTRUCTION = 80

# UK country values accepted in the leads table — FIX-DR-30
_UK_COUNTRY_VALUES = {
    "", None,
    "united kingdom", "uk",
    "england", "wales",
    "scotland", "northern ireland",
    "great britain", "gb",
}


# ─────────────────────────────────────────────────────────
# SerpApi
# ─────────────────────────────────────────────────────────

def _serp_search(query: str, num: int = 10) -> list:
    if not SERPAPI_AVAILABLE:
        return []
    try:
        from serpapi import GoogleSearch
        params = {
            "q":       query,
            "hl":      "en",
            "gl":      "uk",
            "num":     num,
            "api_key": SERPAPI_KEY,
        }
        search  = GoogleSearch(params)
        results = search.get_dict()
        return results.get("organic_results", [])
    except Exception as exc:
        print(f"    SerpApi error: {exc}")
        return []


# ─────────────────────────────────────────────────────────
# UK Phone Validation
# ─────────────────────────────────────────────────────────

_VALID_MOBILE_PREFIXES = set(range(740, 800))
_VALID_08_PREFIXES     = {"800", "808"}


def _is_valid_uk_phone(phone: str) -> bool:
    if not phone or not isinstance(phone, str):
        return False
    cleaned = re.sub(r'[\s\-\.\(\)]', '', phone.strip())
    if cleaned.startswith('+44'):
        cleaned = '0' + cleaned[3:]
    if not cleaned.startswith('0'):
        return False
    digits = re.sub(r'\D', '', cleaned)
    if len(digits) < 10 or len(digits) > 11:
        return False
    if digits.startswith('09'):
        return False
    if digits.startswith('084') or digits.startswith('087'):
        return False
    if digits.startswith('081') or digits.startswith('082'):
        return False
    if digits.startswith('07'):
        if len(digits) != 11:
            return False
        if digits.startswith('070'):
            return False
        mobile_prefix = int(digits[1:4])
        if mobile_prefix not in _VALID_MOBILE_PREFIXES:
            return False
        return True
    if digits.startswith('02'):
        return len(digits) == 11
    if digits.startswith('01'):
        return len(digits) in (10, 11)
    if digits.startswith('03'):
        return len(digits) == 11
    if digits.startswith('08'):
        three_digit = digits[1:4]
        if three_digit not in _VALID_08_PREFIXES:
            return False
        return len(digits) == 11
    return False


# ─────────────────────────────────────────────────────────
# Email Validation
# ─────────────────────────────────────────────────────────

_RFC5321_LOCAL = re.compile(r'^[a-zA-Z0-9._%+\-]+$')


def _is_valid_email(email: str) -> bool:
    """
    Gates every email write to the database.
    Checks RFC format, blocked domains, blocked TLDs,
    blocked local parts, junk patterns, foreign TLDs.
    """
    if not email or not isinstance(email, str):
        return False
    email = email.strip().lower()
    if email.count('@') != 1:
        return False
    local, domain = email.split('@', 1)
    if not _RFC5321_LOCAL.match(local):
        return False
    if '..' in local or '..' in domain:
        return False
    if '.' not in domain:
        return False
    if domain in BLOCKED_EMAIL_DOMAINS:
        return False
    if domain.endswith('.ac.uk'):
        return False
    if domain.endswith('.edu'):
        return False
    if local in BLOCKED_EMAIL_LOCAL_PARTS:
        return False
    if is_junk_email(email):
        return False
    if is_foreign_email(email):
        return False
    return True


def _email_fail_reason(email: str) -> str:
    """
    Returns why an email fails, or empty string if it passes.
    Used for informative cleanup output.
    """
    if not email or not isinstance(email, str):
        return "empty"
    email = email.strip().lower()
    if email.count('@') != 1:
        return "no-at-sign"
    local, domain = email.split('@', 1)
    if not _RFC5321_LOCAL.match(local):
        return "invalid-local-chars"
    if '..' in local or '..' in domain:
        return "double-dot"
    if '.' not in domain:
        return "no-dot-in-domain"
    if domain in BLOCKED_EMAIL_DOMAINS:
        return f"blocked-domain:{domain}"
    if domain.endswith('.ac.uk'):
        return "academic-institution"
    if domain.endswith('.edu'):
        return "us-academic-edu"
    if local in BLOCKED_EMAIL_LOCAL_PARTS:
        return f"blocked-local:{local}"
    if domain in GENERIC_DOMAINS:
        return f"generic-domain:{domain}"
    if _is_junk_website("https://" + domain):
        return f"junk-website-domain:{domain}"
    if is_junk_email(email):
        return "enricher-junk"
    if is_foreign_email(email):
        return "foreign-email"
    return ""


def _is_constructed_email_valid(
    email: str,
    business_name: str,
    website_domain: str,
) -> bool:
    if not _is_valid_email(email):
        return False
    local  = email.split("@")[0].lower()
    domain = email.split("@")[1].lower()
    local_clean = re.sub(r'[.\-_]', ' ', local)
    for word in local_clean.split():
        if word in _JUNK_EMAIL_LOCAL_WORDS:
            return False
    tokens      = business_name_tokens(business_name)
    domain_root = domain.split(".")[0]
    token_in_domain = any(
        len(t) >= 4 and t in domain_root
        for t in tokens
    )
    website_clean = (website_domain or "").replace("www.", "")
    matches_website = bool(
        website_clean and
        domain.replace("www.", "") == website_clean
    )
    if not token_in_domain and not matches_website:
        return False
    if domain in GENERIC_DOMAINS:
        return False
    return True


def _is_junk_website(url: str) -> bool:
    if not url:
        return False
    domain = normalize_domain(url)
    for pattern in JUNK_WEBSITE_PATTERNS:
        if pattern in domain:
            return True
    for tld in FOREIGN_WEBSITE_TLDS:
        if domain.endswith(tld):
            return True
    if domain.endswith('.ac.uk'):
        return True
    bare = domain.replace("www.", "")
    if bare in GENERIC_DOMAINS:
        return True
    return False


def _is_own_website(domain: str, business_name: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT business_name FROM leads "
            "WHERE website LIKE ? AND business_name != ?",
            (f"%{domain}%", business_name),
        )
        rows = cur.fetchall()
        if not rows:
            return True
        tokens = set(business_name_tokens(business_name))
        for (other_name,) in rows:
            other_tokens = set(business_name_tokens(other_name))
            if len(tokens & other_tokens) >= 2:
                return True
        return False
    except Exception:
        return True
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────
# FIX-DR-27: Email domain must match website domain
# ─────────────────────────────────────────────────────────

def _email_domain_matches_website(
    email: str,
    website_domain: str,
) -> bool:
    """
    Returns True if the email domain relates to the website domain.

    Handles exact match, subdomain relationships, and shared
    registrable root (e.g. nightanddaydentist appears in both
    nightanddaydentist.co.uk and
    nightanddayemergencydentist-nottingham.co.uk).

    Rejects emails from font foundries, CDN assets, booking
    platforms, and third-party services embedded in page HTML.
    """
    if not email or not website_domain:
        return False

    email_domain = email.split("@")[-1].lower().replace("www.", "")
    site_domain  = website_domain.lower().replace("www.", "")

    # Exact match
    if email_domain == site_domain:
        return True

    # Subdomain relationships
    if email_domain.endswith("." + site_domain):
        return True
    if site_domain.endswith("." + email_domain):
        return True

    # Shared registrable root
    def _root(domain: str) -> str:
        parts = domain.split(".")
        if len(parts) >= 3 and parts[-2] in (
            "co", "org", "me", "net", "ltd", "plc"
        ):
            return parts[-3]
        if len(parts) >= 2:
            return parts[-2]
        return parts[0]

    email_root = _root(email_domain)
    site_root  = _root(site_domain)

    # Only compare roots of meaningful length to avoid false matches
    if len(email_root) >= 6 and len(site_root) >= 6:
        if email_root in site_root or site_root in email_root:
            return True

    return False


# ─────────────────────────────────────────────────────────
# FIX-DR-29: Decision maker name quality check
# ─────────────────────────────────────────────────────────

def _is_junk_dm_name(name: str) -> bool:
    """
    Returns True if a decision maker name is clearly not a real
    person's name. Catches website navigation text, company names,
    and placeholder values scraped incorrectly.

    Triggers on:
    - More than 4 words (real names are 2-3 words)
    - Contains digits
    - Contains known junk phrases
    - Is a single word shorter than 3 characters
    """
    if not name or not isinstance(name, str):
        return True

    name = name.strip()
    words = name.split()

    # Too many words — likely navigation text or company description
    if len(words) > 4:
        return True

    # Contains digits — not a person name
    if any(c.isdigit() for c in name):
        return True

    # Known junk phrases from website navigation / page content
    junk_phrases = {
        "my account", "contact us", "find us", "about us",
        "book appointment", "news knowledge", "newsletter",
        "feedback", "anonymously", "cracker cup",
        "services breeder", "construction company",
        "dental clinics", "care group", "designated member",
        "nominees limited", "administration uk",
        "operations manager",
    }
    name_lower = name.lower()
    for phrase in junk_phrases:
        if phrase in name_lower:
            return True

    # Single word that is clearly not a name
    if len(words) == 1 and len(name) < 3:
        return True

    return False


# ─────────────────────────────────────────────────────────
# Database cleanup
# ─────────────────────────────────────────────────────────

def cleanup_junk_websites() -> int:
    cleared = 0
    conn    = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, business_name, website FROM leads "
            "WHERE website IS NOT NULL AND website != ''"
        )
        rows = cur.fetchall()
        for lead_id, name, website in rows:
            if _is_junk_website(website):
                cur.execute(
                    "UPDATE leads SET website = '' WHERE id = ?",
                    (lead_id,),
                )
                print(
                    f"  CLEARED junk website: "
                    f"{name[:45]} → {website}"
                )
                cleared += 1
        conn.commit()
    except Exception as exc:
        print(f"  cleanup_junk_websites error: {exc}")
    finally:
        if conn:
            conn.close()
    return cleared


def cleanup_junk_phones() -> int:
    cleared = 0
    conn    = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, business_name, phone FROM leads "
            "WHERE phone IS NOT NULL AND phone != ''"
        )
        rows = cur.fetchall()
        for lead_id, name, phone in rows:
            if not _is_valid_uk_phone(phone):
                cur.execute(
                    "UPDATE leads SET phone = '' WHERE id = ?",
                    (lead_id,),
                )
                print(
                    f"  CLEARED invalid phone: "
                    f"{name[:45]} → {phone}"
                )
                cleared += 1
        conn.commit()
    except Exception as exc:
        print(f"  cleanup_junk_phones error: {exc}")
    finally:
        if conn:
            conn.close()
    return cleared


def cleanup_junk_emails() -> int:
    cleared = 0
    conn    = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, business_name, email, website FROM leads "
            "WHERE email IS NOT NULL AND email != ''"
        )
        rows = cur.fetchall()
        for lead_id, name, email, website in rows:
            reason = _email_fail_reason(email)
            if reason:
                cur.execute(
                    "UPDATE leads SET email = '' WHERE id = ?",
                    (lead_id,),
                )
                print(
                    f"  CLEARED email [{reason}]: "
                    f"{name[:40]} → {email}"
                )
                cleared += 1
        conn.commit()
    except Exception as exc:
        print(f"  cleanup_junk_emails error: {exc}")
    finally:
        if conn:
            conn.close()
    return cleared


def cleanup_junk_dm_names() -> int:
    """
    FIX-DR-29: Clear decision maker names that are clearly not
    real person names — website navigation text, company names,
    placeholder values.
    """
    cleared = 0
    conn    = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, business_name, decision_maker_name "
            "FROM leads "
            "WHERE decision_maker_name IS NOT NULL "
            "AND decision_maker_name != ''"
        )
        rows = cur.fetchall()
        for lead_id, name, dm in rows:
            if _is_junk_dm_name(dm):
                cur.execute(
                    "UPDATE leads SET "
                    "decision_maker_name = '', dm_source = '' "
                    "WHERE id = ?",
                    (lead_id,),
                )
                print(
                    f"  CLEARED junk DM: "
                    f"{name[:35]} → {dm}"
                )
                cleared += 1
        conn.commit()
    except Exception as exc:
        print(f"  cleanup_junk_dm_names error: {exc}")
    finally:
        if conn:
            conn.close()
    return cleared


def cleanup_false_positive_data() -> int:
    cleared = 0
    conn    = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, business_name, website, email "
            "FROM leads "
            "WHERE website IS NOT NULL AND website != '' "
            "AND email IS NOT NULL AND email != ''"
        )
        rows = cur.fetchall()
        for lead_id, name, website, email in rows:
            domain = normalize_domain(website)
            is_foreign_site = any(
                domain.endswith(tld)
                for tld in FOREIGN_WEBSITE_TLDS
            )
            if not is_foreign_site:
                continue
            tokens = business_name_tokens(name)
            email_domain = (
                email.split("@")[-1] if "@" in email else ""
            )
            name_in_email = any(
                len(t) >= 5 and t in email_domain
                for t in tokens
            )
            if not name_in_email:
                cur.execute(
                    "UPDATE leads SET website = '', email = '' "
                    "WHERE id = ?",
                    (lead_id,),
                )
                print(
                    f"  CLEARED false positive: {name[:45]}"
                    f"\n    website={website}"
                    f"\n    email={email}"
                )
                cleared += 1
        conn.commit()
    except Exception as exc:
        print(f"  cleanup_false_positive_data error: {exc}")
    finally:
        if conn:
            conn.close()
    return cleared


# ─────────────────────────────────────────────────────────
# Director name formatting
# ─────────────────────────────────────────────────────────

def _format_ch_name(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if "," in raw:
        parts    = raw.split(",", 1)
        surname  = parts[0].strip().title()
        forename = parts[1].strip().title()
        forename = re.sub(
            r'\s+\b(I{1,3}|IV|V|Jr\.?|Sr\.?)\s*$',
            '', forename, flags=re.IGNORECASE,
        ).strip()
        if forename:
            return forename + " " + surname
        return surname
    words = raw.split()
    if len(words) >= 2 and words[0].isupper():
        surname  = words[0].title()
        forename = " ".join(w.title() for w in words[1:])
        forename = re.sub(
            r'\s+\b(I{1,3}|IV|V|Jr\.?|Sr\.?)\s*$',
            '', forename, flags=re.IGNORECASE,
        ).strip()
        if forename:
            return forename + " " + surname
        return surname
    return raw.title()


# ─────────────────────────────────────────────────────────
# Companies House
# ─────────────────────────────────────────────────────────

_COMPANY_AS_DIRECTOR_WORDS = {
    "limited", "ltd", "llp", "plc", "lp",
    "administration", "services", "nominees",
    "secretaries", "trustee",
}


def ch_search(business_name: str) -> dict:
    empty = {
        "company_number":      "",
        "registered_address":  "",
        "registered_postcode": "",
        "director_name":       "",
        "company_status":      "",
        "sic_codes":           [],
    }
    if not CH_AVAILABLE:
        return empty
    try:
        resp = requests.get(
            "https://api.company-information.service.gov.uk"
            "/search/companies",
            params={"q": business_name, "items_per_page": 5},
            auth=(CH_API_KEY, ""),
            timeout=10,
        )
        if resp.status_code != 200:
            return empty
        items = resp.json().get("items", [])
        if not items:
            return empty

        tokens     = business_name_tokens(business_name)
        best       = None
        best_score = -1
        for item in items:
            if item.get("company_status", "").lower() not in (
                "active", ""
            ):
                continue
            title = item.get("title", "").lower()
            score = sum(1 for t in tokens if t in title)
            if score > best_score:
                best_score = score
                best = item

        if not best:
            best = items[0]

        company_number = best.get("company_number", "")
        address_obj    = best.get("address", {})
        postcode       = address_obj.get("postal_code", "")
        address_parts  = [
            address_obj.get("address_line_1", ""),
            address_obj.get("address_line_2", ""),
            address_obj.get("locality", ""),
            postcode,
        ]
        registered_address = ", ".join(
            p for p in address_parts if p
        )

        result = {
            "company_number":      company_number,
            "registered_address":  registered_address,
            "registered_postcode": postcode,
            "director_name":       "",
            "company_status":      best.get("company_status", ""),
            "sic_codes":           best.get("sic_codes", []),
        }
        if company_number:
            result["director_name"] = _ch_get_director(
                company_number
            )
        return result

    except Exception as exc:
        print(f"    CH search error: {exc}")
        return empty


def _is_company_as_director(name: str) -> bool:
    name_lower = name.lower()
    return any(
        w in name_lower for w in _COMPANY_AS_DIRECTOR_WORDS
    )


def _ch_get_director(company_number: str) -> str:
    if not CH_AVAILABLE or not company_number:
        return ""
    try:
        resp = requests.get(
            f"https://api.company-information.service.gov.uk"
            f"/company/{company_number}/officers",
            auth=(CH_API_KEY, ""),
            timeout=10,
        )
        if resp.status_code != 200:
            return ""
        officers = resp.json().get("items", [])

        PRIORITY_ROLES = {
            "director", "managing director",
            "chief executive officer", "proprietor",
            "manager", "registered manager",
        }
        # First pass: priority roles only
        for officer in officers:
            if officer.get("resigned_on"):
                continue
            role = officer.get(
                "officer_role", ""
            ).lower().replace("-", " ")
            if any(r in role for r in PRIORITY_ROLES):
                raw = officer.get("name", "").strip()
                if not raw:
                    continue
                formatted = _format_ch_name(raw)
                cleaned   = clean_decision_maker_name(formatted)
                if (
                    cleaned
                    and not _is_company_as_director(cleaned)
                    and not _is_junk_dm_name(cleaned)
                ):
                    return cleaned

        # Second pass: any active officer
        for officer in officers:
            if officer.get("resigned_on"):
                continue
            raw = officer.get("name", "").strip()
            if not raw:
                continue
            formatted = _format_ch_name(raw)
            cleaned   = clean_decision_maker_name(formatted)
            if (
                cleaned
                and not _is_company_as_director(cleaned)
                and not _is_junk_dm_name(cleaned)
            ):
                return cleaned

        return ""
    except Exception as exc:
        print(f"    CH officers error: {exc}")
        return ""


# ─────────────────────────────────────────────────────────
# Website finder — returns (url, score) tuple
# FIX-DR-31: City-first search, CH postcode as last fallback
# ─────────────────────────────────────────────────────────

def find_website(
    business_name: str,
    city: str        = "",
    postcode: str    = "",
    ch_postcode: str = "",
) -> tuple:
    """
    Search for the official website of a UK business.

    Returns:
        (url: str, score: int)

    FIX-DR-31: When Companies House postcode is in a different
    city to the lead's known city, do not use the CH postcode
    in queries — it leads searches to the wrong location.

    Always prioritise the lead's own city for searches.
    Use CH postcode only as a final fallback if all city
    searches fail and only when it differs from postcode.

    Example: Derby Cottage Clinic is registered in Newmarket
    (CB8 7LG) but operates in Derby. Searching by CB8 7LG
    finds nothing. Searching by Derby finds the site.
    """
    clean_name = re.sub(
        r'\b(limited|ltd\.?|llp|plc|lp)\b',
        '', business_name, flags=re.IGNORECASE,
    ).strip().strip(',').strip()
    clean_name = clean_name.title()

    # Lead's own postcode takes priority over CH postcode
    search_postcode = postcode or ""

    # Build queries — city always first
    queries = []
    if city:
        queries.append(f'"{clean_name}" {city} official site')
        queries.append(f'"{clean_name}" {city}')
        queries.append(
            f'"{clean_name}" {city} official website'
        )
    if search_postcode:
        queries.append(f'"{clean_name}" {search_postcode}')
    queries.append(f'"{clean_name}" official website')
    queries.append(f'"{business_name}"')
    # CH postcode as final fallback — only when it differs
    # from the lead's own postcode and we have a city to
    # anchor the search so we don't drift to wrong location
    if (
        ch_postcode
        and ch_postcode != search_postcode
        and city
    ):
        queries.append(f'"{clean_name}" {ch_postcode}')

    tokens = [
        t for t in business_name_tokens(business_name)
        if len(t) >= _MIN_TOKEN_LENGTH
    ]

    best_url   = ""
    best_score = 0

    for query in queries:
        url, score = _serp_website_search(
            query, business_name, tokens
        )
        if url and score > best_score:
            best_url   = url
            best_score = score
            if best_score >= 100:
                break
        human_pause(0.5, 1.0)

    return best_url, best_score


def _serp_website_search(
    query: str,
    business_name: str,
    tokens: list,
) -> tuple:
    results = _serp_search(query, num=10)
    if not results:
        return "", 0

    scored = []
    for result in results:
        link = result.get("link", "")
        if not link:
            continue
        try:
            parsed = urlparse(link)
            d = parsed.netloc.lower().replace("www.", "")
        except Exception:
            continue

        if not d:
            continue
        if _is_junk_website("https://" + d):
            continue
        if is_bad_domain(d, ""):
            continue
        if not _is_own_website(d, business_name):
            print(
                f"    [SERP] Skip {d} "
                f"— assigned to different business"
            )
            continue

        s    = score_domain_relevance(d, business_name)
        root = d.split(".")[0]

        has_long_token_match = any(
            len(t) >= _MIN_TOKEN_LENGTH and t in root
            for t in tokens
        )
        has_couk_token_match = (
            d.endswith(".co.uk")
            and any(
                len(t) >= _MIN_TOKEN_LENGTH_COUK and t in root
                for t in tokens
            )
        )

        min_score = (
            _MIN_WEBSITE_SCORE if d.endswith(".co.uk")
            else _MIN_WEBSITE_SCORE_NONUK
        )

        if s >= min_score and (
            has_long_token_match or has_couk_token_match
        ):
            scored.append((d, s))
            print(f"    [SERP] Candidate: {d} score={s}")

    if not scored:
        return "", 0

    scored.sort(key=lambda x: x[1], reverse=True)
    best_domain = scored[0][0]
    best_score  = scored[0][1]
    best_url    = "https://" + best_domain

    if _validate_website_url(best_url):
        return best_url, best_score

    return "", 0


# ─────────────────────────────────────────────────────────
# Email construction helpers
# ─────────────────────────────────────────────────────────

def _construct_email_candidates(
    director_name: str,
    domain: str,
) -> list:
    if not director_name or not domain:
        return []
    name_clean = re.sub(
        r'[^a-zA-Z\s]', '', director_name
    ).strip()
    parts = name_clean.lower().split()
    if not parts:
        return []
    first = parts[0]
    last  = parts[-1] if len(parts) > 1 else ""
    if first and last:
        return [
            f"{first}.{last}@{domain}",
            f"{first[0]}.{last}@{domain}",
            f"{first}@{domain}",
            f"{first[0]}{last}@{domain}",
        ]
    return [f"{first}@{domain}"]


def _verify_email_domain(email: str, timeout: int = 8) -> bool:
    if not _is_valid_email(email):
        return False
    domain = email.split("@")[1]
    for scheme in ("https", "http"):
        try:
            resp = requests.head(
                f"{scheme}://{domain}",
                headers=get_headers(),
                timeout=timeout,
                allow_redirects=True,
            )
            if resp.status_code < 500:
                return True
        except Exception:
            continue
    return False


# ─────────────────────────────────────────────────────────
# Email finder
# ─────────────────────────────────────────────────────────

def find_email(
    business_name: str,
    website: str       = "",
    city: str          = "",
    director: str      = "",
    postcode: str      = "",
    website_score: int = 0,
) -> str:
    website_domain = ""
    if website:
        try:
            website_domain = urlparse(
                website if website.startswith("http")
                else "https://" + website
            ).netloc.replace("www.", "")
        except Exception:
            pass

    # Step 1: crawl website
    if website and not _is_junk_website(website):
        email = _crawl_for_email(
            website, business_name, website_domain
        )
        if email and _is_valid_email(email):
            print(f"    [EMAIL] Found by crawling: {email}")
            return email

        # Step 2: construct — only when website confidence is high
        if director and website_domain:
            if website_score >= _MIN_SCORE_FOR_CONSTRUCTION:
                print(
                    f"    [EMAIL] Attempting construction "
                    f"(score={website_score})"
                )
                for candidate in _construct_email_candidates(
                    director, website_domain
                ):
                    if _is_constructed_email_valid(
                        candidate, business_name, website_domain
                    ):
                        if _verify_email_domain(candidate):
                            print(
                                f"    [EMAIL] Constructed: "
                                f"{candidate}"
                            )
                            return candidate
                    human_pause(0.3, 0.6)
            else:
                print(
                    f"    [EMAIL] Skipping construction — "
                    f"score={website_score} < "
                    f"{_MIN_SCORE_FOR_CONSTRUCTION}"
                )

    # Step 3: SerpApi search
    clean_name = re.sub(
        r'\b(limited|ltd\.?|llp|plc|lp)\b',
        '', business_name, flags=re.IGNORECASE,
    ).strip().title()

    queries = []
    if director:
        queries.append(
            f'"{director}" "{clean_name}" email contact'
        )
    if city:
        queries.append(
            f'"{clean_name}" {city} email contact'
        )
    if postcode:
        queries.append(f'"{clean_name}" {postcode} email')
    queries.append(f'"{clean_name}" email')

    for query in queries:
        email = _search_for_email(
            query, business_name, website_domain
        )
        if email:
            return email
        human_pause(0.5, 1.0)

    return ""


def _crawl_for_email(
    website: str,
    business_name: str,
    website_domain: str = "",
) -> str:
    """
    Crawl the website and extract an email.
    FIX-DR-27: Every email is checked against
    _email_domain_matches_website() before being returned.
    """
    if not website.startswith("http"):
        website = "https://" + website
    try:
        parsed = urlparse(website)
        base   = f"{parsed.scheme}://{parsed.netloc}"
        domain = website_domain or parsed.netloc.replace(
            "www.", ""
        )
    except Exception:
        return ""

    pages = [
        website,
        urljoin(base, "/contact"),
        urljoin(base, "/contact-us"),
        urljoin(base, "/about"),
        urljoin(base, "/about-us"),
    ]

    for page_url in pages:
        try:
            human_pause(0.5, 1.0)
            html, soup = crawl_page(page_url)
            if not html or not soup:
                continue

            # Mailto links — most reliable
            for email in extract_emails_from_mailto_links(soup):
                if _is_valid_email(email):
                    if _email_domain_matches_website(
                        email, domain
                    ):
                        return email
                    else:
                        print(
                            f"    [EMAIL] Rejected {email} "
                            f"— domain mismatch with {domain}"
                        )

            # JSON-LD structured data
            structured = extract_from_json_ld(soup)
            if structured.get("email"):
                e = structured["email"]
                if _is_valid_email(e):
                    if _email_domain_matches_website(e, domain):
                        return e

            # Plain HTML text
            emails = extract_emails_from_html(
                html,
                domain=domain,
                business_name=business_name,
                source="website",
                min_confidence=40,
            )
            for email in emails:
                if _is_valid_email(email):
                    if _email_domain_matches_website(
                        email, domain
                    ):
                        return email

        except Exception:
            continue

    return ""


def _search_for_email(
    query: str,
    business_name: str,
    website_domain: str = "",
) -> str:
    """
    Search SerpApi for an email.
    FIX-DR-27: When website_domain is known, enforce domain match.
    """
    results = _serp_search(query, num=10)
    for result in results:
        text = (
            result.get("snippet", "") + " " +
            result.get("link", "")
        )
        emails = extract_emails_from_html(
            text,
            business_name=business_name,
            source="search",
            min_confidence=60,
        )
        for email in emails:
            if not _is_valid_email(email):
                continue
            domain = email.split("@")[1]
            if is_bad_domain(domain, ""):
                continue
            if _is_junk_website("https://" + domain):
                continue
            if website_domain:
                if not _email_domain_matches_website(
                    email, website_domain
                ):
                    print(
                        f"    [EMAIL] Rejected {email} "
                        f"— does not match {website_domain}"
                    )
                    continue
            return email
    return ""


# ─────────────────────────────────────────────────────────
# Phone finder
# ─────────────────────────────────────────────────────────

def find_phone(
    business_name: str,
    website: str   = "",
    city: str      = "",
    postcode: str  = "",
) -> str:
    if website and not _is_junk_website(website):
        phone = _crawl_for_phone(website)
        if phone:
            return phone

    clean_name = re.sub(
        r'\b(limited|ltd\.?|llp|plc|lp)\b',
        '', business_name, flags=re.IGNORECASE,
    ).strip().title()

    queries = []
    if city:
        queries.append(
            f'"{clean_name}" {city} phone number contact'
        )
    if postcode:
        queries.append(f'"{clean_name}" {postcode} phone')
    queries.append(f'"{clean_name}" phone number')

    for query in queries:
        phone = _search_for_phone(query)
        if phone:
            return phone
        human_pause(0.5, 1.0)

    return ""


def _crawl_for_phone(website: str) -> str:
    if not website.startswith("http"):
        website = "https://" + website
    try:
        parsed = urlparse(website)
        base   = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return ""

    pages = [
        website,
        urljoin(base, "/contact"),
        urljoin(base, "/contact-us"),
    ]

    for page_url in pages:
        try:
            human_pause(0.3, 0.7)
            html, soup = crawl_page(page_url)
            if not html or not soup:
                continue
            for p in extract_phones_from_tel_links(soup):
                if _is_valid_uk_phone(p):
                    return p
            structured = extract_from_json_ld(soup)
            if structured.get("phone"):
                if _is_valid_uk_phone(structured["phone"]):
                    return structured["phone"]
            for p in extract_phones_from_html(html):
                if _is_valid_uk_phone(p):
                    return p
        except Exception:
            continue

    return ""


def _search_for_phone(query: str) -> str:
    results = _serp_search(query, num=10)
    for result in results:
        text = (
            result.get("snippet", "") + " " +
            result.get("link", "")
        )
        for p in extract_phones_from_html(text):
            if _is_valid_uk_phone(p):
                return p
    return ""


# ─────────────────────────────────────────────────────────
# Database write-back
# ─────────────────────────────────────────────────────────

def write_back(lead_id: int, updates: dict) -> bool:
    if not updates:
        return False
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            UPDATE leads SET
                email = CASE
                    WHEN (email IS NULL OR email = '')
                    AND :email != '' THEN :email
                    ELSE email
                END,
                phone = CASE
                    WHEN (phone IS NULL OR phone = '')
                    AND :phone != '' THEN :phone
                    ELSE phone
                END,
                website = CASE
                    WHEN (website IS NULL OR website = '')
                    AND :website != '' THEN :website
                    ELSE website
                END,
                decision_maker_name = CASE
                    WHEN (
                        decision_maker_name IS NULL
                        OR decision_maker_name = ''
                    )
                    AND :dm != '' THEN :dm
                    ELSE decision_maker_name
                END,
                dm_source = CASE
                    WHEN (
                        decision_maker_name IS NULL
                        OR decision_maker_name = ''
                    )
                    AND :dm != '' THEN :dm_source
                    ELSE dm_source
                END,
                enrichment_status = 'complete',
                last_enriched_at  = datetime('now')
            WHERE id = :id
            """,
            {
                "email":     updates.get("email",         ""),
                "phone":     updates.get("phone",         ""),
                "website":   updates.get("website",       ""),
                "dm":        updates.get("director_name", ""),
                "dm_source": updates.get(
                    "dm_source", "companies_house"
                ),
                "id": lead_id,
            },
        )
        conn.commit()
        return True
    except Exception as exc:
        print(f"    DB write error: {exc}")
        return False
    finally:
        if conn:
            conn.close()


def clear_field(lead_id: int, field: str) -> None:
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            f"UPDATE leads SET {field} = '' WHERE id = ?",
            (lead_id,),
        )
        conn.commit()
    except Exception as exc:
        print(f"    clear_field error: {exc}")
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 70)
    print("KNCS LeadGen Pro v10.11")
    print("Step 0: Database cleanup...\n")

    w  = cleanup_junk_websites()
    p  = cleanup_junk_phones()
    e  = cleanup_junk_emails()
    dm = cleanup_junk_dm_names()
    f  = cleanup_false_positive_data()

    print(
        f"\n  Websites cleared:        {w}"
        f"\n  Invalid phones cleared:  {p}"
        f"\n  Junk emails cleared:     {e}"
        f"\n  Junk DM names cleared:   {dm}"
        f"\n  False positives cleared: {f}\n"
    )

    if not SERPAPI_AVAILABLE:
        print(
            "WARNING: SERPAPI_KEY not set — website/email/phone "
            "search will be skipped. Add key to .env\n"
        )

    print("=" * 70)
    print("\nLoading UK leads for deep research...\n")

    conn = get_connection()
    cur  = conn.cursor()

    # FIX-DR-30: UK-only leads — skip Nigerian / non-UK records
    cur.execute("""
        SELECT
            id, business_name, city, country,
            website, phone, email,
            decision_maker_name, industry,
            postcode, source
        FROM leads
        WHERE (
            (email IS NULL OR email = '')
            OR (phone IS NULL OR phone = '')
        )
        AND (
            country IS NULL
            OR TRIM(country) = ''
            OR LOWER(TRIM(country)) = 'united kingdom'
            OR LOWER(TRIM(country)) = 'uk'
            OR LOWER(TRIM(country)) = 'england'
            OR LOWER(TRIM(country)) = 'wales'
            OR LOWER(TRIM(country)) = 'scotland'
            OR LOWER(TRIM(country)) = 'northern ireland'
            OR LOWER(TRIM(country)) = 'great britain'
            OR LOWER(TRIM(country)) = 'gb'
        )
        ORDER BY id DESC
        LIMIT ?
    """, (LIMIT,))

    columns = [d[0] for d in cur.description]
    rows    = cur.fetchall()
    leads   = [dict(zip(columns, row)) for row in rows]
    conn.close()

    print(f"Found {len(leads)} UK leads to research\n")
    print("=" * 70)

    total_improved       = 0
    total_phones_found   = 0
    total_emails_found   = 0
    total_websites_found = 0

    for i, lead in enumerate(leads):
        lead_id = lead["id"]
        name    = lead.get("business_name", "?")
        city    = lead.get("city", "")

        print(f"\n[{i+1}/{len(leads)}] {name}")
        print(f"  City: {city or 'unknown'}")

        before_email   = lead.get("email",               "") or ""
        before_phone   = lead.get("phone",               "") or ""
        before_website = lead.get("website",             "") or ""
        before_dm      = lead.get("decision_maker_name", "") or ""

        # Clear any junk in the row before processing
        if before_email and not _is_valid_email(before_email):
            reason = _email_fail_reason(before_email)
            print(
                f"  [EMAIL] Clearing [{reason}]: {before_email}"
            )
            clear_field(lead_id, "email")
            before_email = ""

        if before_website and _is_junk_website(before_website):
            print(f"  [WEB] Clearing junk: {before_website}")
            clear_field(lead_id, "website")
            before_website = ""

        if before_phone and not _is_valid_uk_phone(before_phone):
            print(f"  [PHONE] Clearing invalid: {before_phone}")
            clear_field(lead_id, "phone")
            before_phone = ""

        if before_dm and _is_junk_dm_name(before_dm):
            print(f"  [DM] Clearing junk name: {before_dm}")
            clear_field(lead_id, "decision_maker_name")
            before_dm = ""

        if before_email and before_phone:
            print("  Already has email + phone — skipping")
            continue

        updates       = {}
        gained        = []
        ch_postcode   = ""
        website_score = 0

        # ── Step 1: Companies House ───────────────────────────────
        director_name = before_dm
        if CH_AVAILABLE:
            print(f"  [CH]  Searching Companies House...")
            ch_data     = ch_search(name)
            ch_postcode = ch_data.get("registered_postcode", "")

            if ch_data["director_name"]:
                candidate_dm = ch_data["director_name"]
                if _is_junk_dm_name(candidate_dm):
                    print(
                        f"  [CH]  Rejected junk DM: {candidate_dm}"
                    )
                elif not before_dm:
                    director_name            = candidate_dm
                    updates["director_name"] = director_name
                    updates["dm_source"]     = "companies_house"
                    gained.append(f"DM={director_name}")
                    print(
                        f"  [CH]  Director: {director_name} ← NEW"
                    )
                else:
                    director_name = before_dm
                    print(
                        f"  [CH]  Director: {candidate_dm} "
                        f"(already in DB)"
                    )
            else:
                print(f"  [CH]  No director found")

            if ch_data.get("registered_address"):
                print(
                    f"  [CH]  Address: "
                    f"{ch_data['registered_address']}"
                )
        else:
            print("  [CH]  SKIPPED — no API key")

        human_pause(0.5, 1.0)

        # ── Step 2: Website ───────────────────────────────────────
        website = before_website
        if not website:
            print(f"  [WEB] Searching...")
            # FIX-DR-31: pass lead postcode and CH postcode
            # separately so find_website() can use city-first
            # search when CH address is in a different city.
            lead_postcode = lead.get("postcode", "") or ""
            website, website_score = find_website(
                name,
                city=city,
                postcode=lead_postcode,
                ch_postcode=ch_postcode,
            )
            if website:
                updates["website"] = website
                gained.append(f"website={website}")
                total_websites_found += 1
                print(
                    f"  [WEB] Found: {website} "
                    f"(score={website_score})"
                )
            else:
                print(f"  [WEB] Not found")
        else:
            print(f"  [WEB] Already known: {website}")
            # Pre-existing website: allow crawling but not
            # construction (score unknown)
            website_score = 0

        human_pause(0.5, 1.0)

        # ── Step 3: Email ─────────────────────────────────────────
        if not before_email:
            print(f"  [EMAIL] Searching...")
            email = find_email(
                name,
                website=website,
                city=city,
                director=director_name,
                postcode=ch_postcode,
                website_score=website_score,
            )
            if email and _is_valid_email(email):
                updates["email"] = email
                gained.append(f"email={email}")
                total_emails_found += 1
                print(f"  [EMAIL] Found: {email}")
            else:
                print(f"  [EMAIL] Not found")
        else:
            print(f"  [EMAIL] Already known: {before_email}")

        human_pause(0.5, 1.0)

        # ── Step 4: Phone ─────────────────────────────────────────
        if not before_phone:
            print(f"  [PHONE] Searching...")
            phone = find_phone(
                name,
                website=website,
                city=city,
                postcode=ch_postcode,
            )
            if phone and _is_valid_uk_phone(phone):
                updates["phone"] = phone
                gained.append(f"phone={phone}")
                total_phones_found += 1
                print(f"  [PHONE] Found: {phone}")
            else:
                print(f"  [PHONE] Not found")
        else:
            print(f"  [PHONE] Already known: {before_phone}")

        # ── Write ─────────────────────────────────────────────────
        if gained:
            print(f"\n  ✓ IMPROVED: {', '.join(gained)}")
            write_back(lead_id, updates)
            total_improved += 1
        else:
            print(f"\n  ✗ Nothing new found")

        human_pause(1.0, 2.0)

    print(f"\n{'=' * 70}")
    print(f"KNCS LeadGen Pro v10.11 — Run complete")
    print(f"  Leads improved:  {total_improved} / {len(leads)}")
    print(f"  Phones found:    {total_phones_found}")
    print(f"  Emails found:    {total_emails_found}")
    print(f"  Websites found:  {total_websites_found}")
    print(f"\nRun check_db.py to see results")


if __name__ == "__main__":
    main()
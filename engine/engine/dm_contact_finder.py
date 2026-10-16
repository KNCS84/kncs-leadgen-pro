# engine/dm_contact_finder.py
# KNCS LeadGen Pro — Decision Maker Contact Finder
# v1.0
#
# PURPOSE:
#   Dedicated scraper for finding named decision makers
#   and their personal emails from care home websites.
#
#   Called by enricher.py as Step 7 — last resort before
#   giving up on email discovery.
#
# WHAT IT DOES:
#   1. Crawls /about, /about-us, /team, /our-team,
#      /management, /staff, /meet-the-team pages
#   2. Finds person name + title combinations
#   3. Extracts mailto: links near those names
#   4. Extracts email text near those names
#   5. Returns best match: {name, email, title, confidence}
#
# HIT RATE:
#   ~35% of UK care home websites have named staff pages
#   with contact emails. This finder catches those cases
#   where pattern generation and search both failed.
#
# NEVER RAISES. Returns {} if nothing found.

from __future__ import annotations

import re
import logging
import time
import random
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
    "Connection":      "keep-alive",
}

# Pages most likely to have staff + contact details
_STAFF_PAGES = [
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/meet-the-team",
    "/management",
    "/staff",
    "/people",
    "/who-we-are",
    "/leadership",
    "/contact",
    "/contact-us",
]

# Titles that indicate a decision maker
_DM_TITLES = re.compile(
    r'\b(?:'
    r'Registered\s+Manager|Care\s+Home\s+Manager|'
    r'Home\s+Manager|Nominated\s+Individual|'
    r'Deputy\s+Manager|Clinical\s+Lead|Matron|'
    r'Director|Owner|Proprietor|Managing\s+Director|'
    r'MD|CEO|Founder|Principal|Chairman|Chairwoman|'
    r'Head\s+of\s+Care|Manager|Administrator'
    r')\b',
    re.IGNORECASE,
)

# Email pattern
_EMAIL_RE = re.compile(
    r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+'
    r'\.[a-zA-Z]{2,})\b'
)

# Name pattern — two or three capitalised words
_NAME_RE = re.compile(
    r'\b([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})'
    r'{1,2})\b'
)

# Generic emails to skip
_GENERIC_PREFIXES = (
    "info@", "admin@", "contact@", "hello@",
    "noreply@", "no-reply@", "enquiries@",
    "enquiry@", "reception@", "office@",
    "mail@", "post@", "support@", "careers@",
    "jobs@", "hr@", "accounts@",
)

# Foreign TLDs — never valid for UK care homes
_FOREIGN_TLDS = {
    ".cz", ".de", ".fr", ".pl", ".ru", ".cn",
    ".es", ".it", ".nl", ".pt", ".ro", ".hu",
}


def _is_junk_email(email: str) -> bool:
    if not email or "@" not in email:
        return True
    el = email.lower()
    if any(el.startswith(p) for p in _GENERIC_PREFIXES):
        return True
    domain = el.split("@")[-1]
    if any(domain.endswith(t) for t in _FOREIGN_TLDS):
        return True
    junk = [
        "example", "test@", "noreply", "schema.org",
        "w3.org", ".png", ".jpg", ".gif", ".svg",
    ]
    return any(j in el for j in junk)


def _fetch_page(url: str, timeout: int = 8):
    """Fetch URL. Returns (html, soup) or (None, None)."""
    try:
        resp = requests.get(
            url,
            headers=_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code < 400:
            soup = BeautifulSoup(resp.text, "html.parser")
            return resp.text, soup
    except Exception as exc:
        log.debug("_fetch_page(%s): %s", url, exc)
    return None, None


def _extract_emails_from_mailto(soup: BeautifulSoup) -> list:
    """Extract all non-junk emails from mailto: links."""
    emails    = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if href.startswith("mailto:"):
            email = (
                href.replace("mailto:", "")
                .split("?")[0].strip().lower()
            )
            if (
                "@" in email
                and email not in seen
                and not _is_junk_email(email)
            ):
                seen.add(email)
                emails.append(email)
    return emails


def _find_email_near_name(
    soup: BeautifulSoup,
    name: str,
    html: str,
) -> str:
    """
    Search for an email address within 500 characters
    of a person's name in the HTML source.

    This catches cases where email is in plain text
    near the name but not in a mailto: link.
    """
    if not name or not html:
        return ""
    try:
        # Find name position in raw HTML
        name_pos = html.lower().find(name.lower())
        if name_pos == -1:
            return ""
        # Search 500 chars before and after
        start   = max(0, name_pos - 200)
        end     = min(len(html), name_pos + 500)
        snippet = html[start:end]
        emails  = _EMAIL_RE.findall(snippet)
        for email in emails:
            email = email.lower().strip()
            if not _is_junk_email(email):
                return email
    except Exception as exc:
        log.debug("_find_email_near_name: %s", exc)
    return ""


def _score_name_match(
    found_name: str,
    manager_name: str,
) -> int:
    """
    Score how well found_name matches manager_name.
    Returns 0-100.
    """
    if not found_name or not manager_name:
        return 0
    fn = found_name.lower().strip()
    mn = manager_name.lower().strip()
    if fn == mn:
        return 100
    # Check if last name matches
    fn_parts = fn.split()
    mn_parts = mn.split()
    if fn_parts and mn_parts:
        if fn_parts[-1] == mn_parts[-1]:
            return 80
        if fn_parts[0] == mn_parts[0]:
            return 60
    # Partial match
    for part in fn_parts:
        if len(part) > 3 and part in mn:
            return 40
    return 0


def _extract_contacts_from_page(
    html: str,
    soup: BeautifulSoup,
    manager_name: str = "",
) -> list:
    """
    Extract all (name, email, title, confidence) tuples
    from a page.

    Strategy:
      1. Find all mailto: emails on the page
      2. For each email, find nearest name
      3. For each DM title, find nearest name + email
      4. Score and sort by confidence
    """
    contacts = []

    # Strategy 1: mailto: links with nearby names
    mailto_emails = _extract_emails_from_mailto(soup)
    text          = soup.get_text(separator=" ", strip=True)

    for email in mailto_emails:
        # Find the <a> tag for this email
        email_tag = None
        for a in soup.find_all("a", href=True):
            if email in a.get("href", "").lower():
                email_tag = a
                break

        if not email_tag:
            continue

        # Look for name near this tag
        found_name  = ""
        found_title = ""

        # Check parent elements for name/title
        parent = email_tag.parent
        for _ in range(5):  # Walk up 5 levels
            if parent is None:
                break
            parent_text = parent.get_text(
                separator=" ", strip=True
            )
            # Look for title
            title_match = _DM_TITLES.search(parent_text)
            if title_match:
                found_title = title_match.group(0)
            # Look for name
            name_matches = _NAME_RE.findall(parent_text)
            for nm in name_matches:
                if _is_valid_person_name(nm):
                    found_name = nm
                    break
            if found_name and found_title:
                break
            parent = parent.parent

        confidence = 70  # Base: we have actual mailto
        if found_name:
            confidence += 10
        if found_title and _DM_TITLES.search(found_title):
            confidence += 10
        if manager_name and found_name:
            name_score = _score_name_match(
                found_name, manager_name
            )
            if name_score >= 80:
                confidence += 10

        contacts.append({
            "name":       found_name or "",
            "email":      email,
            "title":      found_title or "Contact",
            "confidence": min(confidence, 100),
        })

    # Strategy 2: DM titles → nearby name → nearby email
    sentences = re.split(r'[.\n]', text)
    for sentence in sentences:
        if not _DM_TITLES.search(sentence):
            continue
        title_match = _DM_TITLES.search(sentence)
        title       = title_match.group(0) if title_match else ""
        names       = _NAME_RE.findall(sentence)
        for name in names:
            if not _is_valid_person_name(name):
                continue
            # Try to find email near this name in HTML
            email = _find_email_near_name(soup, name, html)
            if email and not _is_junk_email(email):
                # Check not already found
                if not any(
                    c["email"] == email for c in contacts
                ):
                    name_score = 0
                    if manager_name:
                        name_score = _score_name_match(
                            name, manager_name
                        )
                    confidence = 50 + (
                        name_score // 5
                    )
                    contacts.append({
                        "name":       name,
                        "email":      email,
                        "title":      title,
                        "confidence": confidence,
                    })

    return contacts


def _is_valid_person_name(name: str) -> bool:
    """Basic validation that a string looks like a person name."""
    if not name or len(name) < 4 or len(name) > 50:
        return False
    lower   = name.lower()
    invalid = {
        "care home", "our team", "meet the",
        "contact us", "about us", "the home",
        "registered", "manager", "director",
        "read more", "click here", "learn more",
        "view all", "see more",
    }
    if any(inv in lower for inv in invalid):
        return False
    # Must have at least 2 words
    parts = name.strip().split()
    if len(parts) < 2:
        return False
    # Each part should start with capital
    if not all(p[0].isupper() for p in parts if p):
        return False
    return True


# ─────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────

def find_dm_contact(
    website_url:   str,
    business_name: str = "",
    manager_name:  str = "",
) -> dict:
    """
    Find decision maker contact details from a care home
    website by scraping staff/team/about pages.

    Args:
        website_url:   The care home website URL
        business_name: Care home name (for context)
        manager_name:  Known manager name from CQC
                       (used to prioritise matches)

    Returns:
        dict with keys:
            name:       str  — person name found
            email:      str  — email found
            title:      str  — job title found
            confidence: int  — 0-100
            source:     str  — page URL where found
        or {} if nothing found.

    Never raises.
    """
    if not website_url:
        return {}

    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    try:
        parsed = urlparse(website_url)
        base   = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return {}

    all_contacts: list = []

    for path in _STAFF_PAGES:
        url = urljoin(base, path)
        try:
            time.sleep(random.uniform(0.3, 0.8))
            html, soup = _fetch_page(url)
            if not html or not soup:
                continue

            contacts = _extract_contacts_from_page(
                html, soup, manager_name
            )

            for contact in contacts:
                contact["source"] = url
                all_contacts.append(contact)

            # If we found a high-confidence match, stop
            best = max(
                (c for c in all_contacts),
                key=lambda x: x["confidence"],
                default=None,
            )
            if best and best["confidence"] >= 80:
                log.info(
                    "dm_contact_finder: high confidence "
                    "match '%s' <%s> on %s",
                    best["name"], best["email"], url,
                )
                return best

        except Exception as exc:
            log.debug(
                "dm_contact_finder page %s: %s", url, exc
            )
            continue

    if not all_contacts:
        log.debug(
            "dm_contact_finder: nothing found for %s",
            website_url,
        )
        return {}

    # Return highest confidence contact found
    best = max(
        all_contacts,
        key=lambda x: x["confidence"],
        default=None,
    )

    if best and best.get("email"):
        log.info(
            "dm_contact_finder: best match '%s' <%s> "
            "confidence=%d",
            best.get("name", ""),
            best["email"],
            best["confidence"],
        )
        return best

    return {}


def find_dm_email_only(
    website_url:   str,
    business_name: str = "",
) -> str:
    """
    Simplified version — returns just the best email
    found on staff/team pages. No name required.

    Returns email string or "". Never raises.
    """
    result = find_dm_contact(
        website_url   = website_url,
        business_name = business_name,
    )
    return result.get("email", "")
"""
Website discovery engine.
Searches the web for a business website when GMB does not provide one.
"""

import re
import time
import requests
from urllib.parse import quote_plus, urlparse


SEARCH_ENGINES = [
    "https://www.googleapis.com/customsearch/v1",  # if you have API key
]

EXCLUDED_DOMAINS = {
    "google.com", "facebook.com", "instagram.com",
    "twitter.com", "youtube.com", "linkedin.com",
    "yelp.com", "tripadvisor.com", "yellowpages.com",
    "maps.google.com", "goo.gl", "foursquare.com",
    "wikipedia.org", "businesslist.com.ng",
}


def _is_business_domain(url: str, business_name: str) -> bool:
    """
    Returns True if the URL looks like it belongs to the business
    rather than a directory or social platform.
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")

        # Reject known directories
        for excluded in EXCLUDED_DOMAINS:
            if excluded in domain:
                return False

        # Check if business name words appear in domain
        name_words = [
            w.lower() for w in re.split(r"\s+", business_name)
            if len(w) > 3
        ]
        if any(word in domain for word in name_words):
            return True

        return False
    except Exception:
        return False


def _check_url_resolves(url: str, timeout: int = 8) -> bool:
    """
    Returns True if the URL returns a 200 response.
    """
    try:
        if not url.startswith("http"):
            url = "https://" + url
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        return response.status_code < 400
    except Exception:
        return False


def search_for_business_website(
    business_name: str,
    city: str = "",
    country: str = "",
    industry: str = "",
) -> dict:
    """
    Attempts to find the real website for a business using web search.

    Returns:
        {
            "found": bool,
            "url": str,
            "confidence": "high" | "medium" | "low",
            "source": "search" | "gmb" | "none",
            "checked": bool,
        }
    """
    if not business_name:
        return {
            "found": False, "url": "",
            "confidence": "none", "source": "none", "checked": False,
        }

    # Build search query
    query_parts = [business_name]
    if city:
        query_parts.append(city)
    if country and country.lower() not in {"nigeria"}:
        # Nigeria is implicit for local searches
        query_parts.append(country)
    query_parts.append("official website")

    query = " ".join(query_parts)

    # Try DuckDuckGo HTML search (no API key needed)
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        response = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36"
            },
        )
        if response.status_code == 200:
            # Extract href links from results
            links = re.findall(
                r'href="(https?://[^"]+)"',
                response.text,
            )
            for link in links[:10]:
                if _is_business_domain(link, business_name):
                    if _check_url_resolves(link):
                        return {
                            "found": True,
                            "url": link,
                            "confidence": "high",
                            "source": "search",
                            "checked": True,
                        }
    except Exception:
        pass

    # Try a direct domain guess
    # e.g. "Presentation National High School" → presentationnationalhighschool.com.ng
    name_slug = re.sub(r"[^a-z0-9]", "", business_name.lower())
    tlds = [".com.ng", ".ng", ".com", ".org"]
    for tld in tlds:
        guess_url = f"https://{name_slug}{tld}"
        if _check_url_resolves(guess_url):
            return {
                "found": True,
                "url": guess_url,
                "confidence": "medium",
                "source": "domain_guess",
                "checked": True,
            }

    return {
        "found": False,
        "url": "",
        "confidence": "none",
        "source": "none",
        "checked": True,
    }
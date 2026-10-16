# make_nigeria_files.py
# Run once: python make_nigeria_files.py
# Creates the two missing engine/nigeria/ files

import os

os.makedirs("engine/nigeria", exist_ok=True)

# ── File 1: nigeria_enricher.py ───────────────────────────────────────────

enricher = """\
# engine/nigeria/nigeria_enricher.py
# KNCS LeadGen Pro — Nigeria Lead Enricher

import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class NigeriaEnricher:
    def enrich_lead(self, lead: dict) -> dict:
        enriched = dict(lead)
        name = enriched.get("business_name", "")
        if not name:
            return enriched
        try:
            web_data = self._search_web(name)
            if web_data.get("phone") and not enriched.get("phone"):
                enriched["phone"] = web_data["phone"]
            if web_data.get("email") and not enriched.get("email"):
                enriched["email"] = web_data["email"]
            if web_data.get("website") and not enriched.get("website"):
                enriched["website"] = web_data["website"]
        except Exception as exc:
            log.warning("NigeriaEnricher error: %s", exc)
        return enriched

    def _search_web(self, business_name: str) -> dict:
        result = {"phone": "", "email": "", "website": ""}
        try:
            query = urllib.parse.quote(
                f"{business_name} Nigeria contact phone email"
            )
            url = f"https://www.google.com/search?q={query}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            phone_match = re.search(
                r"(\+?234[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{4}"
                r"|0[789][01]\d{8})",
                html,
            )
            if phone_match:
                result["phone"] = phone_match.group(0).strip()

            email_match = re.search(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                html,
            )
            if email_match:
                email = email_match.group(0)
                if not any(x in email for x in [
                    "google", "schema", "w3.org", "example"
                ]):
                    result["email"] = email
        except Exception as exc:
            log.debug("_search_web failed: %s", exc)
        return result
"""

# ── File 2: decision_maker_finder.py ─────────────────────────────────────

finder = """\
# engine/nigeria/decision_maker_finder.py
# KNCS LeadGen Pro — Nigeria Decision Maker Finder

import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

NICHE_TITLES = {
    "private_school":   "School Proprietor",
    "private_hospital": "Medical Director",
    "real_estate":      "Managing Director",
    "hotel":            "General Manager",
    "manufacturing":    "Factory Director",
    "construction":     "Managing Director",
    "law_firm":         "Senior Partner",
}


class DecisionMakerFinder:
    def find(
        self,
        business_name: str,
        website: str = "",
        city: str = "",
        niche: str = "",
    ) -> dict:
        result = {
            "decision_maker_name":  "",
            "decision_maker_title": NICHE_TITLES.get(niche, "Director"),
            "dm_email":             "",
            "dm_phone":             "",
            "dm_source":            "",
        }
        if not business_name:
            return result
        try:
            name, source = self._search_for_dm(
                business_name, city, niche
            )
            if name:
                result["decision_maker_name"] = name
                result["dm_source"] = source
        except Exception as exc:
            log.warning(
                "DecisionMakerFinder error for %s: %s",
                business_name, exc,
            )
        return result

    def _search_for_dm(
        self,
        business_name: str,
        city: str,
        niche: str,
    ) -> tuple:
        title = NICHE_TITLES.get(niche, "Director")
        query = urllib.parse.quote(
            f"{business_name} {city} {title} Nigeria"
        )
        url = f"https://www.google.com/search?q={query}"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            patterns = [
                rf"{title}[,\s]+([A-Z][a-z]+ (?:[A-Z][a-z]+ )?[A-Z][a-z]+)",
                r"(?:CEO|MD|Director|Founder|Owner|Principal)"
                r"[,\s]+([A-Z][a-z]+ (?:[A-Z][a-z]+ )?[A-Z][a-z]+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    name = match.group(1).strip()
                    if 5 < len(name) < 50:
                        return name, "web_search"
        except Exception as exc:
            log.debug("_search_for_dm failed: %s", exc)
        return "", ""
"""

# ── Write both files ──────────────────────────────────────────────────────

with open("engine/nigeria/nigeria_enricher.py", "w", encoding="utf-8") as f:
    f.write(enricher)
print("✓ Created: engine/nigeria/nigeria_enricher.py")

with open("engine/nigeria/decision_maker_finder.py", "w", encoding="utf-8") as f:
    f.write(finder)
print("✓ Created: engine/nigeria/decision_maker_finder.py")

# ── Verify ────────────────────────────────────────────────────────────────

import os
files = os.listdir("engine/nigeria")
print("\nengine/nigeria/ contents:")
for f in sorted(files):
    print(f"  {f}")

expected = {
    "__init__.py",
    "google_maps_client.py",
    "nigeria_enricher.py",
    "decision_maker_finder.py",
}
missing = expected - set(files)
if missing:
    print(f"\nSTILL MISSING: {missing}")
else:
    print("\nAll 4 files present. Run: streamlit run app.py")
# engine/__init__.py
# KNCS LeadGen Pro — Engine package initialiser
# Provides safe re-exports for all engine submodules.
# Import failures are caught and logged — never crash the app.

import logging

log = logging.getLogger(__name__)

# ── companies_house ────────────────────────────────────────────────────────
# care_acquisition_signals.py imports: from engine import companies_house
# This re-exports companies_house_scraper.py as the 'companies_house' name.

try:
    import engine.companies_house_scraper as companies_house
except Exception as _e:
    log.warning("engine: companies_house_scraper unavailable: %s", _e)
    companies_house = None  # type: ignore[assignment]

# ── cqc_client ────────────────────────────────────────────────────────────
# Imported by care_acquisition_signals as: from engine import cqc_client

try:
    import engine.cqc_client as cqc_client  # type: ignore[assignment]
except Exception as _e:
    log.warning("engine: cqc_client unavailable: %s", _e)
    cqc_client = None  # type: ignore[assignment]

__all__ = [
    "companies_house",
    "cqc_client",
]
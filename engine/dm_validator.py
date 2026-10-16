# engine/dm_validator.py
# KNCS LeadGen Pro — v10.4
# Multi-Source Decision Maker Validation & Election System
#
# Fixes vs v10.3:
#   BUG-DMV-01: confidence==0 check → confidence is None
#   BUG-DMV-02: names_match() — first AND last must both match
#   BUG-DMV-03: Rule 5 removed — two fallback sources agreeing
#               is not sufficient evidence
#   BUG-DMV-04: register() now calls clean_decision_maker_name()
#   BUG-DMV-05: candidates_agreeing documented as audit field only

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Confidence score constants
# ─────────────────────────────────────────────────────────

class DMSource:
    COMPANIES_HOUSE_API     = ("companies_house_api",     100)
    COMPANIES_HOUSE_WEB     = ("companies_house_web",      85)
    CQC_REGISTERED_MANAGER  = ("cqc_registered_manager",   90)
    B2BHINT_DEEP            = ("b2bhint_deep",             70)
    WEBSITE_ABOUT_PAGE      = ("website_about_page",       60)
    WEBSITE_CONTACT_PAGE    = ("website_contact_page",     30)
    WEBSITE_META_AUTHOR     = ("website_meta_author",      20)
    WEBSITE_HOMEPAGE        = ("website_homepage",         25)
    GOOGLE_LINKEDIN_SNIPPET = ("google_linkedin_snippet",  75)
    GOOGLE_OTHER_SNIPPET    = ("google_other_snippet",     40)
    SOCIAL_OSINT            = ("social_osint",             65)
    FALLBACK_SEARCH         = ("fallback_search",          15)
    MANUAL                  = ("manual",                  100)

    DEFAULT_CONFIDENCE: Dict[str, int] = {
        name: score for name, score in [
            COMPANIES_HOUSE_API,
            COMPANIES_HOUSE_WEB,
            CQC_REGISTERED_MANAGER,
            B2BHINT_DEEP,
            WEBSITE_ABOUT_PAGE,
            WEBSITE_CONTACT_PAGE,
            WEBSITE_META_AUTHOR,
            WEBSITE_HOMEPAGE,
            GOOGLE_LINKEDIN_SNIPPET,
            GOOGLE_OTHER_SNIPPET,
            SOCIAL_OSINT,
            FALLBACK_SEARCH,
            MANUAL,
        ]
    }

    @classmethod
    def get_confidence(cls, source: str) -> int:
        return cls.DEFAULT_CONFIDENCE.get(source, 25)


# ─────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────

SINGLE_SOURCE_HIGH_THRESHOLD  = 70
DUAL_SOURCE_MEDIUM_THRESHOLD  = 50
DUAL_SOURCE_LOW_THRESHOLD     = 30
ABSOLUTE_MINIMUM_THRESHOLD    = 15


# ─────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────

@dataclass
class DMCandidate:
    name: str
    title: str = ""
    source: str = ""
    confidence: int = 0
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        # BUG-DMV-01: check None not == 0
        if self.confidence is None or self.confidence == 0:
            if self.source:
                self.confidence = DMSource.get_confidence(
                    self.source
                )

    @property
    def name_normalized(self) -> str:
        n = self.name.lower().strip()
        n = re.sub(r'[^\w\s]', '', n)
        n = re.sub(r'\s+', ' ', n)
        return n

    def names_match(self, other: DMCandidate) -> bool:
        """
        BUG-DMV-02: Both first name AND last name must match.
        Previously any 2 words in common triggered a match.
        """
        if not self.name_normalized or not other.name_normalized:
            return False

        if self.name_normalized == other.name_normalized:
            return True

        parts_self  = self.name_normalized.split()
        parts_other = other.name_normalized.split()

        if len(parts_self) < 2 or len(parts_other) < 2:
            return self.name_normalized == other.name_normalized

        # First name must match
        if parts_self[0] != parts_other[0]:
            return False

        # Last name must match
        if parts_self[-1] != parts_other[-1]:
            return False

        return True


@dataclass
class DMElectionResult:
    name: Optional[str] = None
    title: Optional[str] = None
    dm_source: Optional[str] = None
    confidence: int = 0
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    elected: bool = False
    reason: str = ""
    candidates_considered: int = 0
    candidates_agreeing: int = 0


# ─────────────────────────────────────────────────────────
# Candidate registry
# ─────────────────────────────────────────────────────────

class DMCandidateRegistry:

    def __init__(self, business_name: str = ""):
        self.business_name  = business_name
        self.candidates: List[DMCandidate] = []
        self._elected: Optional[DMElectionResult] = None

    def register(
        self,
        name: str,
        title: str = "",
        source: str = "",
        confidence: Optional[int] = None,
        email: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        if not name or not isinstance(name, str):
            return

        # BUG-DMV-04: clean through full pipeline
        try:
            from engine.enricher import clean_decision_maker_name
            name = clean_decision_maker_name(name.strip())
        except ImportError:
            name = name.strip()

        if not name or len(name) < 3:
            return

        bad_values = {
            "", "n/a", "none", "null", "unknown", "anonymous",
            "team", "staff", "contact", "management",
            "registered manager", "manager post vacant",
        }
        if name.lower() in bad_values:
            return

        conf = (
            confidence
            if confidence is not None
            else DMSource.get_confidence(source)
        )

        candidate = DMCandidate(
            name=name,
            title=title,
            source=source,
            confidence=conf,
            email=email,
            linkedin_url=linkedin_url,
            metadata=metadata or {},
        )
        self.candidates.append(candidate)
        logger.debug(
            "DMValidator: registered '%s' from '%s' (conf=%d)",
            name, source, conf,
        )

    def elect(self) -> DMElectionResult:
        if self._elected is not None:
            return self._elected

        result = DMElectionResult(
            candidates_considered=len(self.candidates),
        )

        if not self.candidates:
            result.reason = "No candidates registered"
            self._elected = result
            return result

        sorted_candidates = sorted(
            self.candidates,
            key=lambda c: c.confidence,
            reverse=True,
        )
        best = sorted_candidates[0]

        # Rule 1: Single high-confidence source
        if best.confidence >= SINGLE_SOURCE_HIGH_THRESHOLD:
            self._set_elected(
                result, best, "single_high_confidence"
            )
            return result

        # Rule 2: Two medium sources agreeing
        for i, c1 in enumerate(sorted_candidates):
            if c1.confidence < DUAL_SOURCE_MEDIUM_THRESHOLD:
                continue
            for c2 in sorted_candidates[i + 1:]:
                if c2.confidence < DUAL_SOURCE_MEDIUM_THRESHOLD:
                    continue
                if c1.names_match(c2):
                    elected = (
                        c1 if c1.confidence >= c2.confidence
                        else c2
                    )
                    result.candidates_agreeing = 2
                    self._set_elected(
                        result, elected,
                        f"dual_medium_agreement: "
                        f"{c1.source} + {c2.source}",
                    )
                    return result

        # Rule 3: One medium + one low source agreeing
        for c1 in sorted_candidates:
            if c1.confidence < DUAL_SOURCE_MEDIUM_THRESHOLD:
                continue
            for c2 in sorted_candidates:
                if c2 is c1:
                    continue
                if c2.confidence < DUAL_SOURCE_LOW_THRESHOLD:
                    continue
                if c1.names_match(c2):
                    result.candidates_agreeing = 2
                    self._set_elected(
                        result, c1,
                        f"medium_low_agreement: "
                        f"{c1.source} + {c2.source}",
                    )
                    return result

        # Rule 4: Single medium source
        if best.confidence >= DUAL_SOURCE_MEDIUM_THRESHOLD:
            self._set_elected(
                result, best, "single_medium_confidence"
            )
            return result

        # BUG-DMV-03: Rule 5 removed — two fallback sources
        # agreeing is not sufficient evidence for election.
        # No candidate meets minimum threshold.
        result.reason = (
            f"Best candidate '{best.name}' from '{best.source}' "
            f"has confidence {best.confidence} — below threshold"
        )
        result.elected = False
        self._elected  = result
        logger.info(
            "DMValidator: NO election for '%s' — %s",
            self.business_name, result.reason,
        )
        return result

    def _set_elected(
        self,
        result: DMElectionResult,
        candidate: DMCandidate,
        reason: str,
    ) -> None:
        result.name       = candidate.name
        result.title      = candidate.title
        result.dm_source  = candidate.source
        result.confidence = candidate.confidence
        result.email      = candidate.email
        result.linkedin_url = candidate.linkedin_url
        result.elected    = True
        result.reason     = reason
        result.candidates_agreeing = max(
            result.candidates_agreeing, 1
        )
        self._elected = result
        logger.info(
            "DMValidator: ELECTED '%s' for '%s' — %s "
            "(conf=%d, src=%s)",
            candidate.name, self.business_name, reason,
            candidate.confidence, candidate.source,
        )

    def get_all_candidates(self) -> List[Dict]:
        return [
            {
                "name":       c.name,
                "title":      c.title,
                "source":     c.source,
                "confidence": c.confidence,
                "email":      c.email,
                "linkedin_url": c.linkedin_url,
            }
            for c in sorted(
                self.candidates,
                key=lambda x: x.confidence,
                reverse=True,
            )
        ]


# ─────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────

def validate_and_elect_dm(
    registry: DMCandidateRegistry,
    business_name: str = "",
    min_confidence: int = ABSOLUTE_MINIMUM_THRESHOLD,
) -> DMElectionResult:
    if not registry.business_name and business_name:
        registry.business_name = business_name

    result = registry.elect()

    if result.elected and result.confidence < min_confidence:
        result.elected = False
        result.reason  = (
            f"Elected confidence {result.confidence} below "
            f"minimum {min_confidence}"
        )
    return result


def normalize_dm_name_for_comparison(name: str) -> str:
    if not name:
        return ""
    n = name.lower().strip()
    n = re.sub(
        r'^(mr|mrs|ms|miss|dr|prof|sir|lord|dame)\s+', '', n
    )
    n = re.sub(
        r'\s+(jr|sr|ii|iii|iv|phd|md|mbchb|bds)$', '', n
    )
    n = re.sub(r'[^\w\s]', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


__all__ = [
    "DMSource",
    "DMCandidate",
    "DMCandidateRegistry",
    "DMElectionResult",
    "validate_and_elect_dm",
    "normalize_dm_name_for_comparison",
]
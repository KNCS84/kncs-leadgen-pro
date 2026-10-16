# engine/script_generator.py
"""
KNCS LeadGen Pro — v9.7
Sales call script generator.

Provides:
- generate_script()          ← main entry point
- generate_script_with_ai()  ← Cloudflare AI generation
- get_script_template()      ← template fallback
- generate_call_script()     ← backward-compatibility alias

Falls back to templates automatically if Cloudflare AI
is not configured or the API call fails.

v9.7 compatibility:
- Uses v9.7 field names throughout (no rating/reviews/
  has_instagram/has_facebook/website_active)
- Template selection uses website, social_*_url,
  fit_score, lead_priority, pain_points
- market-aware: Nigerian leads get ₦ pricing,
  UK leads get £ pricing
- generate_script() never raises — always returns a string
"""

import requests
from typing import Optional

__all__ = [
    "generate_script",
    "generate_script_with_ai",
    "get_script_template",
    "generate_call_script",
]

# ─────────────────────────────────────────────────────────
# Config import — graceful fallback if config.py absent
# ─────────────────────────────────────────────────────────

try:
    from config import (
        CLOUDFLARE_ACCOUNT_ID,
        CLOUDFLARE_API_TOKEN,
        CF_MODEL,
        CF_AI_ENABLED,
    )
    AI_ACCOUNT_ID = CLOUDFLARE_ACCOUNT_ID
    AI_API_TOKEN  = CLOUDFLARE_API_TOKEN
    AI_MODEL      = CF_MODEL
    AI_ENABLED    = CF_AI_ENABLED

except Exception:
    AI_ACCOUNT_ID = ""
    AI_API_TOKEN  = ""
    AI_MODEL      = "@cf/meta/llama-3.1-8b-instruct"
    AI_ENABLED    = False


# ─────────────────────────────────────────────────────────
# Script templates — v9.7 compatible placeholders only
# ─────────────────────────────────────────────────────────
# Placeholders used:
#   {business_name}  — always available
#   {currency}       — ₦ for Nigeria/Africa, £ for UK
#   {city}           — from lead dict
# No rating/reviews/website_active placeholders
# ─────────────────────────────────────────────────────────

SCRIPT_TEMPLATES = {

    "no_website": """
📞 COLD CALL SCRIPT — NO WEBSITE

OPENING:
"Hello, may I speak with the owner or manager please?"

HOOK:
"My name is [Your Name]. I was looking at {business_name}
and noticed it does not appear to have a website yet."

PROBLEM:
"Right now, most customers search online before calling
or visiting a business. Without a website, many of those
customers may go to competitors instead."

SOLUTION:
"I help local businesses like yours get a simple,
professional website that attracts more customers —
often within days, not months."

OBJECTION HANDLING:
- "We're too busy"    → "That's exactly why I handle everything for you."
- "Too expensive"     → "We have starter packages from {currency}100K."
- "We get referrals"  → "Imagine doubling that with online visibility."

CLOSE:
"Would you be open to a short conversation this week
so I can show you what that could look like for {business_name}?"
""",

    "dead_website": """
📞 COLD CALL SCRIPT — BROKEN WEBSITE

OPENING:
"Hello, is this the owner or manager of {business_name}?"

HOOK:
"I tried to visit your website and it doesn't seem
to be loading correctly right now."

PROBLEM:
"When a website is broken or slow, potential customers
often assume the business is closed or unreliable.
You could be losing leads right now without knowing it."

SOLUTION:
"I specialise in fixing and upgrading local business
websites quickly. I can have yours back up and looking
professional within days."

OBJECTION HANDLING:
- "We know about it"    → "Then let's get it fixed fast — every day costs you customers."
- "Too expensive"       → "We have packages from {currency}100K. Much cheaper than lost business."

CLOSE:
"Can I show you what we can fix and improve this week?"
""",

    "has_social": """
📞 COLD CALL SCRIPT — HAS SOCIAL MEDIA, NO WEBSITE

OPENING:
"Hello, may I speak with the owner or manager?"

HOOK:
"I can see {business_name} is active on social media —
that's great. But I noticed you don't have a proper
website yet to back that up."

PROBLEM:
"Social media alone is not enough. Customers who find
you on Google are much more likely to buy than casual
social media followers — and right now you're invisible
on Google."

SOLUTION:
"I help businesses like yours turn their social
presence into a full digital strategy that generates
consistent leads — starting with a proper website
that ranks on Google."

OBJECTION HANDLING:
- "Social media is enough"  → "It helps — but 70% of buying decisions start on Google."
- "Too expensive"           → "Packages start from {currency}100K. What's one new customer worth?"

CLOSE:
"Could we talk for 10 minutes this week about
what that could look like for {business_name}?"
""",

    "high_fit": """
📞 COLD CALL SCRIPT — HIGH PRIORITY LEAD

OPENING:
"Hello, may I speak with the owner or manager of {business_name}?"

HOOK:
"My name is [Your Name]. I help businesses in {city}
attract more customers online, and I came across
{business_name} — I think there's a real opportunity here."

PROBLEM:
"Most of your competitors in {city} already have
professional websites and are getting found on Google.
Without a strong online presence, you're leaving
customers on the table every single day."

SOLUTION:
"I can have {business_name} ranking on Google and
looking professional online within days — not months."

OBJECTION HANDLING:
- "Not interested"    → "I understand. Can I send you examples of what I've done for similar businesses?"
- "Call back later"   → "Of course. What time works best — morning or afternoon?"
- "We have someone"   → "Great — I'd love to show you how we can complement what you have."

CLOSE:
"I have time this week for a quick 10-minute call.
Would tomorrow or the day after work for you?"
""",

    "uk_default": """
📞 COLD CALL SCRIPT — UK MARKET

OPENING:
"Hello, may I speak with the owner or manager please?"

HOOK:
"Good morning/afternoon. My name is [Your Name].
I help local businesses across the UK improve their
online presence and attract more customers."

PROBLEM:
"Many businesses in {city} are missing out on customers
simply because their website is outdated or hard to find
on Google."

SOLUTION:
"I can help {business_name} get a professional website
that actually brings in enquiries — starting from £500."

OBJECTION HANDLING:
- "Not interested"       → "Understood. Could I send you a quick case study?"
- "We have a website"    → "Great — when did you last update it? Google rewards fresh sites."
- "Call me next month"   → "Of course. Shall I put a reminder in for the first?"

CLOSE:
"Would you be open to a 10-minute call this week
to see what's possible for {business_name}?"
""",

    "default": """
📞 COLD CALL SCRIPT

OPENING:
"Hello, may I speak with the owner or manager please?"

HOOK:
"My name is [Your Name] and I help local businesses
improve their online presence and attract more customers."

PROBLEM:
"Many businesses in your area are losing potential
customers because their digital presence is weak
or incomplete."

SOLUTION:
"I can help {business_name} get more visibility online,
build trust with customers, and generate more enquiries."

OBJECTION HANDLING:
- "Not interested"      → "I understand. Can I send you some info just in case?"
- "Call back later"     → "Of course. What time works best?"
- "We do it ourselves"  → "I can show you how to make it even more effective."

CLOSE:
"Would you be open to a quick 10-minute call this week?"
""",
}


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _get_currency(lead: dict) -> str:
    """
    Return currency symbol based on lead market/country.
    Nigeria/Africa → ₦
    UK/Europe      → £
    Default        → ₦ (primary market)
    """
    market  = (lead.get("market") or "").lower()
    country = (lead.get("country") or "").lower()

    if market in ("uk", "europe") or "united kingdom" in country:
        return "£"
    return "₦"


def _has_social(lead: dict) -> bool:
    """Return True if lead has any discovered social URL."""
    return bool(
        lead.get("social_instagram_url")
        or lead.get("social_facebook_url")
        or lead.get("social_linkedin_url")
    )


def _is_high_fit(lead: dict) -> bool:
    """Return True if lead is high priority per v9.7 labels."""
    priority   = (lead.get("lead_priority") or "").lower()
    fit_score  = lead.get("fit_score")
    if priority == "high":
        return True
    if fit_score is not None:
        try:
            return float(fit_score) >= 8
        except (ValueError, TypeError):
            pass
    return False


def _is_uk_market(lead: dict) -> bool:
    """Return True if lead is in UK market."""
    market  = (lead.get("market") or "").lower()
    country = (lead.get("country") or "").lower()
    return market == "uk" or "united kingdom" in country


# ─────────────────────────────────────────────────────────
# Template selector
# ─────────────────────────────────────────────────────────

def get_script_template(lead: dict) -> str:
    """
    Return the best script template based on v9.7 lead data.

    Selection logic (in priority order):
    1. No website + has social media → has_social template
    2. No website                    → no_website template
    3. Website present but broken    → dead_website template
       (inferred: website field set but website_discovered=1
        with no confirmed live site)
    4. High fit score / high priority → high_fit template
    5. UK market                     → uk_default template
    6. Default                       → default template

    Uses v9.7 fields only:
    - website, website_discovered, social_*_url,
    - fit_score, lead_priority, market, country, city
    """
    if not lead:
        return SCRIPT_TEMPLATES["default"]

    business_name = lead.get("business_name") or "your business"
    city          = lead.get("city") or "your area"
    currency      = _get_currency(lead)
    website       = (lead.get("website") or "").strip()

    # Select template — order matters
    if not website and _has_social(lead):
        key = "has_social"

    elif not website:
        key = "no_website"

    elif website and _is_high_fit(lead):
        # Has a website but scored high — competitor angle
        key = "high_fit"

    elif _is_uk_market(lead):
        key = "uk_default"

    else:
        key = "default"

    template = SCRIPT_TEMPLATES[key]

    # Safe format — only known safe placeholders
    try:
        return template.format(
            business_name=business_name,
            city=city,
            currency=currency,
        )
    except Exception:
        # If any placeholder fails, return unformatted template
        return template


# ─────────────────────────────────────────────────────────
# AI generation
# ─────────────────────────────────────────────────────────

def generate_script_with_ai(lead: dict) -> str:
    """
    Generate a personalised script using Cloudflare Workers AI.

    Falls back to get_script_template() if:
    - AI not configured (AI_ENABLED=False)
    - API call fails or returns empty
    - Response too short to be useful

    NOTE: This makes a direct API call — it does not use
    ai_client.py's retry/circuit-breaker/cache infrastructure.
    Acceptable for this optional, infrequent feature.
    For high-volume scoring use scorer.py → ai_client.py instead.
    """
    if not AI_ENABLED or not AI_ACCOUNT_ID or not AI_API_TOKEN:
        return get_script_template(lead)

    # v9.7 field names only
    business_name = lead.get("business_name") or "Unknown"
    industry      = lead.get("industry") or "Local business"
    city          = lead.get("city") or "the area"
    country       = lead.get("country") or ""
    website       = lead.get("website") or "None"
    has_email     = "Yes" if lead.get("email") else "No"
    market        = (lead.get("market") or "").lower()
    currency      = _get_currency(lead)
    has_soc       = "Yes" if _has_social(lead) else "No"
    dm_name       = lead.get("decision_maker_name") or ""

    # fit_score guard — may be None if unscored
    fit_score = lead.get("fit_score")
    try:
        score_display = f"{float(fit_score):.1f}/10"
    except (TypeError, ValueError):
        score_display = "unscored"

    # pain_points — stored as JSON list string in DB
    pain_points_raw = lead.get("pain_points") or ""
    pain_points     = pain_points_raw
    if pain_points_raw and pain_points_raw.startswith("["):
        try:
            import json
            parsed = json.loads(pain_points_raw)
            if isinstance(parsed, list):
                pain_points = ", ".join(str(p) for p in parsed)
        except Exception:
            pass

    dm_line = (
        f"\n- Decision Maker: {dm_name}" if dm_name else ""
    )

    prompt = f"""Write a short, persuasive cold call script for selling website design services to a local business.

Business Details:
- Name: {business_name}
- Industry: {industry}
- Location: {city}{', ' + country if country else ''}
- Market: {market or 'nigeria'}
- Website: {website}
- Has Social Media: {has_soc}
- Has Email on file: {has_email}
- Fit Score: {score_display}
- Known Pain Points: {pain_points or 'not yet identified'}{dm_line}
- Currency for pricing: {currency}

Requirements:
- Greeting and introduction (use decision maker name if provided)
- Hook specific to their situation (website status, social presence)
- Clear problem statement relevant to their industry
- Simple solution offer with local pricing in {currency}
- Handle 2 common objections
- Strong close with specific time ask
- Under 300 words
- Conversational and natural phone tone
- Primary market context: {'UK — professional and formal' if 'uk' in market else 'Nigeria — warm and direct'}
"""

    try:
        url = (
            f"https://api.cloudflare.com/client/v4/"
            f"accounts/{AI_ACCOUNT_ID}/ai/run/{AI_MODEL}"
        )

        headers = {
            "Authorization": f"Bearer {AI_API_TOKEN}",
            "Content-Type":  "application/json",
        }

        payload = {
            "messages": [
                {
                    "role":    "system",
                    "content": (
                        "You are an expert sales coach specialising "
                        "in digital marketing for local businesses. "
                        "Write short, effective cold call scripts "
                        "that sound natural on the phone."
                    ),
                },
                {
                    "role":    "user",
                    "content": prompt,
                },
            ],
            "max_tokens": 600,
        }

        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        # Handle rate limit — wait and fall back to template
        if resp.status_code == 429:
            print(
                f"⚠️  Script AI rate limited for "
                f"'{business_name}' — using template"
            )
            return get_script_template(lead)

        if resp.status_code == 200:
            data = resp.json()

            if data.get("success"):
                result = data.get("result", {})

                # result can be dict or string depending on model
                if isinstance(result, dict):
                    response = result.get("response", "")
                elif isinstance(result, str):
                    response = result
                else:
                    response = ""

                if response and len(response) > 50:
                    return response

    except Exception as exc:
        print(
            f"⚠️  AI script error for "
            f"'{business_name}': {exc}"
        )

    return get_script_template(lead)


# ─────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────

def generate_script(lead: dict) -> str:
    """
    Main script generation entry point.

    Tries AI first if configured.
    Always falls back to templates.
    Never raises an exception — always returns a string.

    Args:
        lead: v9.7-compatible lead dict from DB

    Returns:
        Formatted call script string
    """
    if not lead:
        return SCRIPT_TEMPLATES["default"]

    try:
        return generate_script_with_ai(lead)
    except Exception:
        pass

    try:
        return get_script_template(lead)
    except Exception:
        pass

    return SCRIPT_TEMPLATES["default"]


# ─────────────────────────────────────────────────────────
# Backward-compatibility alias
# ─────────────────────────────────────────────────────────

def generate_call_script(lead: dict) -> str:
    """
    Backward-compatible alias for generate_script().
    Used by call_center.py and any older callers.
    """
    return generate_script(lead)
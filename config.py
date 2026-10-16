# config.py
"""
LeadGen Pro — Configuration

All settings, thresholds and constants.
Environment variables loaded from .env file.

.env file format:
    APP_NAME=LeadGen Pro
    DEBUG=True
    CLOUDFLARE_ACCOUNT_ID=your_id_here
    CLOUDFLARE_API_TOKEN=your_token_here
"""

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ─────────────────────────────────────────────────────────
# App identity
# ─────────────────────────────────────────────────────────

APP_NAME    = os.getenv("APP_NAME", "LeadGen Pro")
APP_VERSION = "3.1.0"
DEBUG       = os.getenv("DEBUG", "True") == "True"


# ─────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────

DB_PATH = os.getenv("DB_PATH", "data/leads.db")


# ─────────────────────────────────────────────────────────
# Cloudflare AI
# ─────────────────────────────────────────────────────────

CLOUDFLARE_ACCOUNT_ID = os.getenv(
    "CLOUDFLARE_ACCOUNT_ID", ""
)
CLOUDFLARE_API_TOKEN = os.getenv(
    "CLOUDFLARE_API_TOKEN", ""
)

# Aliases for backward compatibility
CF_ACCOUNT_ID = CLOUDFLARE_ACCOUNT_ID
CF_API_TOKEN  = CLOUDFLARE_API_TOKEN

CF_AI_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CF_ACCOUNT_ID}/ai/run"
)

# Cloudflare model to use for script generation
CF_MODEL = "@cf/meta/llama-3-8b-instruct"

# Whether AI is available
CF_AI_ENABLED = bool(
    CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN
)


# ─────────────────────────────────────────────────────────
# Scraping settings
# ─────────────────────────────────────────────────────────

HEADLESS_MODE = os.getenv(
    "HEADLESS_MODE", "True"
) == "True"

MAX_RESULTS   = int(os.getenv("MAX_RESULTS", "100"))
MAX_PAGES     = int(os.getenv("MAX_PAGES", "5"))


# ─────────────────────────────────────────────────────────
# Request settings
# ─────────────────────────────────────────────────────────

REQUEST_TIMEOUT    = 15
REQUEST_MIN_DELAY  = 1.0
REQUEST_MAX_DELAY  = 3.5

SCRAPE_MIN_DELAY   = 2.0
SCRAPE_MAX_DELAY   = 6.0


# ─────────────────────────────────────────────────────────
# Daily scraping limits per source
# ─────────────────────────────────────────────────────────

DAILY_LIMITS = {
    "google_maps": {
        "safe":    50,
        "caution": 100,
        "warning": 150,
        "stop":    200,
    },
    "b2bhint": {
        "safe":    200,
        "caution": 400,
        "warning": 600,
        "stop":    800,
    },
    "google_search": {
        "safe":    30,
        "caution": 60,
        "warning": 100,
        "stop":    150,
    },
    "bing_search": {
        "safe":    50,
        "caution": 100,
        "warning": 150,
        "stop":    200,
    },
    "duckduckgo": {
        "safe":    50,
        "caution": 100,
        "warning": 150,
        "stop":    200,
    },
}


# ─────────────────────────────────────────────────────────
# Lead scoring thresholds
# ─────────────────────────────────────────────────────────

HOT_LEAD_SCORE  = 70
WARM_LEAD_SCORE = 50
COLD_LEAD_SCORE = 0


# ─────────────────────────────────────────────────────────
# Opportunity score weights
# ─────────────────────────────────────────────────────────

SCORE_NO_WEBSITE     = 40
SCORE_DEAD_WEBSITE   = 30
SCORE_LOW_REVIEWS    = 15
SCORE_HIGH_RATING    = 10
SCORE_HAS_INSTAGRAM  = 20
SCORE_NO_SOCIAL      = 20
SCORE_NO_PHONE       = 5
SCORE_FEW_PHOTOS     = 10


# ─────────────────────────────────────────────────────────
# Contact score weights
# ─────────────────────────────────────────────────────────

CONTACT_SCORE_PHONE   = 40
CONTACT_SCORE_EMAIL   = 35
CONTACT_SCORE_WEBSITE = 15
CONTACT_SCORE_ADDRESS = 10


# ─────────────────────────────────────────────────────────
# Enrichment settings
# ─────────────────────────────────────────────────────────

ENRICHMENT_MIN_EMAIL_CONFIDENCE = 40

ENRICHMENT_CONTACT_PAGES = [
    "",
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/staff",
    "/management",
    "/leadership",
    "/support",
    "/help",
    "/locations",
    "/location",
    "/reach-us",
    "/get-in-touch",
    "/info",
]


# ─────────────────────────────────────────────────────────
# Call center settings
# ─────────────────────────────────────────────────────────

CALLBACK_DEFAULT_DAYS  = 2
NO_ANSWER_RETRY_DAYS   = 1

VALID_CALL_STATUSES = [
    "new",
    "called",
    "interested",
    "not_interested",
    "callback",
    "no_answer",
    "wrong_number",
    "deal_closed",
]

SERVICE_TYPES = [
    "Website Development",
    "Google My Business",
    "Social Media Management",
    "SEO",
    "Full Digital Package",
    "Logo / Branding",
    "Email Marketing",
    "Other",
]


# ─────────────────────────────────────────────────────────
# Currency display
# ─────────────────────────────────────────────────────────

DEFAULT_CURRENCY        = "₦"
SUPPORTED_CURRENCIES    = ["₦ NGN", "$ USD", "£ GBP", "€ EUR"]


# ─────────────────────────────────────────────────────────
# Business categories
# ─────────────────────────────────────────────────────────

BUSINESS_CATEGORIES = [
    "Restaurant",
    "Hotel",
    "Pharmacy",
    "Clinic",
    "Hospital",
    "Dental Clinic",
    "School",
    "Salon",
    "Spa",
    "Gym",
    "Supermarket",
    "Real Estate",
    "Logistics",
    "Construction",
    "Printing",
    "Bakery",
    "Event Center",
    "Photography",
    "Law Firm",
    "Church",
    "Car Dealer",
    "Solar Energy",
    "Fashion / Boutique",
    "Travel Agency",
    "Insurance",
    "Custom Keyword",
]


# ─────────────────────────────────────────────────────────
# Location presets
# ─────────────────────────────────────────────────────────

LOCATION_PRESETS = {
    "Nigeria": [
        "Lagos",
        "Abuja",
        "Port Harcourt",
        "Kano",
        "Ibadan",
        "Enugu",
        "Benin City",
        "Warri",
        "Owerri",
        "Calabar",
    ],
    "United States": [
        "Chicago",
        "New York",
        "Los Angeles",
        "Houston",
        "Dallas",
        "Atlanta",
        "Miami",
        "Phoenix",
        "Seattle",
        "Denver",
        "Austin",
        "San Antonio",
        "Philadelphia",
        "San Diego",
        "San Francisco",
    ],
    "United Kingdom": [
        "London",
        "Manchester",
        "Birmingham",
        "Leeds",
        "Liverpool",
        "Edinburgh",
        "Glasgow",
        "Bristol",
        "Sheffield",
        "Cardiff",
    ],
    "Canada": [
        "Toronto",
        "Vancouver",
        "Montreal",
        "Calgary",
        "Ottawa",
        "Edmonton",
    ],
    "Ghana": [
        "Accra",
        "Kumasi",
        "Tamale",
    ],
    "South Africa": [
        "Johannesburg",
        "Cape Town",
        "Durban",
        "Pretoria",
    ],
    "Kenya": [
        "Nairobi",
        "Mombasa",
        "Kisumu",
    ],
    "Australia": [
        "Sydney",
        "Melbourne",
        "Brisbane",
        "Perth",
        "Adelaide",
    ],
    "India": [
        "Mumbai",
        "Delhi",
        "Bangalore",
        "Hyderabad",
        "Chennai",
    ],
    "UAE": [
        "Dubai",
        "Abu Dhabi",
        "Sharjah",
    ],
    "Germany": [
        "Berlin",
        "Munich",
        "Hamburg",
        "Frankfurt",
    ],
    "Custom": [],
}


# ─────────────────────────────────────────────────────────
# Debug helper
# ─────────────────────────────────────────────────────────

def print_config():
    """
    Print current config to terminal.
    Useful for debugging startup issues.
    """
    print("\n" + "=" * 50)
    print(f"  {APP_NAME} v{APP_VERSION}")
    print("=" * 50)
    print(f"  DB_PATH:      {DB_PATH}")
    print(f"  DEBUG:        {DEBUG}")
    print(
        f"  CF_AI:        "
        f"{'enabled' if CF_AI_ENABLED else 'disabled'}"
    )
    print(f"  HEADLESS:     {HEADLESS_MODE}")
    print(f"  MAX_RESULTS:  {MAX_RESULTS}")
    print("=" * 50 + "\n")


if DEBUG:
    print_config()
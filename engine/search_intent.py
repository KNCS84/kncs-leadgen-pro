# engine/search_intent.py
# KNCS LeadGen Pro v9.6
# Search Intent Layer — Industry to Google Query Mapping
# Owner: Amechi | Created: May 15, 2026

from typing import Optional
import re

INDUSTRY_SEARCH_MAP = {
    # ── Health & Medical ──────────────────────────────
    "Dentist":              "Dentist near me accepting new patients",
    "Orthodontist":         "Best orthodontist in [City]",
    "Chiropractor":         "Chiropractor in [City] same day appointment",
    "Optometrist":          "Eye doctor near me open today",
    "Physiotherapist":      "Physiotherapy near me",
    "GP / Doctor":          "Doctor near me taking new patients",
    "Dermatologist":        "Skin specialist near me",
    "Psychiatrist":         "Mental health doctor near me",
    "Psychologist":         "Therapist near me affordable",
    "Veterinarian":         "Vet near me emergency",

    # ── Home Services ─────────────────────────────────
    "Plumber":              "Emergency plumber near me 24 hour",
    "Electrician":          "Licensed electrician near me",
    "HVAC":                 "AC repair near me same day",
    "Roofer":               "Roofing contractor near me free quote",
    "Landscaper":           "Landscaping company near me",
    "Painter":              "House painter near me affordable",
    "Carpenter":            "Carpenter near me for hire",
    "Cleaner":              "House cleaning service near me",
    "Pest Control":         "Pest control near me same day",
    "Pool Service":         "Pool cleaning service near me",
    "Handyman":             "Handyman near me cheap",
    "Locksmith":            "Locksmith near me 24 hours",
    "Window Cleaner":       "Window cleaning service near me",
    "Garage Door Repair":   "Garage door repair near me urgent",

    # ── Food & Hospitality ────────────────────────────
    "Restaurant":           "Best restaurant near me open now",
    "Cafe":                 "Coffee shop near me open now",
    "Bakery":               "Bakery near me fresh bread",
    "Catering":             "Catering company near me for events",
    "Food Truck":           "Food truck near me today",
    "Pizza":                "Best pizza near me delivery",
    "Sushi":                "Sushi restaurant near me",
    "BBQ":                  "BBQ restaurant near me",
    "Vegan":                "Vegan restaurant near me",

    # ── Beauty & Wellness ─────────────────────────────
    "Hair Salon":           "Hair salon near me open today",
    "Barber":               "Barber shop near me walk in",
    "Nail Salon":           "Nail salon near me cheap",
    "Spa":                  "Day spa near me specials",
    "Massage":              "Massage near me same day booking",
    "Tattoo":               "Tattoo studio near me",
    "Eyebrows":             "Eyebrow threading near me",
    "Makeup Artist":        "Makeup artist near me for weddings",

    # ── Professional Services ─────────────────────────
    "Accountant":           "Accountant near me for small business",
    "Lawyer":               "Lawyer near me free consultation",
    "Financial Advisor":    "Financial advisor near me",
    "Insurance Agent":      "Insurance broker near me",
    "Real Estate Agent":    "Real estate agent near me reviews",
    "Mortgage Broker":      "Best mortgage broker near me",
    "Business Consultant":  "Business consultant near me",

    # ── Automotive ────────────────────────────────────
    "Auto Mechanic":        "Car mechanic near me open now",
    "Car Wash":             "Car wash near me open today",
    "Towing":               "Towing service near me emergency",
    "Auto Body":            "Auto body shop near me reviews",
    "Tyre Shop":            "Tyre shop near me cheapest",
    "Car Detailing":        "Car detailing near me mobile",

    # ── Education & Childcare ─────────────────────────
    "Tutor":                "Tutor near me for kids",
    "Childcare":            "Childcare centre near me with availability",
    "Driving School":       "Driving lessons near me cheap",
    "Dance Studio":         "Dance classes near me for beginners",
    "Music School":         "Music lessons near me",
    "Gym":                  "Gym near me open 24 hours",
    "Personal Trainer":     "Personal trainer near me affordable",
    "Yoga Studio":          "Yoga classes near me beginner",

    # ── Retail & Other ────────────────────────────────
    "Florist":              "Flower shop near me same day delivery",
    "Jeweller":             "Jewellery store near me",
    "Photography":          "Photographer near me for events",
    "Videography":          "Videographer near me for weddings",
    "Print Shop":           "Printing service near me fast",
    "IT Support":           "Computer repair near me same day",
    "Web Designer":         "Web designer near me for small business",
}


def _norm(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _inject_city(phrase: str, city: str) -> str:
    city = str(city or "").strip()
    if not city:
        # Make placeholders readable if city is missing
        return phrase.replace("[City]", "your area")

    if "[City]" in phrase:
        return phrase.replace("[City]", city)

    # Replace "near me" with "in City"
    return re.sub(r"(?i)\bnear me\b", f"in {city}", phrase, count=1)


def get_search_phrase(industry: str, city: str = "") -> str:
    """
    Returns a realistic Google search query for a given industry.

    Args:
        industry (str): The business category/industry.
        city (str): Optional city name to inject into the query.

    Returns:
        str: A Google-style search phrase.
    """
    industry_norm = _norm(industry)

    if not industry_norm:
        return "local business near me"

    # Direct match first
    phrase = None
    for key, value in INDUSTRY_SEARCH_MAP.items():
        if _norm(key) == industry_norm:
            phrase = value
            break

    # Fuzzy fallback — partial match
    if not phrase:
        for key, value in INDUSTRY_SEARCH_MAP.items():
            key_norm = _norm(key)
            if key_norm in industry_norm or industry_norm in key_norm:
                phrase = value
                break

    # Generic fallback
    if not phrase:
        phrase = f"Best {industry} near me"

    # Inject city if provided
    phrase = _inject_city(phrase, city)
    return phrase


def get_search_intent_line(industry: str, city: str = "", tone: str = "friendly") -> str:
    """
    Returns a short search-intent sentence for outreach messages.

    This function is safe for both scorer.py and whatsapp_messages.py.
    """
    phrase = get_search_phrase(industry, city)
    city_text = str(city or "").strip() or "your area"
    tone = _norm(tone)

    if tone == "formal":
        return (
            f"People in {city_text} often search \"{phrase}\" before choosing a provider."
        )
    if tone == "direct":
        return (
            f"People in {city_text} search \"{phrase}\" when they need this service."
        )
    return (
        f"I noticed people in {city_text} often search \"{phrase}\" when they need this service."
    )


def get_search_intent_line_by_tone(
    industry: str,
    city: str = "",
    tone: str = "friendly"
) -> str:
    """
    Backward-compatible alias used by whatsapp_messages.py.
    """
    return get_search_intent_line(industry, city, tone)
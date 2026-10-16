import re

with open('engine/enricher.py', 'r', encoding='utf-8') as f:
    text = f.read()

if '_enrich_from_b2bhint_and_ch' not in text:
    hook_code = """
# ─────────────────────────────────────────────────────────
# v10.2 B2BHint & Companies House Deep Officer Hook
# ─────────────────────────────────────────────────────────

def _enrich_from_b2bhint_and_ch(lead: dict) -> int:
    \"\"\"
    Step 0.5: If lead has B2BHint detail URL or Company Number,
    perform deep profile extraction (B2BHint detail page + Companies House officers)
    to discover Business Owners, Directors, and verified contacts right away!
    \"\"\"
    fields_added = 0
    try:
        # 1. Check if lead has B2BHint detail URL inside enrichment_notes
        notes = str(lead.get("enrichment_notes") or "")
        b2b_url = ""
        if "b2bhint_detail:" in notes:
            for part in notes.split("|"):
                if "b2bhint_detail:" in part:
                    b2b_url = part.split("b2bhint_detail:")[1].strip()
                    break
        elif lead.get("source_url") and "b2bhint.com" in str(lead.get("source_url")):
            b2b_url = str(lead.get("source_url")).strip()

        if b2b_url and not lead.get("decision_maker_name"):
            try:
                from engine.b2bhint_scraper import scrape_b2bhint_company_page
                deep_info = scrape_b2bhint_company_page(b2b_url)
                if deep_info:
                    if deep_info.get("decision_maker_name") and not lead.get("decision_maker_name"):
                        lead["decision_maker_name"] = deep_info["decision_maker_name"]
                        lead["decision_maker_title"] = deep_info.get("decision_maker_title", "Director / Owner")
                        lead["dm_source"] = "b2bhint_deep"
                        fields_added += 1
                    if deep_info.get("company_registration_number") and not lead.get("company_registration_number"):
                        lead["company_registration_number"] = deep_info["company_registration_number"]
                        fields_added += 1
                    if deep_info.get("email") and not lead.get("email"):
                        lead["email"] = deep_info["email"]
                        fields_added += 1
                    if deep_info.get("phone") and not lead.get("phone"):
                        lead["phone"] = deep_info["phone"]
                        fields_added += 1
                    if deep_info.get("website") and not lead.get("website_url"):
                        lead["website_url"] = deep_info["website"]
                        fields_added += 1
            except Exception as exc:
                logger.debug("B2BHint deep enrich error: %s", exc)

        # 2. Check if lead has exact Company Registration Number (UK/GB)
        ch_num = str(lead.get("company_registration_number") or lead.get("company_number") or "").strip()
        country = str(lead.get("country") or "").strip().lower()
        if ch_num and country in {"uk", "united kingdom", "england", "scotland", "wales", "gb", "great britain"}:
            if not lead.get("decision_maker_name") or lead.get("decision_maker_name") in _GENERIC_MANAGER_VALUES:
                try:
                    from engine.companies_house_scraper import get_company_officers, _title_name
                    officers = get_company_officers(ch_num)
                    if officers:
                        for off in officers:
                            if off.get("resigned_on") or "resigned" in str(off.get("status", "")).lower():
                                continue
                            role = str(off.get("role") or "").lower()
                            if any(r in role for r in ["director", "managing-director", "owner", "proprietor", "partner", "secretary"]):
                                name = str(off.get("name") or "").strip()
                                if name:
                                    lead["decision_maker_name"] = _title_name(name)
                                    lead["decision_maker_title"] = off.get("role", "Director").title()
                                    lead["dm_source"] = "companies_house"
                                    fields_added += 1
                                    break
                except Exception as exc:
                    logger.debug("CH officer lookup error: %s", exc)

        return fields_added
    except Exception as exc:
        logger.warning("B2BHint/CH enrichment exception: %s", exc)
        return 0
"""
    idx = text.find("def enrich_lead(lead: dict,")
    text = text[:idx] + hook_code + "\n" + text[idx:]

    old_call = """    # ── Step 0: CQC ───────────────────────────────────
    cqc_fields = _enrich_from_cqc(working)
    if cqc_fields > 0:
        fields_improved += cqc_fields
        enrichment_notes.append("CQC data enriched")"""
    
    new_call = """    # ── Step 0: CQC ───────────────────────────────────
    cqc_fields = _enrich_from_cqc(working)
    if cqc_fields > 0:
        fields_improved += cqc_fields
        enrichment_notes.append("CQC data enriched")

    # ── Step 0.5: B2BHint & Companies House Deep Officers ──
    b2b_ch_fields = _enrich_from_b2bhint_and_ch(working)
    if b2b_ch_fields > 0:
        fields_improved += b2b_ch_fields
        enrichment_notes.append("B2BHint/CH officers enriched")"""

    text = text.replace(old_call, new_call)
    with open("engine/enricher.py", "w", encoding="utf-8") as out:
        out.write(text)
    print("Successfully patched and wired engine/enricher.py v10.2!")

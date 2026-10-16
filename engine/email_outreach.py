# engine/email_outreach.py
# v1.2 — ColdEmailSender + EmailTemplateEngine
# Gmail SMTP via app password
# Reads GMAIL_ADDRESS, GMAIL_APP_PASSWORD from .env
#
# v1.1 changes:
#   - Added EmailTemplateEngine.generate_email()   <- dashboard alias
#   - Added ColdEmailSender.send_email()           <- dashboard alias
#   - Added ColdEmailSender.gmail_address property <- dashboard check
#
# v1.2 changes:
#   - send_email() now has Nigeria + UK compliance guard
#     Nigeria leads are BLOCKED from email (WhatsApp/call first)
#     UK leads blocked unless enrichment_status = complete or enriched

from __future__ import annotations

import os
import re
import smtplib
import logging
import textwrap
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()
logger = logging.getLogger(__name__)

_GMAIL_ADDRESS: str      = os.getenv("GMAIL_ADDRESS", "")
_GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")

_SMTP_HOST    = "smtp.gmail.com"
_SMTP_PORT    = 587
_SEND_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_email(address: str) -> bool:
    pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    return bool(re.match(pattern, address.strip()))


def _clean(value: Optional[str], fallback: str = "") -> str:
    return (value or "").strip() or fallback


# ---------------------------------------------------------------------------
# EmailTemplateEngine
# ---------------------------------------------------------------------------

class EmailTemplateEngine:
    """
    Renders plain-text and HTML email bodies from a lead dict.
    """

    _TEMPLATES: dict[str, dict[str, str]] = {

        "no_website": {
            "subject": "Quick question about {company_name}",
            "body": textwrap.dedent("""\
                Hi {first_name},

                I came across {company_name} while researching
                {industry} businesses in {city}.

                I noticed {company_name} doesn't appear to have
                a website yet - this means customers searching
                Google for your services may not be finding you.

                Most people search online before calling or visiting
                a business. Without a website, those customers may
                be going to your competitors instead.

                I build professional websites for local {industry}
                businesses - often within days, not months - at
                a price that makes sense.

                Would you be open to a quick 10-minute chat this week?

                Best regards,
                {sender_name}
                {sender_title}
                {sender_company}
            """),
        },

        "poor_website": {
            "subject": "I noticed something on {company_name}'s website",
            "body": textwrap.dedent("""\
                Hi {first_name},

                I took a quick look at {company_name}'s website and
                spotted several issues that may be costing you
                customers.

                Three things stood out:
                  1. The site may not be loading fast enough on mobile
                  2. It's hard for customers to find your contact details
                  3. There are SEO gaps your competitors are exploiting

                I help {industry} businesses fix exactly these problems
                - happy to share a free report if useful.

                Worth a quick call?

                Best regards,
                {sender_name}
                {sender_title}
                {sender_company}
            """),
        },

        "cold_intro": {
            "subject": "Quick question about {company_name}",
            "body": textwrap.dedent("""\
                Hi {first_name},

                I came across {company_name} and was impressed by what
                you're doing in the {industry} space.

                We help {industry} businesses streamline their operations
                and grow revenue - typically delivering results within the
                first 90 days.

                Would you be open to a 15-minute call this week to explore
                whether there's a fit?

                Best,
                {sender_name}
                {sender_title}
                {sender_company}
            """),
        },

        "website_audit": {
            "subject": "I noticed something on {company_name}'s website",
            "body": textwrap.dedent("""\
                Hi {first_name},

                I took a quick look at {website} and spotted a few
                opportunities that could meaningfully improve your
                online performance.

                Three things stood out:
                  1. Page-speed improvements that could lift conversion
                  2. SEO gaps your competitors are already exploiting
                  3. Mobile UX issues that may be costing you leads

                Happy to share the full report at no cost - would a brief
                call work for you?

                Best,
                {sender_name}
                {sender_title}
                {sender_company}
            """),
        },

        "follow_up_1": {
            "subject": "Re: Quick question about {company_name}",
            "body": textwrap.dedent("""\
                Hi {first_name},

                I wanted to follow up on my previous note in case it
                slipped through.

                We've been working with several {industry} companies in
                {city} recently - happy to share what's been working.

                Worth a quick chat?

                Best,
                {sender_name}
                {sender_title}
                {sender_company}
            """),
        },

        "follow_up_2": {
            "subject": "Closing the loop - {company_name}",
            "body": textwrap.dedent("""\
                Hi {first_name},

                I'll keep this short - I don't want to clog your inbox.

                If the timing isn't right, no worries at all. I'll check
                back in a few months.

                If you'd like to explore how we can help {company_name}
                in the meantime, just reply to this email.

                Wishing you continued success.

                Best,
                {sender_name}
                {sender_title}
                {sender_company}
            """),
        },

        "uk_compliance": {
            "subject": "Helping {industry} businesses in the UK",
            "body": textwrap.dedent("""\
                Hi {first_name},

                I hope this finds you well.

                We work with {industry} companies across the UK,
                helping them {pain_point}.

                If this is relevant, I'd love to connect - even
                just to share some ideas.

                Kind regards,
                {sender_name}
                {sender_title}
                {sender_company}

                --------------------------------------------------
                You are receiving this email because your business
                information is publicly available and we believe our
                services may be of genuine interest.
                To unsubscribe reply with "unsubscribe" in the subject.
                --------------------------------------------------
            """),
        },
    }

    _DEFAULT_SENDER_META = {
        "sender_name":    "The Team",
        "sender_title":   "",
        "sender_company": "",
    }

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def list_templates(cls) -> list[str]:
        return list(cls._TEMPLATES.keys())

    @classmethod
    def get_template_info(cls, template_key: str) -> dict:
        if template_key not in cls._TEMPLATES:
            raise KeyError(f"Unknown template: {template_key!r}")
        return dict(cls._TEMPLATES[template_key])

    def render(
        self,
        template_key: str,
        lead:         dict,
        sender_meta:  Optional[dict] = None,
    ) -> dict[str, str]:
        """
        Render subject + plain-text + HTML body for lead using template_key.
        Returns dict with keys: subject, body_plain, body_html
        """
        if template_key not in self._TEMPLATES:
            raise KeyError(f"Unknown template: {template_key!r}")

        tmpl       = self._TEMPLATES[template_key]
        ctx        = self._build_context(lead, sender_meta or {})
        subject    = tmpl["subject"].format_map(ctx)
        body_plain = tmpl["body"].format_map(ctx)
        body_html  = self._plain_to_html(body_plain, ctx)

        return {
            "subject":    subject,
            "body_plain": body_plain,
            "body_html":  body_html,
        }

    def generate_email(
        self,
        lead:        dict,
        sequence:    int           = 1,
        sender_meta: Optional[dict] = None,
    ) -> dict[str, str]:
        """
        Dashboard-facing alias for render().

        Auto-selects template based on:
          - website_score on the lead (set by website auditor)
          - sequence number (1=first touch, 2=follow-up, 3=breakup)

        Returns dict with keys: subject, text, html, template_type
        """
        # Pick template based on sequence
        if sequence == 2:
            template_key = "follow_up_1"
        elif sequence >= 3:
            template_key = "follow_up_2"
        else:
            score   = lead.get("website_score", 0)
            website = (lead.get("website") or "").strip()

            if not website or score == 0:
                template_key = "no_website"
            elif score <= 4:
                template_key = "poor_website"
            else:
                template_key = "cold_intro"

        rendered = self.render(template_key, lead, sender_meta)

        return {
            "subject":       rendered["subject"],
            "text":          rendered["body_plain"],
            "html":          rendered["body_html"],
            "template_type": template_key,
        }

    def preview(
        self,
        template_key: str,
        lead:         dict,
        sender_meta:  Optional[dict] = None,
    ) -> str:
        rendered = self.render(template_key, lead, sender_meta)
        return (
            f"SUBJECT: {rendered['subject']}\n"
            f"{'-' * 60}\n"
            f"{rendered['body_plain']}"
        )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _build_context(self, lead: dict, sender_override: dict) -> dict:
        city    = _clean(lead.get("city"),    "your area")
        country = _clean(lead.get("country"), "")

        industry = _clean(lead.get("industry"), "your industry")
        industry = industry.title()

        pain_point = _clean(
            lead.get("pain_point"),
            "reduce costs and increase revenue",
        )

        company_name = _clean(
            lead.get("business_name") or lead.get("company_name"),
            "your company",
        )

        first_name = _clean(
            lead.get("decision_maker_first") or lead.get("first_name"),
            "there",
        )
        last_name = _clean(lead.get("last_name"), "")

        meta = {**self._DEFAULT_SENDER_META, **sender_override}

        sender_name    = _clean(meta.get("sender_name",    ""), "") or \
                         _clean(os.getenv("FROM_NAME",     ""), "KNCS Digital")
        sender_title   = _clean(meta.get("sender_title",   ""), "Digital Growth Specialist")
        sender_company = _clean(meta.get("sender_company", ""), "KNCS Digital")

        return {
            "company_name":   company_name,
            "first_name":     first_name,
            "last_name":      last_name,
            "full_name":      f"{first_name} {last_name}".strip() or "there",
            "website":        _clean(lead.get("website"), "your website"),
            "industry":       industry,
            "city":           city,
            "country":        country,
            "pain_point":     pain_point,
            "sender_name":    sender_name,
            "sender_title":   sender_title,
            "sender_company": sender_company,
            "current_year":   str(datetime.now(timezone.utc).year),
            "current_date":   datetime.now(timezone.utc).strftime("%d %B %Y"),
        }

    @staticmethod
    def _plain_to_html(plain: str, ctx: dict) -> str:
        paragraphs = plain.strip().split("\n\n")
        html_parts: list[str] = []

        for para in paragraphs:
            lines = para.strip().splitlines()
            if all(re.match(r"^\s*\d+\.", ln) for ln in lines if ln.strip()):
                items = "".join(
                    f"<li>{ln.strip().lstrip('0123456789. ')}</li>"
                    for ln in lines if ln.strip()
                )
                html_parts.append(f"<ol>{items}</ol>")
            else:
                escaped = "<br>".join(
                    ln.replace("&", "&amp;").replace("<", "&lt;")
                    for ln in lines
                )
                html_parts.append(f"<p>{escaped}</p>")

        body_inner     = "\n".join(html_parts)
        sender_company = ctx.get("sender_company", "")
        footer_line    = (
            f"<p style='color:#888;font-size:12px;'>Sent by {sender_company}</p>"
            if sender_company else ""
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: Arial, sans-serif; font-size: 14px;
            line-height: 1.6; color: #333; max-width: 600px; }}
    p    {{ margin: 0 0 12px 0; }}
    ol   {{ margin: 0 0 12px 0; padding-left: 20px; }}
    li   {{ margin-bottom: 6px; }}
    hr   {{ border: none; border-top: 1px solid #eee; margin: 20px 0; }}
  </style>
</head>
<body>
{body_inner}
{footer_line}
</body>
</html>"""


# ---------------------------------------------------------------------------
# ColdEmailSender
# ---------------------------------------------------------------------------

class ColdEmailSender:
    """
    Sends cold outreach emails via Gmail SMTP (STARTTLS, port 587).
    """

    def __init__(
        self,
        gmail_address:      Optional[str]  = None,
        gmail_app_password: Optional[str]  = None,
        sender_meta:        Optional[dict] = None,
        dry_run:            bool           = False,
    ) -> None:
        self._address         = _clean(gmail_address      or _GMAIL_ADDRESS)
        self._password        = _clean(gmail_app_password or _GMAIL_APP_PASSWORD)
        self._sender_meta     = sender_meta or {}
        self._dry_run         = dry_run
        self._template_engine = EmailTemplateEngine()

    # ------------------------------------------------------------------ #
    # Properties                                                           #
    # ------------------------------------------------------------------ #

    @property
    def is_configured(self) -> bool:
        return bool(self._address and self._password)

    @property
    def sender_address(self) -> str:
        return self._address

    @property
    def gmail_address(self) -> str:
        """
        Dashboard checks: if not getattr(sender, 'gmail_address', None)
        Returns empty string if not configured so the check fails correctly.
        """
        return self._address

    # ------------------------------------------------------------------ #
    # Dashboard-facing send_email() — v1.2 adds compliance guard          #
    # ------------------------------------------------------------------ #

    def send_email(
        self,
        to_email:  str,
        subject:   str,
        html_body: str,
        text_body: str,
        lead:      Optional[dict]      = None,
        cc:        Optional[list[str]] = None,
        reply_to:  Optional[str]       = None,
    ) -> dict:
        """
        Dashboard alias for send_raw() — WITH compliance guard.

        The dashboard calls:
            result = sender.send_email(
                to_email  = email_addr,
                subject   = subject,
                html_body = html,
                text_body = text,
            )
            result.get("sent")   <- bool
            result.get("error")  <- str

        COMPLIANCE RULES (from handover note):
            Nigeria  -> BLOCKED always (WhatsApp/call first, never email)
            UK       -> BLOCKED unless enrichment_status = complete or enriched
            All else -> allowed
        """
        # ── Compliance guard ──────────────────────────────────────────
        if lead:
            country = _clean(lead.get("country", "")).lower()

            # Nigeria — never email first
            if country == "nigeria":
                logger.info(
                    "send_email BLOCKED (Nigeria rule): %s", to_email
                )
                return {
                    "sent":  False,
                    "error": (
                        "Blocked — Nigeria leads require WhatsApp first, "
                        "then call. Email is not permitted as first contact."
                    ),
                }

            # UK — only email if fully enriched
            if country in ("united kingdom", "uk", "gb"):
                status = _clean(lead.get("enrichment_status", "")).lower()
                if status not in ("complete", "enriched"):
                    logger.info(
                        "send_email BLOCKED (UK enrichment rule): %s status=%s",
                        to_email, status,
                    )
                    return {
                        "sent":  False,
                        "error": (
                            f"UK email blocked — enrichment_status must be "
                            f"'complete' or 'enriched', got: {status!r}"
                        ),
                    }

        # ── Send ──────────────────────────────────────────────────────
        ok, msg = self.send_raw(
            to_address = to_email,
            subject    = subject,
            body_plain = text_body,
            body_html  = html_body,
            cc         = cc,
            reply_to   = reply_to,
        )
        return {
            "sent":  ok,
            "error": "" if ok else msg,
        }

    # ------------------------------------------------------------------ #
    # Core send                                                            #
    # ------------------------------------------------------------------ #

    def send(
        self,
        to_address:   str,
        template_key: str,
        lead:         dict,
        cc:           Optional[list[str]] = None,
        reply_to:     Optional[str]       = None,
        sender_meta:  Optional[dict]      = None,
    ) -> tuple[bool, str]:
        """
        Render template_key for lead and send to to_address.
        Returns (True, "Sent OK") or (False, reason).
        """
        if not _validate_email(to_address):
            return False, f"Invalid recipient address: {to_address!r}"

        if not self.is_configured:
            return False, (
                "Gmail credentials not configured. "
                "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env"
            )

        # Compliance guard
        country = _clean(lead.get("country", "")).lower()
        if country == "nigeria":
            return False, (
                "Email blocked — Nigeria leads require WhatsApp then call. "
                "Never email first."
            )

        if country in ("united kingdom", "uk", "gb"):
            status = _clean(lead.get("enrichment_status", "")).lower()
            if status not in ("complete", "enriched"):
                return False, (
                    "UK email blocked — enrichment_status must be "
                    f"'complete' or 'enriched', got: {status!r}"
                )

        try:
            meta     = {**self._sender_meta, **(sender_meta or {})}
            rendered = self._template_engine.render(template_key, lead, meta)
        except KeyError as exc:
            return False, f"Template error: {exc}"
        except Exception as exc:
            logger.exception("Template render failed")
            return False, f"Template render failed: {exc}"

        if self._dry_run:
            logger.info(
                "[DRY RUN] Would send to %s\n%s",
                to_address,
                self._template_engine.preview(template_key, lead, meta),
            )
            return True, "Dry run — email not actually sent"

        msg = self._build_mime(
            to_address = to_address,
            rendered   = rendered,
            cc         = cc or [],
            reply_to   = reply_to,
        )
        return self._smtp_send(to_address, msg, cc or [])

    def send_raw(
        self,
        to_address: str,
        subject:    str,
        body_plain: str,
        body_html:  Optional[str]       = None,
        cc:         Optional[list[str]] = None,
        reply_to:   Optional[str]       = None,
    ) -> tuple[bool, str]:
        """
        Send a fully custom email without using a template.
        Returns (bool, str) same contract as send().
        """
        if not _validate_email(to_address):
            return False, f"Invalid recipient address: {to_address!r}"

        if not self.is_configured:
            return False, (
                "Gmail credentials not configured. "
                "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env"
            )

        rendered = {
            "subject":    subject,
            "body_plain": body_plain,
            "body_html":  body_html or EmailTemplateEngine._plain_to_html(
                body_plain, {}
            ),
        }

        if self._dry_run:
            logger.info(
                "[DRY RUN] Raw email to %s | Subject: %s",
                to_address, subject,
            )
            return True, "Dry run — email not actually sent"

        msg = self._build_mime(to_address, rendered, cc or [], reply_to)
        return self._smtp_send(to_address, msg, cc or [])

    # ------------------------------------------------------------------ #
    # Batch helper                                                         #
    # ------------------------------------------------------------------ #

    def send_batch(
        self,
        leads:         list[dict],
        template_key:  str,
        email_field:   str            = "email",
        sender_meta:   Optional[dict] = None,
        stop_on_error: bool           = False,
    ) -> list[dict]:
        """
        Send template_key to each lead in leads.
        Returns list of result dicts: { lead, to_address, success, message }
        """
        results: list[dict] = []

        for lead in leads:
            to_address = _clean(lead.get(email_field, ""))
            if not to_address:
                results.append({
                    "lead":       lead,
                    "to_address": to_address,
                    "success":    False,
                    "message":    f"No value for field {email_field!r}",
                })
                continue

            ok, msg = self.send(
                to_address   = to_address,
                template_key = template_key,
                lead         = lead,
                sender_meta  = sender_meta,
            )

            results.append({
                "lead":       lead,
                "to_address": to_address,
                "success":    ok,
                "message":    msg,
            })

            if not ok and stop_on_error:
                logger.warning(
                    "Batch aborted after failure: %s -> %s", to_address, msg
                )
                break

        sent = sum(1 for r in results if r["success"])
        logger.info(
            "Batch complete — %d/%d sent (template=%s)",
            sent, len(results), template_key,
        )
        return results

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _build_mime(
        self,
        to_address: str,
        rendered:   dict,
        cc:         list[str],
        reply_to:   Optional[str],
    ) -> MIMEMultipart:
        msg            = MIMEMultipart("alternative")
        msg["From"]    = self._address
        msg["To"]      = to_address
        msg["Subject"] = rendered["subject"]
        msg["Date"]    = datetime.now(timezone.utc).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        )

        if cc:
            msg["Cc"] = ", ".join(cc)
        if reply_to:
            msg["Reply-To"] = reply_to

        msg.attach(MIMEText(rendered["body_plain"], "plain", "utf-8"))
        msg.attach(MIMEText(rendered["body_html"],  "html",  "utf-8"))

        return msg

    def _smtp_send(
        self,
        to_address: str,
        msg:        MIMEMultipart,
        cc:         list[str],
    ) -> tuple[bool, str]:
        """
        Open SMTP connection and send. Always closes cleanly.
        Returns (bool, str) safe to unpack directly.
        """
        all_recipients = [to_address] + cc

        try:
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=_SEND_TIMEOUT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._address, self._password)
                server.sendmail(
                    from_addr = self._address,
                    to_addrs  = all_recipients,
                    msg       = msg.as_string(),
                )
            logger.info(
                "Email sent -> %s | subject: %s", to_address, msg["Subject"]
            )
            return True, "Sent OK"

        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP auth failed for %s", self._address)
            return False, (
                "Authentication failed — check GMAIL_ADDRESS and "
                "GMAIL_APP_PASSWORD. Make sure 2FA is on and you are "
                "using an App Password, not your account password."
            )

        except smtplib.SMTPRecipientsRefused:
            logger.warning("Recipient refused: %s", to_address)
            return False, f"Recipient refused by server: {to_address}"

        except smtplib.SMTPSenderRefused:
            logger.error("Sender refused: %s", self._address)
            return False, f"Sender address refused: {self._address}"

        except smtplib.SMTPDataError as exc:
            logger.error("SMTP data error: %s", exc)
            return False, f"SMTP data error: {exc.smtp_error!r}"

        except smtplib.SMTPException as exc:
            logger.error("SMTP error: %s", exc)
            return False, f"SMTP error: {exc}"

        except TimeoutError:
            return False, f"Connection to {_SMTP_HOST}:{_SMTP_PORT} timed out"

        except OSError as exc:
            logger.error("Network error: %s", exc)
            return False, f"Network error: {exc}"


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """True when Gmail credentials are present in the environment."""
    return bool(_GMAIL_ADDRESS and _GMAIL_APP_PASSWORD)


def get_sender(dry_run: bool = False) -> ColdEmailSender:
    """Return a configured ColdEmailSender (or dry-run instance)."""
    return ColdEmailSender(dry_run=dry_run)


def list_templates() -> list[str]:
    """Convenience wrapper — return all template keys."""
    return EmailTemplateEngine.list_templates()


def render_preview(template_key: str, lead: dict) -> str:
    """Quick preview string for dashboard display."""
    return EmailTemplateEngine().preview(template_key, lead)
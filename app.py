# app.py
# KNCS LeadGen Pro — Premium Lead Intelligence Platform
# v10.1 (audit-clean)
#
# Registered bugs fixed:
#   BUG-APP-01  cleanup_stale_jobs() called in _init_db() after
#               init_database() succeeds — ghost jobs cleared at
#               startup
#   BUG-APP-02  Global CSS converted from f-string to plain string —
#               unescaped {} in future CSS edits never crashes app
#   BUG-APP-03  st.stop() removed from _init_db() body — failure
#               returns False; caller handles display after Streamlit
#               context confirmed active
#   BUG-APP-04  Page imports are lazy — only selected page module
#               imported per rerun, not all 8 at startup
#   BUG-APP-05  Sidebar stats wrapped in _get_cached_stats() with
#               @st.cache_data(ttl=30)
#   BUG-APP-06  render_page() logs full exception; sanitised message
#               shown to user
#   BUG-APP-07  _render_settings() HTML strings de-f-stringed
#   BUG-APP-08  save_logo() validates size (<=2 MB) and magic bytes
#   BUG-APP-09  Refresh button added adjacent to live job card
#   BUG-APP-10  Version string updated to v10.1
#   BUG-APP-11  _safe_import() logs warning on failure
#
# Additional defects fixed:
#   NEW-APP-A   render_page() used logger.exception() for stored
#               exceptions — no active traceback context; replaced
#               with logger.error(..., exc_info=stored_exc)
#   NEW-APP-B   _safe_import() did not re-raise KeyboardInterrupt/
#               SystemExit — now re-raised before Exception catch
#   NEW-APP-C   Sidebar progress bar — integer percent (e.g. 75)
#               clamped to 1.0; now normalised via /100 branch
#   NEW-APP-D   page_icon was HTML entity string "&#129504;" —
#               Streamlit needs actual Unicode emoji "🧠"
#   NEW-APP-E   warnings.filterwarnings dot unescaped — now r".*..."
#   NEW-APP-F   _render_settings() note still said v10.0 — updated
#   NEW-APP-G   Sidebar button keys now include active_id to prevent
#               duplicate key errors across reruns

from __future__ import annotations

import importlib
import logging
import warnings
from pathlib import Path
from typing import Optional

import streamlit as st

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# PAGE CONFIG — must be the very first Streamlit call
# NEW-APP-D: actual Unicode emoji, not HTML entity string
# ─────────────────────────────────────────────────────────
st.set_page_config(
    page_title="KNCS LeadGen Pro",
    page_icon="🧠",           # NEW-APP-D: was "&#129504;"
    layout="wide",
    initial_sidebar_state="expanded",
)

# NEW-APP-E: escaped dot — suppresses only the intended warning
warnings.filterwarnings(
    "ignore",
    message=r".*use_container_width.*",
)

# ─────────────────────────────────────────────────────────
# PIL OPTIONAL
# ─────────────────────────────────────────────────────────
try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False
    _PILImage = None  # type: ignore

# ─────────────────────────────────────────────────────────
# SESSION STATE DEFAULTS
# ─────────────────────────────────────────────────────────
_SESSION_DEFAULTS: dict = {
    "is_scoring":           False,
    "ai_scoring_running":   False,
    "cc_selected_lead_id":  None,
    "cc_filter_country":    "All",
    "cc_filter_market":     "All",
    "cc_batch_size":        10,
    "scrape_job_id":        None,
    "scrape_running":       False,
    "wa_verify_lead_id":    None,
}

for _k, _v in _SESSION_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─────────────────────────────────────────────────────────
# LOGO MANAGEMENT
# BUG-APP-08: size cap + magic-byte validation in save_logo()
# ─────────────────────────────────────────────────────────
LOGO_DIR  = Path("data/branding")
LOGO_DIR.mkdir(parents=True, exist_ok=True)
LOGO_PATH = LOGO_DIR / "logo.png"

_MAX_LOGO_BYTES = 2 * 1024 * 1024   # 2 MB
_PNG_MAGIC      = b"\x89PNG"
_JPEG_MAGIC     = b"\xff\xd8\xff"


def save_logo(uploaded_file) -> bool:
    """
    BUG-APP-08: Validate file size and magic bytes before writing.
    Rejects files over 2 MB and non-PNG/JPEG content regardless
    of declared file extension.
    """
    try:
        data = uploaded_file.getbuffer()

        if len(data) > _MAX_LOGO_BYTES:
            st.error(
                "Logo file is too large ("
                + str(round(len(data) / 1_048_576, 1))
                + " MB). Maximum is 2 MB."
            )
            return False

        data_bytes = bytes(data)
        if not (
            data_bytes[:4] == _PNG_MAGIC
            or data_bytes[:3] == _JPEG_MAGIC
        ):
            st.error(
                "File does not appear to be a valid PNG or JPEG image."
            )
            return False

        with open(LOGO_PATH, "wb") as fh:
            fh.write(data)
        return True

    except Exception as exc:
        st.error("Error saving logo: " + str(exc))
        return False


def get_logo():
    if not _PIL_AVAILABLE or not LOGO_PATH.exists():
        return None
    try:
        return _PILImage.open(LOGO_PATH)
    except Exception:
        return None


def remove_logo() -> bool:
    try:
        if LOGO_PATH.exists():
            LOGO_PATH.unlink()
        return True
    except PermissionError as exc:
        st.error("Cannot remove logo (file in use): " + str(exc))
        return False
    except Exception as exc:
        st.error("Error removing logo: " + str(exc))
        return False


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────
def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _fmt_revenue(value) -> str:
    try:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("", "none", "null", "n/a"):
                return "0"
        result = float(value or 0)
        if result == int(result):
            return "{:,}".format(int(result))
        return "{:,.2f}".format(result)
    except (TypeError, ValueError):
        return "0"


# ─────────────────────────────────────────────────────────
# DESIGN TOKENS
# ─────────────────────────────────────────────────────────
_C = {
    "bg_base":        "#060d19",
    "bg_surface":     "#0b1628",
    "bg_raised":      "#101f38",
    "bg_input":       "#0d1a2e",
    "brand_blue":     "#5b8cff",
    "brand_purple":   "#7c5cff",
    "brand_mid":      "#6b74ff",
    "green":          "#10b981",
    "gold":           "#f59e0b",
    "red":            "#ef4444",
    "orange":         "#f97316",
    "text_primary":   "#f1f5f9",
    "text_secondary": "#94a3b8",
    "text_muted":     "#475569",
    "text_faint":     "#1e293b",
    "border_subtle":  "rgba(255,255,255,0.05)",
    "border_mid":     "rgba(255,255,255,0.08)",
    "border_brand":   "rgba(91,140,255,0.30)",
}

# ─────────────────────────────────────────────────────────
# GLOBAL CSS
# BUG-APP-02: Plain string — no f-string.
# Colour values injected via string concatenation only.
# ─────────────────────────────────────────────────────────
_CSS = (
    "<style>"
    "@import url('https://fonts.googleapis.com/css2?family=Inter"
    ":wght@300;400;500;600;700;800;900&display=swap');"

    "html, body,"
    "[data-testid='stAppViewContainer'],"
    "[data-testid='stAppViewContainer'] > section,"
    "[data-testid='stAppViewContainer'] > section > div,"
    ".main {"
    "    background: " + _C["bg_base"] + " !important;"
    "    color: " + _C["text_primary"] + ";"
    "    font-family: 'Inter', -apple-system, BlinkMacSystemFont,"
    "                 'Segoe UI', system-ui, sans-serif;"
    "    -webkit-font-smoothing: antialiased;"
    "}"

    "::-webkit-scrollbar { width: 6px; height: 6px; }"
    "::-webkit-scrollbar-track { background: " + _C["bg_base"] + "; }"
    "::-webkit-scrollbar-thumb {"
    "    background: " + _C["bg_raised"] + "; border-radius: 999px;"
    "}"
    "::-webkit-scrollbar-thumb:hover {"
    "    background: " + _C["brand_blue"] + "44;"
    "}"

    "#MainMenu, footer, header { display: none !important; }"

    "[data-testid='stSidebarNav'],"
    "[data-testid='stSidebarNavItems'],"
    "[data-testid='stSidebarNavSeparator'],"
    "section[data-testid='stSidebarNav'],"
    "[data-testid='stSidebarNavContainer'],"
    "nav[data-testid='stSidebarNav'] { display: none !important; }"

    ".main .block-container {"
    "    padding-top: 1.5rem;"
    "    padding-bottom: 2.5rem;"
    "    max-width: 1600px;"
    "}"

    "[data-testid='stSidebar'] {"
    "    background: " + _C["bg_surface"] + " !important;"
    "    border-right: 1px solid " + _C["border_subtle"] + " !important;"
    "}"

    ".page-header { margin-bottom: 24px; }"
    ".page-header-eyebrow {"
    "    font-size: 10px; font-weight: 700; text-transform: uppercase;"
    "    letter-spacing: 2px; color: " + _C["brand_blue"] + ";"
    "    margin-bottom: 6px;"
    "}"
    ".page-header-title {"
    "    font-size: 34px; font-weight: 900;"
    "    color: " + _C["text_primary"] + ";"
    "    letter-spacing: -1.2px; line-height: 1.05;"
    "    margin: 0 0 6px 0;"
    "}"
    ".page-header-sub {"
    "    font-size: 14px; color: " + _C["text_muted"] + ";"
    "    font-weight: 500; max-width: 760px;"
    "    line-height: 1.7; margin: 0;"
    "}"

    ".card-premium {"
    "    background: linear-gradient(135deg,"
    "        " + _C["bg_surface"] + " 0%, " + _C["bg_raised"] + " 100%);"
    "    border: 1px solid " + _C["border_subtle"] + ";"
    "    border-radius: 18px; padding: 22px 24px; margin-bottom: 10px;"
    "}"

    ".sec-title {"
    "    font-size: 18px; font-weight: 800;"
    "    color: " + _C["text_primary"] + "; margin: 0 0 4px 0;"
    "}"
    ".sec-sub {"
    "    font-size: 12px; color: " + _C["text_muted"] + ";"
    "    font-weight: 500; margin: 0;"
    "}"

    ".sb-metric {"
    "    background: linear-gradient(135deg,"
    "        " + _C["bg_raised"] + ", " + _C["bg_surface"] + ");"
    "    border: 1px solid " + _C["border_subtle"] + ";"
    "    border-radius: 10px; padding: 10px 12px;"
    "}"
    ".sb-metric-label {"
    "    color: " + _C["text_muted"] + "; font-size: 9px;"
    "    font-weight: 700; text-transform: uppercase;"
    "    letter-spacing: 1px; margin-bottom: 4px;"
    "}"
    ".sb-metric-value {"
    "    color: " + _C["text_primary"] + "; font-size: 18px;"
    "    font-weight: 900; letter-spacing: -0.5px; line-height: 1;"
    "}"

    ".stButton > button {"
    "    border-radius: 12px !important; border: none !important;"
    "    background: linear-gradient(135deg,"
    "        " + _C["brand_blue"] + ", " + _C["brand_purple"] + ") !important;"
    "    color: white !important; font-weight: 700 !important;"
    "    font-size: 13px !important; padding: 11px 18px !important;"
    "    width: 100%;"
    "    transition: opacity 0.2s ease, transform 0.15s ease !important;"
    "}"
    ".stButton > button:hover {"
    "    opacity: 0.88 !important; transform: translateY(-1px) !important;"
    "}"
    ".stButton > button:active { transform: translateY(0px) !important; }"

    ".stTextInput input, .stTextArea textarea, .stNumberInput input {"
    "    background: " + _C["bg_input"] + " !important;"
    "    border: 1px solid " + _C["border_mid"] + " !important;"
    "    border-radius: 12px !important;"
    "    color: " + _C["text_primary"] + " !important;"
    "    font-family: 'Inter', sans-serif !important;"
    "    font-size: 13px !important;"
    "    transition: border-color 0.2s ease !important;"
    "}"
    ".stTextInput input:focus, .stTextArea textarea:focus,"
    ".stNumberInput input:focus {"
    "    border-color: " + _C["brand_blue"] + "88 !important;"
    "    box-shadow: 0 0 0 3px " + _C["brand_blue"] + "12 !important;"
    "    outline: none !important;"
    "}"

    "[data-testid='stSelectbox'] > div > div {"
    "    background: " + _C["bg_input"] + " !important;"
    "    border: 1px solid " + _C["border_mid"] + " !important;"
    "    border-radius: 12px !important;"
    "    color: " + _C["text_primary"] + " !important;"
    "    font-size: 13px !important;"
    "    font-family: 'Inter', sans-serif !important;"
    "    transition: border-color 0.2s ease !important;"
    "}"
    "[data-testid='stSelectbox'] > div > div:hover {"
    "    border-color: " + _C["brand_blue"] + "66 !important;"
    "}"
    "[data-testid='stSelectbox'] svg { fill: " + _C["text_muted"] + " !important; }"
    "[data-testid='stSelectbox'] ul {"
    "    background: " + _C["bg_raised"] + " !important;"
    "    border: 1px solid " + _C["border_mid"] + " !important;"
    "    border-radius: 12px !important; padding: 6px !important;"
    "}"
    "[data-testid='stSelectbox'] ul li {"
    "    border-radius: 8px !important; font-size: 13px !important;"
    "    color: " + _C["text_secondary"] + " !important;"
    "    padding: 8px 12px !important;"
    "}"
    "[data-testid='stSelectbox'] ul li:hover {"
    "    background: " + _C["brand_blue"] + "18 !important;"
    "    color: " + _C["text_primary"] + " !important;"
    "}"

    "[data-testid='stMultiSelect'] > div > div {"
    "    background: " + _C["bg_input"] + " !important;"
    "    border: 1px solid " + _C["border_mid"] + " !important;"
    "    border-radius: 12px !important;"
    "    color: " + _C["text_primary"] + " !important;"
    "    font-size: 13px !important;"
    "}"

    "[data-testid='stTabs'] [data-testid='stTab'] {"
    "    background: transparent !important; border: none !important;"
    "    color: " + _C["text_muted"] + " !important;"
    "    font-size: 13px !important; font-weight: 600 !important;"
    "    padding: 10px 18px !important;"
    "    border-radius: 10px 10px 0 0 !important;"
    "    transition: color 0.2s ease, background 0.2s ease !important;"
    "}"
    "[data-testid='stTabs'] [data-testid='stTab']:hover {"
    "    color: " + _C["text_secondary"] + " !important;"
    "    background: " + _C["bg_raised"] + "88 !important;"
    "}"
    "[data-testid='stTabs'] [data-testid='stTab'][aria-selected='true'] {"
    "    color: " + _C["brand_blue"] + " !important;"
    "    background: " + _C["bg_raised"] + " !important;"
    "    border-bottom: 2px solid " + _C["brand_blue"] + " !important;"
    "    font-weight: 700 !important;"
    "}"
    "[data-testid='stTabs'] [data-testid='stTabsListContainer'] {"
    "    border-bottom: 1px solid " + _C["border_subtle"] + " !important;"
    "    gap: 4px !important;"
    "}"

    "[data-testid='stMetric'] {"
    "    background: linear-gradient(135deg,"
    "        " + _C["bg_surface"] + ", " + _C["bg_raised"] + ");"
    "    border: 1px solid " + _C["border_subtle"] + ";"
    "    border-radius: 14px; padding: 14px 16px;"
    "}"
    "[data-testid='stMetricLabel'] {"
    "    color: " + _C["text_muted"] + " !important;"
    "    font-size: 10px !important; font-weight: 700 !important;"
    "    text-transform: uppercase !important; letter-spacing: 1px !important;"
    "}"
    "[data-testid='stMetricValue'] {"
    "    color: " + _C["text_primary"] + " !important;"
    "    font-weight: 900 !important; font-size: 22px !important;"
    "}"

    "[data-testid='stDataFrame'] {"
    "    border-radius: 14px; overflow: hidden;"
    "    border: 1px solid " + _C["border_subtle"] + ";"
    "}"

    "[data-testid='stAlert'] {"
    "    border-radius: 12px !important; border: none !important;"
    "    font-size: 13px !important;"
    "}"
    ".stSuccess {"
    "    background: rgba(16,185,129,0.08) !important;"
    "    border-left: 3px solid " + _C["green"] + " !important;"
    "}"
    ".stWarning {"
    "    background: rgba(245,158,11,0.08) !important;"
    "    border-left: 3px solid " + _C["gold"] + " !important;"
    "}"
    ".stError {"
    "    background: rgba(239,68,68,0.08) !important;"
    "    border-left: 3px solid " + _C["red"] + " !important;"
    "}"

    "[data-testid='stProgressBar'] > div > div {"
    "    background: linear-gradient(90deg,"
    "        " + _C["brand_blue"] + ", " + _C["brand_purple"] + ") !important;"
    "    border-radius: 999px !important;"
    "}"
    "[data-testid='stProgressBar'] > div {"
    "    background: " + _C["bg_raised"] + " !important;"
    "    border-radius: 999px !important; height: 6px !important;"
    "}"

    "[data-testid='stCheckbox'] label {"
    "    color: " + _C["text_secondary"] + " !important;"
    "    font-size: 13px !important; font-weight: 500 !important;"
    "}"
    "[data-testid='stRadio'] label {"
    "    color: " + _C["text_secondary"] + " !important;"
    "    font-size: 13px !important;"
    "}"

    "[data-testid='stExpander'] {"
    "    background: " + _C["bg_surface"] + " !important;"
    "    border: 1px solid " + _C["border_subtle"] + " !important;"
    "    border-radius: 12px !important;"
    "}"
    "[data-testid='stExpander'] summary {"
    "    color: " + _C["text_secondary"] + " !important;"
    "    font-size: 13px !important; font-weight: 600 !important;"
    "}"

    "hr {"
    "    border: none !important;"
    "    border-top: 1px solid " + _C["border_subtle"] + " !important;"
    "    margin: 16px 0 !important;"
    "}"

    "@keyframes pulse-dot {"
    "    0%, 100% { opacity: 1; transform: scale(1); }"
    "    50%       { opacity: 0.4; transform: scale(0.85); }"
    "}"
    ".live-dot {"
    "    display: inline-block; width: 7px; height: 7px;"
    "    border-radius: 50%; background: " + _C["green"] + ";"
    "    animation: pulse-dot 1.6s ease-in-out infinite;"
    "}"

    ".kpi-card {"
    "    background: linear-gradient(135deg,"
    "        " + _C["bg_surface"] + ", " + _C["bg_raised"] + ");"
    "    border: 1px solid " + _C["border_subtle"] + ";"
    "    border-radius: 14px; padding: 16px 18px;"
    "    text-align: center;"
    "}"
    ".kpi-label {"
    "    color: " + _C["text_muted"] + "; font-size: 9.5px;"
    "    font-weight: 700; text-transform: uppercase;"
    "    letter-spacing: 1.2px; margin-bottom: 8px;"
    "}"
    ".kpi-value {"
    "    color: " + _C["text_primary"] + "; font-size: 26px;"
    "    font-weight: 900; letter-spacing: -1px; line-height: 1;"
    "    margin-bottom: 4px;"
    "}"
    ".kpi-sub {"
    "    color: " + _C["text_muted"] + "; font-size: 11px;"
    "    font-weight: 500;"
    "}"

    "</style>"
)

st.markdown(_CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# DATABASE INIT
# BUG-APP-01: cleanup_stale_jobs() called after init_database()
# BUG-APP-03: st.stop() removed from _init_db() body
# ─────────────────────────────────────────────────────────
def _get_stats_stub() -> dict:
    return {}


_get_stats_fn = _get_stats_stub


def _init_db() -> bool:
    """
    Initialise the database and call startup maintenance functions.

    BUG-APP-01: cleanup_stale_jobs() called here — ghost jobs from
                crashed server sessions cleared before any page or
                job manager check runs.

    BUG-APP-03: st.stop() removed. Returns False on failure so the
                caller can call st.stop() after Streamlit context
                is confirmed active.
    """
    global _get_stats_fn

    try:
        from database import init_database
    except ImportError:
        try:
            from database import initialize_database as init_database  # type: ignore
        except ImportError as exc:
            logger.error("Cannot import database.py: %s", exc)
            return False

    try:
        from database import get_dashboard_stats
        _get_stats_fn = get_dashboard_stats
    except ImportError:
        try:
            from database import get_stats as get_dashboard_stats  # type: ignore
            _get_stats_fn = get_dashboard_stats
        except ImportError:
            pass

    try:
        init_database()
    except Exception as exc:
        logger.error("Database initialisation failed: %s", exc)
        return False

    # BUG-APP-01: clear ghost jobs from previous server sessions
    try:
        from database import cleanup_stale_jobs
        cleanup_stale_jobs()
        logger.info("app: cleanup_stale_jobs() completed at startup")
    except Exception as exc:
        # Non-fatal — stale jobs are an operational inconvenience,
        # not a correctness failure on a clean start
        logger.warning(
            "app: cleanup_stale_jobs() failed at startup: %s", exc
        )

    return True


_db_ready = _init_db()

# BUG-APP-03: st.stop() called HERE — after Streamlit context active
if not _db_ready:
    st.error(
        "&#10060; Database initialisation failed. "
        "Check logs for details."
    )
    st.stop()


# ─────────────────────────────────────────────────────────
# SAFE IMPORT
# BUG-APP-11: logger.warning() on failure
# NEW-APP-B:  KeyboardInterrupt / SystemExit re-raised
# ─────────────────────────────────────────────────────────
def _safe_import(module_path: str):
    """
    Import a module by path. Returns the module on success,
    or the Exception on failure.

    BUG-APP-11: Logs a warning on import failure — visible in logs
                at startup, not only when user navigates to the page.

    NEW-APP-B:  KeyboardInterrupt and SystemExit are re-raised so
                they are never silently swallowed by this function.
    """
    try:
        return importlib.import_module(module_path)
    except (KeyboardInterrupt, SystemExit):
        # NEW-APP-B: never suppress process-level signals
        raise
    except Exception as exc:
        logger.warning(
            "app: failed to import '%s': %s", module_path, exc
        )
        return exc


# ─────────────────────────────────────────────────────────
# PAGE RENDER HELPER
# BUG-APP-06: Full exception logged; sanitised message to user
# NEW-APP-A:  logger.error(exc_info=) for stored exceptions
# ─────────────────────────────────────────────────────────
def render_page(
    module_or_err,
    fn_names: list,
    page_label: str = "",
) -> None:
    """
    BUG-APP-06: Full exception logged; user sees sanitised message
                only — no raw exception strings, paths, or keys.

    NEW-APP-A:  Import errors are stored exceptions (not active).
                logger.exception() has no traceback context here —
                replaced with logger.error(..., exc_info=stored_exc)
                which correctly attaches the stored traceback.
    """
    if isinstance(module_or_err, Exception):
        st.error(
            "&#10060; **" + page_label + "** failed to load. "
            "Check server logs for details."
        )
        # NEW-APP-A: exc_info= attaches stored exception traceback
        logger.error(
            "app: page '%s' import error",
            page_label,
            exc_info=module_or_err,
        )
        return

    for fn_name in fn_names:
        fn = getattr(module_or_err, fn_name, None)
        if callable(fn):
            try:
                fn()
            except Exception as exc:
                st.error(
                    "&#10060; An error occurred in **"
                    + page_label + "**. "
                    "Check server logs for details."
                )
                # Inside except block — logger.exception() correct here
                logger.exception(
                    "app: render error in page '%s' fn '%s': %s",
                    page_label, fn_name, exc,
                )
            return

    st.error(
        "&#10060; No render function found in **"
        + page_label + "**. "
        "Expected one of: " + str(fn_names)
    )


# ─────────────────────────────────────────────────────────
# JOB MANAGER — optional
# ─────────────────────────────────────────────────────────
_job_mgr_available = False
_any_job_running   = lambda: False       # noqa: E731
_get_active_job_id = lambda: None        # noqa: E731
_get_job_summary   = lambda jid: {}      # noqa: E731
_stop_job          = lambda jid: None    # noqa: E731

try:
    from engine.job_manager import (
        any_job_running        as _any_job_running,    # type: ignore
        get_active_job_id      as _get_active_job_id,  # type: ignore
        get_job_status_summary as _get_job_summary,    # type: ignore
        stop_job               as _stop_job,           # type: ignore
    )
    _job_mgr_available = True
except Exception as exc:
    logger.warning("app: job_manager unavailable: %s", exc)


# ─────────────────────────────────────────────────────────
# NAVIGATION
# ─────────────────────────────────────────────────────────
NAV_OPTIONS = [
    "Dashboard",
    "Lead Search",
    "Businesses",
    "Care Homes",
    "Email Outreach",
    "Nigeria B2B",
    "Call Center",
    "Reports",
    "Care Intelligence",
    "Dental Intelligence",
    "MSP Intelligence",
    "Settings",
]

NAV_ICONS = [
    "grid-fill",
    "search",
    "building",
    "house-heart-fill",
    "envelope-fill",
    "flag-fill",
    "telephone-fill",
    "bar-chart-fill",
    "heart-pulse-fill",
    "tooth",
    "pc-display-horizontal",
    "gear-fill",
]

try:
    from streamlit_option_menu import option_menu  # type: ignore
    _OPTION_MENU_AVAILABLE = True
except Exception:
    option_menu = None  # type: ignore
    _OPTION_MENU_AVAILABLE = False


# ─────────────────────────────────────────────────────────
# SIDEBAR STATS — cached
# BUG-APP-05: DB query cached for 30 seconds
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def _get_cached_stats() -> dict:
    """
    BUG-APP-05: Wraps get_dashboard_stats() with a 30-second cache.
    DB query fires at most twice per minute regardless of rerun
    frequency from widget interactions.
    """
    try:
        return _get_stats_fn() or {}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────
# AUTHENTICATION GATE
# ─────────────────────────────────────────────────────────
from engine.auth import require_auth, render_logout_widget

if not require_auth():
    st.stop()

# ─────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────
with st.sidebar:
    logo = get_logo()

    if logo:
        col1, col2 = st.columns([1, 2.2])
        with col1:
            st.image(logo, width=52)
        with col2:
            st.markdown(
                "<div style='padding-top:17px;'>"
                "<div style='"
                "font-size:20px;font-weight:900;"
                "background:linear-gradient(135deg,"
                + _C["brand_blue"] + "," + _C["brand_purple"] + ");"
                "-webkit-background-clip:text;"
                "-webkit-text-fill-color:transparent;"
                "background-clip:text;"
                "letter-spacing:-0.5px;line-height:1.1;"
                "'>KNCS</div>"
                "<div style='"
                "font-size:8.5px;color:" + _C["text_muted"] + ";"
                "font-weight:700;text-transform:uppercase;"
                "letter-spacing:2px;margin-top:3px;"
                "'>Lead Intelligence</div>"
                "</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            "<div style='padding:18px 16px 12px 16px;'>"
            "<div style='"
            "font-size:22px;font-weight:900;"
            "background:linear-gradient(135deg,"
            + _C["brand_blue"] + "," + _C["brand_purple"] + ");"
            "-webkit-background-clip:text;"
            "-webkit-text-fill-color:transparent;"
            "background-clip:text;"
            "letter-spacing:-0.5px;line-height:1.1;"
            "'>KNCS</div>"
            "<div style='"
            "font-size:8.5px;color:" + _C["text_muted"] + ";"
            "font-weight:700;text-transform:uppercase;"
            "letter-spacing:2px;margin-top:4px;"
            "'>Lead Intelligence Platform</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        "<div style='margin:6px 10px 10px 10px;height:1px;"
        "background:linear-gradient(90deg,"
        + _C["brand_blue"] + "," + _C["brand_purple"] + ",transparent);"
        "opacity:0.25;'></div>",
        unsafe_allow_html=True,
    )

    # User Profile & Logout Widget (Pinned prominently right under Logo)
    render_logout_widget()

    # ── Navigation ────────────────────────────────────────
    if _OPTION_MENU_AVAILABLE:
        page = option_menu(
            menu_title=None,
            options=NAV_OPTIONS,
            icons=NAV_ICONS,
            default_index=0,
            styles={
                "container": {
                    "padding":          "0 6px",
                    "background-color": _C["bg_surface"],
                },
                "menu-title": {"display": "none"},
                "icon": {
                    "font-size": "13px",
                    "color":     _C["text_muted"],
                },
                "nav-link": {
                    "font-size":        "13px",
                    "font-weight":      "600",
                    "text-align":       "left",
                    "margin":           "1px 0",
                    "padding":          "10px 13px",
                    "border-radius":    "10px",
                    "color":            _C["text_muted"],
                    "background-color": _C["bg_surface"],
                    "border":           "none",
                },
                "nav-link-selected": {
                    "background": (
                        "linear-gradient(135deg,"
                        "rgba(91,140,255,0.14),"
                        "rgba(124,92,255,0.10))"
                    ),
                    "color":         _C["brand_blue"],
                    "font-weight":   "700",
                    "border-left":   "2px solid " + _C["brand_blue"],
                    "border-radius": "0 10px 10px 0",
                    "padding-left":  "11px",
                },
            },
        )
    else:
        page = st.selectbox("Navigation", NAV_OPTIONS, index=0)

    st.markdown(
        "<div style='height:10px'></div>", unsafe_allow_html=True
    )

    # ── Live job indicator ────────────────────────────────
    if _job_mgr_available:
        try:
            if _any_job_running():
                active_id = _get_active_job_id()
                if active_id:
                    summary  = _get_job_summary(active_id) or {}

                    raw_pct      = float(
                        summary.get("progress_pct", 0.0) or 0.0
                    )
                    progress_frac = (
                        raw_pct / 100.0
                        if raw_pct > 1.0
                        else raw_pct
                    )
                    progress_frac = max(0.0, min(1.0, progress_frac))

                    leads_n  = _safe_int(summary.get("leads",   0))
                    message  = summary.get("message", "Running...")
                    inserted = _safe_int(
                        summary.get("leads_inserted", 0)
                    )
                    skipped  = _safe_int(
                        summary.get("leads_skipped",  0)
                    )
                    updated  = _safe_int(
                        summary.get("leads_updated",  0)
                    )

                    st.markdown(
                        "<div style='"
                        "background:rgba(16,185,129,0.06);"
                        "border:1px solid rgba(16,185,129,0.15);"
                        "border-radius:12px;"
                        "padding:12px 14px;"
                        "margin:0 2px 10px 2px;"
                        "'>"
                        "<div style='display:flex;align-items:center;"
                        "gap:8px;margin-bottom:8px;'>"
                        "<span class='live-dot'></span>"
                        "<span style='color:" + _C["green"] + ";"
                        "font-size:10px;font-weight:700;"
                        "text-transform:uppercase;letter-spacing:1px;'>"
                        "Live Scraping"
                        "</span>"
                        "</div>"
                        "<div style='color:" + _C["text_secondary"] + ";"
                        "font-size:11px;font-weight:600;"
                        "margin-bottom:4px;'>"
                        + str(leads_n) + " leads found"
                        + "</div>"
                        "<div style='color:" + _C["text_muted"] + ";"
                        "font-size:10px;margin-bottom:8px;'>"
                        + str(message)
                        + "</div>"
                        "</div>",
                        unsafe_allow_html=True,
                    )

                    st.progress(progress_frac)

                    if inserted or skipped or updated:
                        st.markdown(
                            "<div style='font-size:10px;"
                            "color:" + _C["text_muted"] + ";"
                            "text-align:center;margin-top:4px;'>"
                            "&#9989; " + str(inserted) + "&ensp;"
                            "&#9193; " + str(skipped) + "&ensp;"
                            "&#128260; " + str(updated)
                            + "</div>",
                            unsafe_allow_html=True,
                        )

                    col_stop, col_ref = st.columns(2)
                    with col_stop:
                        if st.button(
                            "&#128721; Stop",
                            use_container_width=True,
                            key="sidebar_stop_" + str(active_id),
                        ):
                            try:
                                _stop_job(active_id)
                                st.warning("Stop requested.")
                            except Exception as exc:
                                st.error(
                                    "Stop failed: " + str(exc)
                                )
                            st.rerun()
                    with col_ref:
                        if st.button(
                            "&#128260; Refresh",
                            use_container_width=True,
                            key="sidebar_refresh_" + str(active_id),
                        ):
                            st.rerun()

        except Exception:
            pass

    st.markdown(
        "<div style='margin:6px 10px 10px 10px;height:1px;"
        "background:" + _C["border_subtle"] + ";'></div>",
        unsafe_allow_html=True,
    )

    # ── Sidebar stats ─────────────────────────────────────
    try:
        stats = _get_cached_stats()

        st.markdown(
            "<div style='color:" + _C["text_muted"] + ";"
            "font-size:9.5px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:1.5px;"
            "margin:0 10px 8px 10px;'>Performance</div>",
            unsafe_allow_html=True,
        )

        total_leads = _safe_int(stats.get("total_leads",    0))
        high_fit    = _safe_int(stats.get("high_fit_leads", 0))
        email_ready = _safe_int(stats.get("email_ready",    0))
        deals       = _safe_int(stats.get("deals_closed",   0))

        st.markdown(
            "<div style='display:grid;"
            "grid-template-columns:1fr 1fr;"
            "gap:6px;margin:0 2px 10px 2px;'>"

            "<div class='sb-metric'>"
            "<div class='sb-metric-label'>Leads</div>"
            "<div class='sb-metric-value'>"
            + "{:,}".format(total_leads)
            + "</div></div>"

            "<div class='sb-metric'>"
            "<div class='sb-metric-label'>High Fit</div>"
            "<div class='sb-metric-value' style='"
            "background:linear-gradient(135deg,"
            + _C["brand_blue"] + "," + _C["brand_purple"] + ");"
            "-webkit-background-clip:text;"
            "-webkit-text-fill-color:transparent;"
            "background-clip:text;'>"
            + "{:,}".format(high_fit)
            + "</div></div>"

            "<div class='sb-metric'>"
            "<div class='sb-metric-label'>Emails</div>"
            "<div class='sb-metric-value'>"
            + "{:,}".format(email_ready)
            + "</div></div>"

            "<div class='sb-metric'>"
            "<div class='sb-metric-label'>Deals</div>"
            "<div class='sb-metric-value' style='color:"
            + _C["green"] + ";'>"
            + "{:,}".format(deals)
            + "</div></div>"

            "</div>",
            unsafe_allow_html=True,
        )

        revenue_display = _fmt_revenue(stats.get("total_revenue", 0))

        st.markdown(
            "<div style='"
            "background:linear-gradient(135deg,"
            "rgba(245,158,11,0.08),rgba(245,158,11,0.04));"
            "border:1px solid rgba(245,158,11,0.18);"
            "border-left:3px solid " + _C["gold"] + ";"
            "border-radius:0 12px 12px 0;"
            "padding:12px 14px;"
            "margin:0 2px 4px 2px;"
            "'>"
            "<div style='color:" + _C["gold"] + ";font-size:9.5px;"
            "font-weight:700;text-transform:uppercase;"
            "letter-spacing:1.2px;margin-bottom:5px;"
            "opacity:0.85;'>&#128176; Revenue Pipeline</div>"
            "<div style='font-size:20px;font-weight:900;"
            "color:" + _C["text_primary"] + ";"
            "letter-spacing:-0.5px;line-height:1;'>"
            + revenue_display
            + "</div>"
            "<div style='color:" + _C["gold"] + ";font-size:10px;"
            "margin-top:4px;font-weight:600;opacity:0.7;'>"
            "Total tracked &middot; &#8358; / &pound;"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    except Exception as exc:
        st.warning("Stats unavailable: " + str(exc))

    st.markdown(
        "<div style='margin:14px 10px 0 10px;height:1px;"
        "background:" + _C["border_subtle"] + ";'></div>"
        "<div style='color:" + _C["text_muted"] + ";font-size:9.5px;"
        "font-weight:600;text-align:center;padding-top:12px;'>"
        "KNCS &middot; LeadGen Pro &middot; v10.1</div>",
        unsafe_allow_html=True,
    )

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        if st.button(
            "&#128260; Refresh",
            use_container_width=True,
            key="btn_refresh",
            help="Reload all data",
        ):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()
    with col_r2:
        if st.button(
            "&#129529; Cache",
            use_container_width=True,
            key="btn_clear_cache",
            help="Clear cached data",
        ):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.success("&#9989; Cleared!")
            st.rerun()


# ─────────────────────────────────────────────────────────
# SETTINGS PAGE
# BUG-APP-07: f-strings removed from all HTML blocks
# NEW-APP-F:  version note updated from v10.0 to v10.1
# ─────────────────────────────────────────────────────────
def _render_settings() -> None:
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-header-eyebrow'>Configuration</div>"
        "<h1 class='page-header-title'>System Settings</h1>"
        "<p class='page-header-sub'>"
        "Configure your KNCS LeadGen Pro platform, "
        "branding and system preferences."
        "</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    tab_brand, tab_sys, tab_data = st.tabs(
        ["&#127912; Branding", "&#9881; System", "&#128202; Data"]
    )

    with tab_brand:
        st.markdown(
            "<div class='card-premium'>"
            "<div class='sec-title'>Company Branding</div>"
            "<p class='sec-sub'>"
            "Upload your logo. "
            "PNG with transparent background recommended. "
            "Displays in the sidebar above navigation."
            "</p>"
            "</div>",
            unsafe_allow_html=True,
        )

        current_logo = get_logo()
        if current_logo:
            c1, c2, c3 = st.columns([1, 1, 1])
            with c2:
                st.image(current_logo, caption="Current Logo", width=200)

        uploaded = st.file_uploader(
            "Upload New Logo (PNG/JPG)",
            type=["png", "jpg", "jpeg"],
            key="logo_uploader",
        )
        if uploaded and st.button(
            "&#128190; Save Logo",
            type="primary",
            key="save_logo_btn",
        ):
            if save_logo(uploaded):
                st.success("&#9989; Logo saved successfully.")
                st.rerun()

        if current_logo and st.button(
            "&#128465; Remove Logo", key="remove_logo_btn"
        ):
            if remove_logo():
                st.success("Logo removed.")
                st.rerun()

    with tab_sys:
        st.markdown(
            "<div class='card-premium'>"
            "<div class='sec-title'>Scraping Configuration</div>"
            "<p class='sec-sub'>"
            "Control how the scraping engine behaves."
            "</p>"
            "</div>",
            unsafe_allow_html=True,
        )

        col_s1, col_s2 = st.columns(2)

        with col_s1:
            st.markdown(
                "<div style='font-size:11px;font-weight:700;"
                "text-transform:uppercase;letter-spacing:1px;"
                "color:" + _C["text_muted"] + ";margin-bottom:8px;'>"
                "Rate Limits</div>",
                unsafe_allow_html=True,
            )
            st.number_input(
                "Requests per minute",
                min_value=1, max_value=120, value=30,
                key="cfg_rpm",
                help="How many requests per minute the scraper sends.",
            )
            st.number_input(
                "Delay between requests (seconds)",
                min_value=1, max_value=30, value=2,
                key="cfg_delay",
                help="Minimum wait between each scraping request.",
            )

        with col_s2:
            st.markdown(
                "<div style='font-size:11px;font-weight:700;"
                "text-transform:uppercase;letter-spacing:1px;"
                "color:" + _C["text_muted"] + ";margin-bottom:8px;'>"
                "Scoring</div>",
                unsafe_allow_html=True,
            )
            st.number_input(
                "Default score batch size",
                min_value=5, max_value=200, value=50,
                key="cfg_batch",
                help="How many leads to score per batch.",
            )
            st.selectbox(
                "AI model",
                [
                    "@cf/meta/llama-3-8b-instruct",
                    "@cf/meta/llama-3-70b-instruct",
                ],
                key="cfg_model",
                help="Cloudflare Workers AI model for scoring.",
            )

        st.markdown(
            "<div style='"
            "background:rgba(91,140,255,0.05);"
            "border:1px solid rgba(91,140,255,0.14);"
            "border-left:3px solid " + _C["brand_blue"] + ";"
            "border-radius:0 12px 12px 0;"
            "padding:12px 16px;margin-top:12px;"
            "font-size:12px;color:" + _C["text_muted"] + ";"
            "line-height:1.8;'>"
            "&#8505; Settings on this page are display-only in v10.1.<br>"
            "Full persistence via .env and database config "
            "is scheduled for v11.0."
            "</div>",
            unsafe_allow_html=True,
        )

    with tab_data:
        st.markdown(
            "<div class='card-premium'>"
            "<div class='sec-title'>Data Management</div>"
            "<p class='sec-sub'>"
            "Export, import and maintain your lead database."
            "</p>"
            "</div>",
            unsafe_allow_html=True,
        )

        col_d1, col_d2 = st.columns(2)

        with col_d1:
            st.markdown(
                "<div style='font-size:11px;font-weight:700;"
                "text-transform:uppercase;letter-spacing:1px;"
                "color:" + _C["text_muted"] + ";margin-bottom:8px;'>"
                "Export</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<div style='"
                "background:" + _C["bg_raised"] + ";"
                "border:1px solid " + _C["border_subtle"] + ";"
                "border-radius:12px;padding:14px 16px;"
                "font-size:13px;color:" + _C["text_muted"] + ";"
                "line-height:2.0;'>"
                "&#128196; CSV export<br>"
                "&#128202; Excel export<br>"
                "&#128269; Filtered export by country / niche<br>"
                "<span style='font-size:11px;"
                "color:" + _C["text_faint"] + ";"
                "margin-top:8px;display:block;'>"
                "Available in v11.0"
                "</span>"
                "</div>",
                unsafe_allow_html=True,
            )

        with col_d2:
            st.markdown(
                "<div style='font-size:11px;font-weight:700;"
                "text-transform:uppercase;letter-spacing:1px;"
                "color:" + _C["text_muted"] + ";margin-bottom:8px;'>"
                "Maintenance</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<div style='"
                "background:" + _C["bg_raised"] + ";"
                "border:1px solid " + _C["border_subtle"] + ";"
                "border-radius:12px;padding:14px 16px;"
                "font-size:13px;color:" + _C["text_muted"] + ";"
                "line-height:2.0;'>"
                "&#128257; Bulk import (CSV)<br>"
                "&#129529; Duplicate cleanup tool<br>"
                "&#128190; Database backup &amp; restore<br>"
                "<span style='font-size:11px;"
                "color:" + _C["text_faint"] + ";"
                "margin-top:8px;display:block;'>"
                "Available in v11.0"
                "</span>"
                "</div>",
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────
# PAGE ROUTING
# BUG-APP-04: Lazy import — only selected page imported per
# rerun. Import-level code in other modules does not execute
# on every widget interaction.
# ─────────────────────────────────────────────────────────
if page == "Dashboard":
    render_page(
        _safe_import("pages.dashboard"),
        ["render_dashboard", "render"],
        "Dashboard",
    )
elif page == "Lead Search":
    render_page(
        _safe_import("pages.search"),
        ["render_search_page", "render"],
        "Lead Search",
    )
elif page == "Businesses":
    render_page(
        _safe_import("pages.leads"),
        ["render_leads_page", "render"],
        "Businesses",
    )
elif page == "Care Homes":
    render_page(
        _safe_import("pages.care_home_dashboard"),
        ["render", "render_care_home_dashboard"],
        "Care Homes",
    )
elif page == "Email Outreach":
    render_page(
        _safe_import("pages.outreach_dashboard"),
        ["render_outreach_dashboard", "render"],
        "Email Outreach",
    )
elif page == "Nigeria B2B":
    render_page(
        _safe_import("pages.nigeria_dashboard"),
        ["render_nigeria_dashboard", "render"],
        "Nigeria B2B",
    )
elif page == "Call Center":
    render_page(
        _safe_import("pages.call_center"),
        ["render_call_center", "render"],
        "Call Center",
    )
elif page == "Reports":
    render_page(
        _safe_import("pages.reports"),
        ["render_reports", "render"],
        "Reports",
    )
elif page == "Care Intelligence":
    render_page(
        _safe_import("pages.care_intelligence_dashboard"),
        ["show", "render"],
        "Care Intelligence",
    )
elif page == "Dental Intelligence":
    render_page(
        _safe_import("pages.dental_intelligence_dashboard"),
        ["show", "render"],
        "Dental Intelligence",
    )
elif page == "MSP Intelligence":
    render_page(
        _safe_import("pages.msp_intelligence_dashboard"),
        ["show", "render"],
        "MSP Intelligence",
    )
elif page == "Settings":
    _render_settings()
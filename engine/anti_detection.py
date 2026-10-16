# engine/anti_detection.py
"""
Anti-detection utilities for LeadGen Pro.
v2 — Added Client Hints headers, stronger stealth JS,
     Yelp/YP specific context builders
"""

import random
import asyncio
import time


# ─────────────────────────────────────────────────────────
# User agent pools
# ─────────────────────────────────────────────────────────

DESKTOP_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
        "rv:126.0) Gecko/20100101 Firefox/126.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
        "rv:124.0) Gecko/20100101 Firefox/124.0"
    ),
]

MOBILE_USER_AGENTS = [
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 "
        "like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 "
        "Mobile/15E148 Safari/604.1"
    ),
    (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.6367.82 Mobile Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Linux; Android 13; Samsung "
        "Galaxy S23) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 "
        "Mobile Safari/537.36"
    ),
]

# ─────────────────────────────────────────────────────────
# Client Hints — matched to Chrome 124
# Yelp + Cloudflare verify these headers exist
# ─────────────────────────────────────────────────────────

CHROME_CLIENT_HINTS = {
    "sec-ch-ua": (
        '"Chromium";v="124", '
        '"Google Chrome";v="124", '
        '"Not-A.Brand";v="99"'
    ),
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
}

CHROME_EXTRA_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,"
        "*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate, br",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Cache-Control":             "max-age=0",
    "DNT":                       "1",
}


# ─────────────────────────────────────────────────────────
# Random helpers
# ─────────────────────────────────────────────────────────

def get_random_user_agent(mobile: bool = False) -> str:
    pool = (
        MOBILE_USER_AGENTS if mobile
        else DESKTOP_USER_AGENTS
    )
    return random.choice(pool)


def random_delay(
    min_sec: float = 1.5,
    max_sec: float = 5.0,
) -> float:
    return random.uniform(min_sec, max_sec)


def sleep_random(
    min_sec: float = 1.5,
    max_sec: float = 5.0,
) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


async def async_random_delay(
    min_sec: float = 1.5,
    max_sec: float = 4.0,
) -> None:
    await asyncio.sleep(random.uniform(min_sec, max_sec))


def random_viewport() -> dict:
    sizes = [
        {"width": 1920, "height": 1080},
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1280, "height": 800},
        {"width": 1600, "height": 900},
        {"width": 1024, "height": 768},
    ]
    return random.choice(sizes)


def random_headers(
    referer: str = "",
    mobile: bool = False,
) -> dict:
    ua = get_random_user_agent(mobile=mobile)

    accept_langs = [
        "en-US,en;q=0.9",
        "en-GB,en;q=0.9",
        "en-US,en;q=0.7",
        "en-US,en;q=0.8,fr;q=0.5",
    ]

    headers = {
        "User-Agent":      ua,
        "Accept-Language": random.choice(accept_langs),
        **CHROME_EXTRA_HEADERS,
        **CHROME_CLIENT_HINTS,
    }

    if referer:
        headers["Referer"] = referer

    return headers


# ─────────────────────────────────────────────────────────
# Browser stealth args
# ─────────────────────────────────────────────────────────

def get_stealth_args() -> list:
    return [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
        "--disable-default-apps",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--metrics-recording-only",
        "--mute-audio",
        # Additional stealth
        "--disable-web-security",
        "--allow-running-insecure-content",
        "--disable-features=IsolateOrigins",
        "--disable-site-isolation-trials",
    ]


# ─────────────────────────────────────────────────────────
# Stealth JS — stronger, covers more fingerprints
# ─────────────────────────────────────────────────────────

STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// Realistic plugin list
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin',
              filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer',
              filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client',
              filename: 'internal-nacl-plugin' },
        ];
        plugins.refresh = () => {};
        return plugins;
    }
});

// Languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en']
});

// Platform
Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32'
});

// Hardware concurrency
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8
});

// Device memory
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8
});

// Chrome runtime
window.chrome = {
    app: { isInstalled: false },
    webstore: { onInstallStageChanged: {}, onDownloadProgress: {} },
    runtime: {
        PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android',
                      CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
        PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
        RequestUpdateCheckStatus: { THROTTLED: 'throttled',
                                    NO_UPDATE: 'no_update',
                                    UPDATE_AVAILABLE: 'update_available' },
        OnInstalledReason: { INSTALL: 'install', UPDATE: 'update',
                             CHROME_UPDATE: 'chrome_update',
                             SHARED_MODULE_UPDATE: 'shared_module_update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update',
                                   OS_UPDATE: 'os_update',
                                   PERIODIC: 'periodic' }
    }
};

// Permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);

// WebGL vendor/renderer
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};

// Hide automation in toString
const nativeToString = Function.prototype.toString;
Function.prototype.toString = function() {
    if (this === window.navigator.permissions.query) {
        return 'function query() { [native code] }';
    }
    return nativeToString.call(this);
};
"""


async def apply_stealth(page) -> None:
    """Inject stealth JS into every page."""
    await page.add_init_script(STEALTH_JS)


# ─────────────────────────────────────────────────────────
# Yelp-specific browser context builder
# ─────────────────────────────────────────────────────────

async def build_yelp_context(browser):
    """
    Creates a browser context specifically tuned
    for Yelp's Cloudflare detection bypass.
    Uses US locale + realistic headers.
    """
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=ua,
        locale="en-US",
        timezone_id="America/Chicago",
        is_mobile=False,
        java_script_enabled=True,
        extra_http_headers={
            **CHROME_CLIENT_HINTS,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         "https://www.google.com/",
            "sec-fetch-dest":  "document",
            "sec-fetch-mode":  "navigate",
            "sec-fetch-site":  "cross-site",
            "sec-fetch-user":  "?1",
            "DNT":             "1",
        },
    )
    return context


# ─────────────────────────────────────────────────────────
# YellowPages-specific browser context builder
# ─────────────────────────────────────────────────────────

async def build_yp_context(browser, is_uk: bool = False):
    """
    Creates a browser context tuned for
    YellowPages.com (US) or Yell.com (UK).
    """
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=ua,
        locale="en-GB" if is_uk else "en-US",
        timezone_id=(
            "Europe/London" if is_uk
            else "America/New_York"
        ),
        is_mobile=False,
        java_script_enabled=True,
        extra_http_headers={
            **CHROME_CLIENT_HINTS,
            "Accept-Language": (
                "en-GB,en;q=0.9" if is_uk
                else "en-US,en;q=0.9"
            ),
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         "https://www.google.com/",
            "sec-fetch-dest":  "document",
            "sec-fetch-mode":  "navigate",
            "sec-fetch-site":  "cross-site",
            "sec-fetch-user":  "?1",
            "DNT":             "1",
        },
    )
    return context


# ─────────────────────────────────────────────────────────
# Human simulation
# ─────────────────────────────────────────────────────────

async def human_scroll(
    page,
    scroll_times: int = 3,
) -> None:
    for _ in range(scroll_times):
        scroll_amount = random.randint(300, 700)
        await page.mouse.wheel(0, scroll_amount)
        await async_random_delay(0.5, 1.5)


async def human_mouse_move(page) -> None:
    try:
        viewport = await page.viewport_size()
        if viewport:
            x = random.randint(
                100, viewport["width"] - 100
            )
            y = random.randint(
                100, viewport["height"] - 100
            )
            await page.mouse.move(x, y)
            await async_random_delay(0.1, 0.4)
    except Exception:
        pass


async def human_type(
    page,
    selector: str,
    text: str,
    delay_ms: int = None,
) -> None:
    if delay_ms is None:
        delay_ms = random.randint(50, 150)
    try:
        await page.type(selector, text, delay=delay_ms)
    except Exception:
        await page.fill(selector, text)


async def accept_cookies(page) -> None:
    selectors = [
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        'button:has-text("I Accept")',
        'button:has-text("OK")',
        'button:has-text("Agree")',
        '[id*="accept"]',
        '[class*="accept"]',
    ]
    for selector in selectors:
        try:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click()
                await async_random_delay(0.5, 1.0)
                return
        except Exception:
            continue


# ─────────────────────────────────────────────────────────
# Session rotation helper
# ─────────────────────────────────────────────────────────

def get_session_headers(source: str = "") -> dict:
    referers = {
        "google_maps":  "https://www.google.com/",
        "b2bhint":      "https://b2bhint.com/",
        "google":       "https://www.google.com/",
        "bing":         "https://www.bing.com/",
        "duckduckgo":   "https://duckduckgo.com/",
        "yelp":         "https://www.yelp.com/",
        "yellowpages":  "https://www.yellowpages.com/",
    }
    referer = referers.get(source, "")
    return random_headers(referer=referer)
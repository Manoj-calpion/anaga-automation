"""Playwright persistent context, human-pace delays, site-instability helpers."""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from models import CloudflareChallenge, CssErrorModal

SEARCH_URL_DEFAULT = "https://goals.sos.ga.gov/GASOSOneStop/s/licensee-search"

# body.innerText does not pierce closed/open shadow roots; walk them ourselves.
DEEP_TEXT_JS = """
function deepText(node) {
  if (!node) return '';
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
  let s = '';
  if (node.shadowRoot) s += deepText(node.shadowRoot);
  const children = node.childNodes || [];
  for (const c of children) s += deepText(c);
  return s;
}
"""


class RateLimiter:
    def __init__(self, max_per_hour: int):
        self.max_per_hour = max_per_hour
        self._hits: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            cutoff = now - 3600
            while self._hits and self._hits[0] < cutoff:
                self._hits.popleft()
            if len(self._hits) < self.max_per_hour:
                self._hits.append(now)
                return
            sleep_for = max(1.0, self._hits[0] + 3600 - now)
            print(f"Rate limit {self.max_per_hour}/hour reached; waiting {sleep_for:.0f}s")
            await asyncio.sleep(sleep_for)


class BrowserSession:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.rate = RateLimiter(int(config.get("max_per_hour", 100)))

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        headed = bool(self.config.get("headed", True))
        user_data = Path(self.config.get("user_data_dir", ".browser-profile"))
        channel = (self.config.get("browser_channel") or "").strip()
        if channel:
            user_data = Path(str(user_data) + f"-{channel}")
        user_data.mkdir(parents=True, exist_ok=True)
        width = int(self.config.get("viewport_width", 1400))
        height = int(self.config.get("viewport_height", 1000))
        self._pw = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(user_data.resolve()),
            "headless": not headed,
            "viewport": {"width": width, "height": height},
            "ignore_https_errors": False,
            "args": ["--disable-dev-shm-usage"],
        }
        if channel:
            launch_kwargs["channel"] = channel
        self.context = await _launch_persistent(self._pw, launch_kwargs, user_data)
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()
        self.page.set_default_timeout(int(self.config.get("interaction_timeout_ms", 30000)))

    async def close(self) -> None:
        if self.context:
            await self.context.close()
            self.context = None
            self.page = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def ui_pause(self) -> None:
        lo = int(self.config.get("ui_delay_ms_min", 300)) / 1000
        hi = int(self.config.get("ui_delay_ms_max", 800)) / 1000
        await asyncio.sleep(random.uniform(lo, hi))

    async def provider_pause(self) -> None:
        lo = float(self.config.get("provider_delay_s_min", 3))
        hi = float(self.config.get("provider_delay_s_max", 8))
        await asyncio.sleep(random.uniform(lo, hi))

    async def goto_search(self) -> Page:
        """Land on the search form. Do not reload if the form is already up.

        Reloading while Cloudflare's checkbox is showing resets it — that is why
        the box keeps coming back if you click it over and over.
        """
        assert self.page
        url = self.config.get("search_url", SEARCH_URL_DEFAULT)
        wait_s = float(self.config.get("cloudflare_wait_s", 300))
        if await _cloudflare_blocking(self.page):
            print("  Cloudflare checkbox is showing — not reloading. Waiting for you to finish it once.")
            await wait_through_cloudflare(self.page, timeout_s=wait_s)
            return self.page
        if await _search_form_visible(self.page):
            return self.page
        await self.page.goto(url, wait_until="domcontentloaded")
        await wait_through_cloudflare(self.page, timeout_s=wait_s)
        if not await _cloudflare_blocking(self.page):
            await dismiss_css_error(self.page)
        # Lightning comboboxes hydrate after the input appears.
        await asyncio.sleep(1.2)
        return self.page


PROFILE_LOCK_FILES = (
    "SingletonLock",
    "SingletonSocket",
    "SingletonCookie",
    "lockfile",
    "DevToolsActivePort",
)


def _pids_using_profile(profile: Path) -> list[int]:
    needle = str(profile.resolve())
    escaped = needle.replace("'", "''")
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{escaped}') }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    me = os.getpid()
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        token = line.strip()
        if token.isdigit():
            pid = int(token)
            if pid != me:
                pids.append(pid)
    return pids


def prepare_user_data_dir(profile: Path, *, kill: bool = False) -> None:
    """Make a persistent profile launchable after a crashed or leftover run.

    Only touches processes whose command line includes this profile path —
    never the user's everyday Chrome session unless it was started with
    this automation directory.
    """
    profile.mkdir(parents=True, exist_ok=True)
    if kill:
        for pid in _pids_using_profile(profile):
            print(f"Stopping leftover browser pid {pid} that holds {profile}")
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            time.sleep(0.3)
    for name in PROFILE_LOCK_FILES:
        path = profile / name
        if path.exists() or path.is_symlink():
            try:
                path.unlink()
            except OSError:
                pass


def _is_profile_busy(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "existing browser session" in msg or "already in use" in msg or "profile is already in use" in msg


async def _launch_persistent(pw: Playwright, launch_kwargs: dict[str, Any], user_data: Path):
    variants: list[dict[str, Any]] = [dict(launch_kwargs)]
    if launch_kwargs.get("channel"):
        bundled = dict(launch_kwargs)
        bundled.pop("channel", None)
        variants.append(bundled)

    last_exc: BaseException | None = None
    for i, kwargs in enumerate(variants):
        for attempt in range(2):
            prepare_user_data_dir(user_data, kill=attempt > 0)
            try:
                return await pw.chromium.launch_persistent_context(**kwargs)
            except Exception as exc:
                last_exc = exc
                if _is_profile_busy(exc):
                    print(
                        "Automation profile is already open (leftover window from a previous run). "
                        "Closing it and retrying..."
                    )
                    prepare_user_data_dir(user_data, kill=True)
                    await asyncio.sleep(1.2)
                    continue
                if kwargs.get("channel") and i == 0:
                    print(f"Could not launch channel={kwargs['channel']} ({exc}); trying bundled Chromium")
                    break
                raise
    assert last_exc is not None
    raise last_exc


async def _cloudflare_blocking(page: Page) -> bool:
    """True when Cloudflare is in front of the search form (do not click anything)."""
    try:
        title = (await page.title()).lower()
    except Exception:
        title = ""
    if title in {"just a moment...", "just a moment"} or "attention required" in title:
        return True
    frames = (
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[src*="turnstile"]',
        'iframe[title*="Widget containing a Cloudflare"]',
    )
    for sel in frames:
        try:
            loc = page.locator(sel)
            if await loc.count() and await loc.first.is_visible():
                return True
        except Exception:
            pass
    try:
        body = (await page.locator("body").inner_text(timeout=800)).lower()[:2500]
    except Exception:
        body = ""
    needles = (
        "verify you are human",
        "confirm you are human",
        "needs to review the security",
        "checking your browser before accessing",
    )
    if any(n in body for n in needles) and "license number" not in body:
        return True
    return False


async def _search_form_visible(page: Page) -> bool:
    if await _cloudflare_blocking(page):
        return False
    form = page.locator('input[name="licenseNumber"]')
    try:
        return bool(await form.count()) and await form.first.is_visible()
    except Exception:
        return False


async def wait_through_cloudflare(page: Page, timeout_s: float = 300) -> None:
    """Wait until the search form is usable. Never click the Cloudflare box.

    Clicking the checkbox repeatedly makes Cloudflare show it again.
    """
    prompted = False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await _search_form_visible(page):
            if prompted:
                print("  Cloudflare passed. Continuing with the search form.")
            return
        blocking = await _cloudflare_blocking(page)
        if blocking and not prompted:
            print()
            print("  ============================================================")
            print("  Cloudflare is asking 'verify you are human'.")
            print("  Do this ONCE in the Chrome window:")
            print("    1. Click the checkbox a single time.")
            print("    2. Wait 10–20 seconds. Do not click it again.")
            print("    3. Do not click Search. Do not refresh the page.")
            print("  The script is paused and will continue by itself.")
            print("  ============================================================")
            print()
            prompted = True
            # Give the widget time after one click; do not poke the page.
            await asyncio.sleep(2)
            continue
        await asyncio.sleep(1.0)
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    raise CloudflareChallenge(
        f"Cloudflare check did not clear after {timeout_s:.0f}s: url={page.url!r} title={title!r}"
    )


async def raise_if_cloudflare(page: Page, persist_only: bool = True) -> None:
    if await _search_form_visible(page):
        return
    if await _cloudflare_blocking(page):
        await wait_through_cloudflare(page, timeout_s=300)


async def dismiss_css_error(page: Page) -> bool:
    """Return True if the Lightning CSS Error modal was present and Refresh was clicked."""
    try:
        if await _cloudflare_blocking(page):
            return False
        modal = page.get_by_text("Sorry to interrupt", exact=False)
        if await modal.count() == 0:
            return False
        if not await modal.first.is_visible():
            return False
        refresh = page.get_by_role("button", name="Refresh")
        if await refresh.count():
            await refresh.first.click()
            await page.wait_for_load_state("domcontentloaded")
            return True
        raise CssErrorModal("CSS Error modal without Refresh")
    except CssErrorModal:
        raise
    except Exception:
        return False


async def real_click(locator) -> None:
    """Trusted Playwright click — never element.evaluate click()."""
    await locator.click()


async def collect_shadow_tokens(page: Page) -> list[str]:
    return await page.evaluate(
        """() => {
          const out = [];
          function walk(node) {
            if (!node) return;
            if (node.nodeType === Node.TEXT_NODE) {
              const t = node.textContent.replace(/\\s+/g, ' ').trim();
              if (t) out.push(t);
              return;
            }
            if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) {
              return;
            }
            if (node.shadowRoot) walk(node.shadowRoot);
            const children = node.childNodes || [];
            for (const c of children) walk(c);
          }
          walk(document.body);
          return out;
        }"""
    )

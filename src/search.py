"""Licensee-search form: SLDS comboboxes, trusted Search click, result parse."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import unquote

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from browser import dismiss_css_error, real_click, raise_if_cloudflare, collect_shadow_tokens
from models import SearchHit

RECAPTCHA_NEEDLE = "V3 Recaptcha failed in apex"
SELECT_BUTTON_RE = re.compile(r"SELECT\s+License\s+Number\s+(\S+)", re.I)

# Stable name attributes from SPEC §2.4
NAME_PROFESSION = "GASOS_Profession_Type__c"
NAME_LICENSE_TYPE = "GASOS_License_Type__c"
NAME_LICENSE_NUMBER = "licenseNumber"
NAME_FIRST = "firstName"
NAME_LAST = "lastName"


class RecaptchaFailed(RuntimeError):
    pass


class SearchValidationError(RuntimeError):
    pass


def parse_aura_search_payload(text: str) -> tuple[list[SearchHit], int | None, bool]:
    """Parse an Aura execute response body. Returns (hits, total_rows, recaptcha_failed)."""
    if RECAPTCHA_NEEDLE.lower() in text.lower():
        return [], None, True
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], None, False
    rows: list[dict[str, Any]] = []
    total = None

    def walk(obj: Any) -> None:
        nonlocal total, rows
        if isinstance(obj, dict):
            if isinstance(obj.get("rows"), list) and obj["rows"] and isinstance(obj["rows"][0], dict):
                if "licenseNumber" in obj["rows"][0] or "encryptedLicenseId" in obj["rows"][0]:
                    rows = obj["rows"]
                    total = obj.get("totalRows", len(rows))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    hits = [hit_from_row(r) for r in rows]
    return hits, total, False


def hit_from_row(row: dict[str, Any]) -> SearchHit:
    return SearchHit(
        license_number=str(row.get("licenseNumber") or "").strip(),
        full_name=str(row.get("fullName") or "").strip(),
        license_type=str(row.get("licenseType") or "").strip(),
        status=str(row.get("status") or "").strip(),
        encrypted_license_id=str(row.get("encryptedLicenseId") or "").strip(),
        select_button=str(row.get("selectButton") or "").strip(),
        raw=row,
    )


class AuraTap:
    """Observes Aura POSTs the browser already makes. Does not send Aura requests."""

    def __init__(self, page: Page):
        self.page = page
        self.bodies: list[str] = []
        self._listening = False

    def start(self) -> None:
        if self._listening:
            return
        self.page.on("response", self._on_response)
        self._listening = True

    def stop(self) -> None:
        if not self._listening:
            return
        try:
            self.page.remove_listener("response", self._on_response)
        except Exception:
            pass
        self._listening = False

    async def _on_response(self, response) -> None:
        url = response.url
        if "aura" not in url.lower() or "ApexAction" not in url:
            return
        try:
            text = await response.text()
        except Exception:
            return
        self.bodies.append(text)

    def latest_search(self) -> tuple[list[SearchHit], int | None, bool]:
        recaptcha = False
        hits: list[SearchHit] = []
        total = None
        for body in reversed(self.bodies):
            h, t, failed = parse_aura_search_payload(body)
            if failed:
                recaptcha = True
                return [], None, True
            if h or t is not None:
                return h, t, False
        return hits, total, recaptcha


async def _visible_combobox_button(page: Page, field_name: str):
    loc = page.locator(f'button[name="{field_name}"]')
    await loc.first.wait_for(state="visible", timeout=30000)
    n = await loc.count()
    for i in range(n):
        el = loc.nth(i)
        try:
            if await el.is_visible():
                return el
        except Exception:
            continue
    return loc.first


async def _combobox_shows(button, option_text: str) -> bool:
    text = " ".join((await button.inner_text()).split())
    if not text:
        return False
    # Placeholder must not be the only content.
    if option_text.lower() not in text.lower():
        return False
    if text.strip().lower() in {"select an option", "select", ""}:
        return False
    return True


async def select_combobox(page: Page, field_name: str, option_text: str, ui_pause) -> None:
    """SLDS faux-combobox: click the button, then click the option by text.

    These are <button>s, not <select>s. Typing .value or keyboard-filter is unreliable.
    """
    button = await _visible_combobox_button(page, field_name)
    if await _combobox_shows(button, option_text):
        return

    last_shown = ""
    for attempt in range(4):
        button = await _visible_combobox_button(page, field_name)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await ui_pause()
        await real_click(button)
        await ui_pause()

        listbox = page.get_by_role("listbox")
        try:
            await listbox.last.wait_for(state="visible", timeout=8000)
        except PlaywrightTimeout:
            # Button click sometimes toggles closed; click again.
            await real_click(button)
            await ui_pause()
            try:
                await listbox.last.wait_for(state="visible", timeout=8000)
            except PlaywrightTimeout:
                last_shown = (await button.inner_text()).strip()
                continue

        box = listbox.last
        picked = False
        option = box.get_by_role("option", name=option_text, exact=True)
        if await option.count() == 0:
            option = box.get_by_text(option_text, exact=True)
        if await option.count():
            await option.first.scroll_into_view_if_needed()
            await ui_pause()
            await real_click(option.first)
            picked = True
        if not picked:
            # Fallback: any visible option whose accessible name contains the label.
            option = box.get_by_role("option", name=re.compile(rf"^{re.escape(option_text)}$", re.I))
            if await option.count():
                await real_click(option.first)
                picked = True

        for _ in range(15):
            if await _combobox_shows(button, option_text):
                return
            await asyncio.sleep(0.25)
        last_shown = (await button.inner_text()).strip()

    raise SearchValidationError(
        f"Could not select {option_text!r} on combobox {field_name}; showing {last_shown!r}"
    )


async def combobox_value(page: Page, field_name: str) -> str:
    button = page.locator(f'button[name="{field_name}"]')
    return (await button.inner_text()).strip()


async def fill_input(page: Page, name: str, value: str, ui_pause) -> None:
    field = page.locator(f'input[name="{name}"]')
    await field.wait_for(state="visible")
    await ui_pause()
    await field.click()
    await field.fill("")
    if value:
        await field.press_sequentially(value, delay=40)
    await ui_pause()


async def input_value(page: Page, name: str) -> str:
    field = page.locator(f'input[name="{name}"]')
    return await field.input_value()


async def ensure_individual_radio(page: Page, ui_pause) -> None:
    radio = page.locator('input[name="radioGroup"][value="Individual"]')
    if await radio.count() == 0:
        radio = page.get_by_role("radio", name=re.compile("Individual", re.I))
    if await radio.count() == 0:
        return
    target = radio.first
    try:
        if await target.is_checked():
            return
    except Exception:
        pass
    await ui_pause()
    await real_click(target)


async def form_matches(
    page: Page,
    *,
    profession: str,
    license_type: str,
    license_number: str,
) -> bool:
    prof = await combobox_value(page, NAME_PROFESSION)
    ltype = await combobox_value(page, NAME_LICENSE_TYPE)
    number = await input_value(page, NAME_LICENSE_NUMBER)
    return (
        profession.lower() in prof.lower()
        and license_type.lower() in ltype.lower()
        and number.strip() == license_number.strip()
    )


async def fill_search_form(
    page: Page,
    *,
    profession: str,
    license_type: str,
    license_number: str,
    ui_pause,
    names_empty: bool = True,
) -> None:
    await raise_if_cloudflare(page)
    from browser import _cloudflare_blocking, wait_through_cloudflare

    if await _cloudflare_blocking(page):
        await wait_through_cloudflare(page, timeout_s=300)
    await dismiss_css_error(page)
    await ensure_individual_radio(page, ui_pause)
    await select_combobox(page, NAME_PROFESSION, profession, ui_pause)
    # License Type depends on Profession Type and repopulates via Apex.
    await ui_pause()
    try:
        await page.wait_for_timeout(2000)
    except Exception:
        pass
    # Opening License Type before options exist fails; retry a few times.
    last_err: Exception | None = None
    for _ in range(4):
        try:
            await select_combobox(page, NAME_LICENSE_TYPE, license_type, ui_pause)
            last_err = None
            break
        except Exception as exc:
            last_err = exc
            await ui_pause()
    if last_err:
        raise last_err
    if names_empty:
        # Clear names so Profession + License Number (or Profession + License Type) are the pair.
        first = page.locator(f'input[name="{NAME_FIRST}"]')
        last = page.locator(f'input[name="{NAME_LAST}"]')
        if await first.count():
            await first.fill("")
        if await last.count():
            await last.fill("")
    if license_number:
        await fill_input(page, NAME_LICENSE_NUMBER, license_number, ui_pause)
    else:
        field = page.locator(f'input[name="{NAME_LICENSE_NUMBER}"]')
        if await field.count():
            await field.fill("")

    if license_number and not await form_matches(
        page, profession=profession, license_type=license_type, license_number=license_number
    ):
        # Silent state reset — refill once.
        await select_combobox(page, NAME_PROFESSION, profession, ui_pause)
        await select_combobox(page, NAME_LICENSE_TYPE, license_type, ui_pause)
        if license_number:
            await fill_input(page, NAME_LICENSE_NUMBER, license_number, ui_pause)
        if not await form_matches(
            page, profession=profession, license_type=license_type, license_number=license_number
        ):
            raise SearchValidationError("form values wiped after refill (silent state reset)")


async def click_search(page: Page, ui_pause, mode: str = "auto") -> None:
    await ui_pause()
    if mode == "human":
        print("\n>>> Click Search in the browser window, wait for results, then press Enter here.")
        await _wait_stdin()
        return
    button = page.get_by_role("button", name=re.compile(r"^Search$", re.I))
    if await button.count() == 0:
        button = page.locator('button:has-text("Search")')
    await button.first.wait_for(state="visible")
    await real_click(button.first)
    print("  Search clicked.", flush=True)


async def _wait_stdin() -> None:
    import asyncio
    import sys

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, sys.stdin.readline)


async def wait_for_results(page: Page, timeout_ms: int = 30000) -> None:
    # Results table, empty-state copy, or an error toast (shadow-DOM aware).
    try:
        await page.wait_for_function(
            """() => {
              function deepText(node) {
                if (!node) return '';
                if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
                let s = '';
                if (node.shadowRoot) s += deepText(node.shadowRoot);
                const children = node.childNodes || [];
                for (const c of children) s += deepText(c);
                return s;
              }
              const t = deepText(document.body);
              return /SELECT License Number/i.test(t)
                  || /no records/i.test(t)
                  || /no results/i.test(t)
                  || /Complete at least two/i.test(t)
                  || /Recaptcha failed/i.test(t)
                  || /Sorry to interrupt/i.test(t);
            }""",
            timeout=timeout_ms,
        )
    except PlaywrightTimeout:
        tokens = await collect_shadow_tokens(page)
        joined = " ".join(tokens)
        if "SELECT License Number" not in joined and "no record" not in joined.lower():
            raise


def hits_from_select_buttons(labels: list[str]) -> list[SearchHit]:
    hits = []
    for label in labels:
        m = SELECT_BUTTON_RE.search(label)
        if m:
            hits.append(
                SearchHit(
                    license_number=m.group(1).strip(),
                    select_button=label.strip(),
                )
            )
    return hits


async def read_select_button_labels(page: Page) -> list[str]:
    buttons = page.get_by_role("button", name=re.compile(r"SELECT License Number", re.I))
    n = await buttons.count()
    labels = []
    for i in range(n):
        labels.append((await buttons.nth(i).inner_text()).strip())
    return labels


async def parse_results_from_dom(page: Page) -> list[SearchHit]:
    labels = await read_select_button_labels(page)
    return hits_from_select_buttons(labels)


def decode_encrypted_id_variants(token: str) -> list[str]:
    """Payload tokens are single-encoded; the address bar uses double-encoding."""
    variants = []
    seen = set()
    current = token
    for _ in range(3):
        if current not in seen:
            variants.append(current)
            seen.add(current)
        nxt = unquote(current)
        if nxt == current:
            break
        current = nxt
    # Also offer an extra-encoded form of the original
    from urllib.parse import quote

    extra = quote(token, safe="")
    if extra not in seen:
        variants.insert(1, extra)
    return variants


async def click_select_for_license(page: Page, license_number: str, ui_pause) -> None:
    button = page.get_by_role(
        "button", name=re.compile(rf"SELECT License Number\s+{re.escape(license_number)}", re.I)
    )
    await button.first.wait_for(state="visible")
    await ui_pause()
    await real_click(button.first)
    await page.wait_for_url(re.compile(r"selectedlicenseId="), timeout=30000)

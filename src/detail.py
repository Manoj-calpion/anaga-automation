"""Detail-page scrape and primary-source PDF capture."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from browser import collect_shadow_tokens, dismiss_css_error, raise_if_cloudflare
from models import LicenseRecord
from naming import is_blank

LABELS = [
    "FIRST NAME",
    "MIDDLE",
    "LAST NAME",
    "ADDRESS",
    "LICENSE NUMBER",
    "PROFESSION",
    "LICENSE TYPE",
    "SUB TYPE",
    "OBTAINED BY",
    "STATUS",
    "ISSUED",
    "EXPIRES",
    "LAST RENEWAL DATE",
]

FIELD_MAP = {
    "FIRST NAME": "first_name",
    "MIDDLE": "middle",
    "LAST NAME": "last_name",
    "ADDRESS": "address",
    "LICENSE NUMBER": "license_number",
    "PROFESSION": "profession",
    "LICENSE TYPE": "license_type",
    "SUB TYPE": "sub_type",
    "OBTAINED BY": "obtained_by",
    "STATUS": "status",
    "ISSUED": "issued",
    "EXPIRES": "expires",
    "LAST RENEWAL DATE": "last_renewal_date",
}


def _normalize_tokens(tokens: list[str]) -> list[str]:
    out = []
    for t in tokens:
        s = " ".join(t.split())
        if s:
            out.append(s)
    return out


def _is_label_at(tokens: list[str], i: int, label: str) -> int | None:
    """If tokens at i match label (one token or split words), return last index of the match."""
    if i >= len(tokens):
        return None
    if tokens[i].upper() == label:
        return i
    parts = label.split()
    if i + len(parts) <= len(tokens):
        joined = " ".join(tokens[i : i + len(parts)]).upper()
        if joined == label:
            return i + len(parts) - 1
    return None


def _label_here(tokens: list[str], i: int) -> str | None:
    # Prefer longer labels first so LICENSE TYPE wins over nothing (we don't have LICENSE alone).
    for label in sorted(LABELS, key=len, reverse=True):
        if _is_label_at(tokens, i, label) is not None:
            return label
    return None


def parse_fields_from_tokens(tokens: list[str]) -> dict[str, str]:
    tokens = _normalize_tokens(tokens)
    found: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        label = _label_here(tokens, i)
        if not label:
            i += 1
            continue
        end = _is_label_at(tokens, i, label)
        assert end is not None
        j = end + 1
        value_parts: list[str] = []
        while j < len(tokens) and _label_here(tokens, j) is None:
            value_parts.append(tokens[j])
            j += 1
        value = " ".join(value_parts).strip() if value_parts else "-"
        found[label] = value
        i = j
    return found


def record_from_fields(fields: dict[str, str]) -> LicenseRecord:
    kwargs = {}
    for label, attr in FIELD_MAP.items():
        kwargs[attr] = fields.get(label, "-")
        if kwargs[attr] == "":
            kwargs[attr] = "-"
    return LicenseRecord(**kwargs)


async def wait_for_detail(page: Page, timeout_ms: int = 30000) -> None:
    await raise_if_cloudflare(page)
    await dismiss_css_error(page)
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
              return /LICENSE NUMBER/i.test(t) && (/EXPIRES/i.test(t) || /STATUS/i.test(t));
            }""",
            timeout=timeout_ms,
        )
    except PlaywrightTimeout as exc:
        raise RuntimeError("detail page did not render LICENSE NUMBER / EXPIRES") from exc


async def scrape_detail(page: Page) -> LicenseRecord:
    await wait_for_detail(page)
    tokens = await collect_shadow_tokens(page)
    fields = parse_fields_from_tokens(tokens)
    if "LICENSE NUMBER" not in fields:
        # One more pass after a short paint wait — DOM can lead the paint, not the reverse.
        import asyncio

        await asyncio.sleep(1.5)
        tokens = await collect_shadow_tokens(page)
        fields = parse_fields_from_tokens(tokens)
    return record_from_fields(fields)


def numbers_match(requested: str, scraped: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "").upper()

    if is_blank(scraped):
        return False
    return norm(requested) == norm(scraped)


async def save_page_pdf(page: Page, dest: Path) -> None:
    """Capture the current page as PDF (print), without annotating content.

    Headed Chromium often rejects page.pdf(); CDP Page.printToPDF is the
    Ctrl+P equivalent and is attempted first.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    cdp = await page.context.new_cdp_session(page)
    try:
        result = await cdp.send(
            "Page.printToPDF",
            {
                "printBackground": True,
                "paperWidth": 8.5,
                "paperHeight": 11,
                "preferCSSPageSize": True,
            },
        )
        data = base64.b64decode(result["data"])
        dest.write_bytes(data)
        return
    except Exception:
        pass
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass
    try:
        await page.pdf(path=str(dest), print_background=True, format="Letter")
        return
    except Exception as exc:
        raise RuntimeError(
            "PDF capture failed in headed Chromium (page.pdf and CDP printToPDF). "
            "Print the page manually or run with a display that supports printToPDF."
        ) from exc


def detail_url(search_url: str, token: str) -> str:
    base = search_url.split("?")[0]
    return f"{base}?selectedlicenseId={token}&searchType=Individual"

"""Phase 0 spike: roster search + encryptedLicenseId deep-link test.

    python spike/phase0_roster_test.py --config config.yaml

Writes spike/phase0_result.json. Does not cache tokens for the pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from browser import BrowserSession  # noqa: E402
from detail import detail_url, scrape_detail  # noqa: E402
from main import load_yaml  # noqa: E402
from search import (  # noqa: E402
    AuraTap,
    RecaptchaFailed,
    click_search,
    decode_encrypted_id_variants,
    fill_search_form,
    parse_results_from_dom,
    wait_for_results,
)


async def run_spike(config_path: Path) -> dict:
    config = load_yaml(config_path)
    search_url = config.get("search_url")
    profession = config.get("profession_type", "Behavior Analyst")
    license_type = config.get("license_type_default", "Behavior Analyst")
    result: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "search_url": search_url,
        "profession": profession,
        "license_type": license_type,
        "total_rows": None,
        "dom_hit_count": 0,
        "aura_hit_count": 0,
        "paginated": None,
        "sample_encrypted_license_id": None,
        "deep_link_attempts": [],
        "roster_tokens_usable": False,
        "notes": [],
    }

    async with BrowserSession(config) as session:
        page = session.page
        assert page
        tap = AuraTap(page)
        tap.start()
        try:
            await session.goto_search()
            await fill_search_form(
                page,
                profession=profession,
                license_type=license_type,
                license_number="",
                ui_pause=session.ui_pause,
            )
            tap.bodies.clear()
            await click_search(page, session.ui_pause, mode=config.get("search_click_mode", "auto"))
            await wait_for_results(page)
            hits, total, recaptcha = tap.latest_search()
            if recaptcha:
                raise RecaptchaFailed("V3 Recaptcha failed in apex during Phase 0")
            result["total_rows"] = total
            result["aura_hit_count"] = len(hits)
            result["dom_hit_count"] = len(await parse_results_from_dom(page))
            if not hits:
                hits = await parse_results_from_dom(page)
            tokens = [h.encrypted_license_id for h in hits if h.encrypted_license_id]
            result["notes"].append(
                "If aura_hit_count < total_rows, the result set is paginated or truncated."
            )
            if total is not None:
                result["paginated"] = len(hits) < int(total)
            if not tokens:
                result["notes"].append(
                    "No encryptedLicenseId in observed Aura payload; deep-link test skipped. "
                    "Keep lookup_mode: per_license."
                )
                return result
            token = tokens[0]
            result["sample_encrypted_license_id"] = token
            variants = decode_encrypted_id_variants(token)
            for variant in variants:
                url = detail_url(search_url, variant)
                attempt = {"variant": variant, "url": url, "ok": False, "error": "", "license_number": ""}
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    await session.ui_pause()
                    record = await scrape_detail(page)
                    attempt["ok"] = bool(record.license_number and record.license_number != "-")
                    attempt["license_number"] = record.license_number
                    attempt["status"] = record.status
                    attempt["expires"] = record.expires
                    if attempt["ok"]:
                        result["roster_tokens_usable"] = True
                        result["deep_link_attempts"].append(attempt)
                        result["notes"].append(
                            "Phase 0 SUCCESS: result-list encryptedLicenseId opened a detail record. "
                            "Set lookup_mode: roster in config.yaml."
                        )
                        break
                except Exception as exc:
                    attempt["error"] = f"{type(exc).__name__}: {exc}"
                result["deep_link_attempts"].append(attempt)
            if not result["roster_tokens_usable"]:
                result["notes"].append(
                    "Phase 0 FAIL: tokens from the result list did not open detail. "
                    "Keep lookup_mode: per_license and click SELECT from the same search."
                )
        except RecaptchaFailed as exc:
            result["notes"].append(f"CAPTCHA failed: {exc}")
            return result
        except Exception as exc:
            result["notes"].append(f"{type(exc).__name__}: {exc}")
            return result
        finally:
            tap.stop()
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    payload = asyncio.run(run_spike(config_path))
    out = ROOT / "spike" / "phase0_result.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {out}")
    raise SystemExit(0 if payload.get("roster_tokens_usable") else 2)


if __name__ == "__main__":
    main()

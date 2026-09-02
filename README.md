# Anaga Automation

**Georgia Secretary of State — Behavior Analyst license verification, end to end.**

A headed Chrome bot that looks up each provider on the public GOALS licensee search, reads the primary-source record, and files a verification PDF under a stable name. CSV in. PDFs and a run log out. No GUI, no database, no captcha-solving service.

---

## Why this exists

Credentialing still walks the Georgia SOS Behavior Analyst board the long way: open the search, pick profession and license type, type a number, open the record, check status / type / issued / expires, then Ctrl+P into that provider’s folder.

This repo is that path, scripted — against the live GOALS Experience Cloud app (`goals.sos.ga.gov`), not the marketing site.

---

## What it does today

| Step | Behavior |
| --- | --- |
| Input | Reads license numbers from CSV or XLSX (`config.yaml`) |
| Browser | One headed Chrome window, persistent profile, one tab, sequential |
| Search | Profession Type → License Type → license number → Search |
| Match | Exactly one result, license number must match; never guesses |
| Detail | Scrapes first / middle / last, status, type, issued, expires |
| PDF | Prints the detail page as-is (primary source, not re-typeset) |
| Name | `Provider Name - GA - License Type - MM-DD-YYYY.pdf` |
| Log | One `run_log.csv` row per input license; `failures.csv` for re-runs |
| Resume | Re-run skips licenses that already have a good PDF; retries errors |

**Also in place**

- Salesforce shadow DOM and SLDS faux-comboboxes (`<button>`, not `<select>`)
- Invisible reCAPTCHA v3 on Search: trusted Playwright clicks, no token forge, no solver
- Cloudflare “verify you are human”: the bot **pauses**. You click the box **once** and wait. Reloading or mashing the box makes it come back
- Rate limit (~100/hour), delays between actions and providers, recaptcha circuit breaker
- Expired / lapsed / inactive licenses still get a PDF and a flag — that is a finding, not a crash
- Unit tests for filenames (middle name, `O'Brien`, `Smith-Dogbey`, blank expiry, long paths) and parsers

**Layout**

```
config.yaml              # paths, pacing, lookup mode
data/sample_input.csv    # two rows: a known license + a deliberate miss
src/                     # CLI, browser, search, detail, naming, logs
tests/                   # naming + parse + run-log tests
spike/                   # Phase 0 roster / deep-link experiment
output/                  # gitignored — PDFs, run_log.csv, failures.csv
```

**Run**

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
$env:PYTHONPATH = "src"
python -m pytest tests
python -m main --config config.yaml
```

Leave the Chrome window open. If Cloudflare asks, check the box **once**, then do nothing. The script continues when the search form appears.

Sample input includes `LBA000602` (Andrea Smith, expected PDF name  
`Andrea Smith - GA - Behavior Analyst - 08-31-2027.pdf`) and `LBA999999` (should log `NOT_FOUND`, no PDF).

---

## What is not done yet

Treat this as a working pipeline with live-site work still open — not a finished production drop.

1. **Live acceptance** — `LBA000602` PDF, invalid number `NOT_FOUND`, and a 25-license batch with zero unhandled exceptions have not been signed off on the live site. Dropdown selection and Cloudflare were still failing during the first runs.
2. **Phase 0 spike** — One board-wide search that yields detail tokens for everyone would make later steps unattended. That test has not succeeded. Until it does, keep `lookup_mode: per_license` in `config.yaml`. If the spike later proves tokens work, switch to `roster`.
3. **Cloudflare / recaptcha reliability** — Search is still gated. Headed Chrome and a human checkbox click are the compliant path. Do not add solvers, stealth packs, or token replay.
4. **PDD open questions** — Input file location, per-provider folder vs flat `output/`, filename date (`MM-DD-YYYY` vs `YYYY-MM-DD`), hyphen vs en dash, assistant/temporary license types in the feed, re-verify vs skip existing PDFs, volume/cadence. Defaults are in `config.yaml`; confirm with operations before a large run.
5. **PDF in headed Chrome** — Capture uses CDP print / Playwright PDF. Confirm files open and contain the on-screen text on the target Windows boxes.
6. **Operations wrap** — No scheduler, no GUI, no extra states or professions. A Task Scheduler / CI wrapper is out of scope here.
7. **Production data** — Point `input_file` and `output_root` at the real provider list and document folders. Do not commit those files.

---

## Hard rules (do not “fix” these)

- Do not bypass, solve, or forge CAPTCHA
- Do not call the Salesforce Aura API directly (`fwuid` rotates)
- Do not cache detail URL tokens across runs
- Do not write a PDF unless the scraped license number matches the request
- Do not run headless against Search (scores poorly)
- Do not parallelize tabs or processes against GOALS

---

## Config you will actually change

```yaml
input_file: "data/sample_input.csv"
input_column: "license_number"
output_root: "output"
on_existing_file: "skip"          # skip | version | overwrite
lookup_mode: "per_license"        # roster only after Phase 0 succeeds
search_click_mode: "auto"         # human = you click Search
headed: true
```

`--human-search-click` is optional and only for when auto Search is rejected. Default is hands-off form fill + Search click.

---

## Status

**v0.1 — usable locally, live site not fully green.**  
Push this, run against the sample file, then close the acceptance list above before you point it at the full roster.

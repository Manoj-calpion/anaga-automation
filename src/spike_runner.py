"""Phase 0 CLI: `python -m spike_runner` or `python run_lookup.py --spike`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml


async def run_spike(config: dict[str, Any]) -> int:
    root = Path.cwd()
    sys.path.insert(0, str(root / "spike"))
    from phase0_roster_test import run_spike as spike_run

    cfg_path = root / "spike" / "_spike_config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    try:
        payload = await spike_run(cfg_path)
    finally:
        cfg_path.unlink(missing_ok=True)
    out = root / "spike" / "phase0_result.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {out}")
    return 0 if payload.get("roster_tokens_usable") else 2


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 0 GA ABA roster/token spike")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--human-search-click", action="store_true")
    args = parser.parse_args(argv)
    with Path(args.config).open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if args.human_search_click:
        config["search_click_mode"] = "human"
    raise SystemExit(asyncio.run(run_spike(config)))


if __name__ == "__main__":
    main()

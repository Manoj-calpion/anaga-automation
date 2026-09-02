from __future__ import annotations

from pathlib import Path

from logging_ import RunWriters
from models import RunLogRow, StatusCode


def test_upsert_replaces_halted_row(tmp_path: Path):
    writers = RunWriters(tmp_path, "license_number")
    writers.append(
        RunLogRow(
            license_number="LBA000602",
            requested_at="t1",
            status_code=StatusCode.HALTED.value,
            error_detail="Cloudflare",
        ),
        {"license_number": "LBA000602"},
    )
    writers.append(
        RunLogRow(
            license_number="LBA000602",
            requested_at="t2",
            status_code=StatusCode.OK.value,
            provider_name="Andrea Smith",
            pdf_path=str(tmp_path / "a.pdf"),
        ),
        {"license_number": "LBA000602"},
    )
    assert writers.existing_row_count() == 1
    latest = writers.latest_by_license()["LBA000602"]
    assert latest["status_code"] == "OK"
    fail = (tmp_path / "failures.csv").read_text(encoding="utf-8")
    assert "LBA000602" not in fail or fail.strip() == ""


def test_halted_is_not_treated_as_complete(tmp_path: Path):
    writers = RunWriters(tmp_path, "license_number")
    writers.append(
        RunLogRow(
            license_number="LBA999999",
            requested_at="t1",
            status_code=StatusCode.HALTED.value,
            error_detail="Cloudflare",
        ),
        {"license_number": "LBA999999"},
    )
    assert writers.load_completed_ok_licenses() == {}
    assert writers.latest_by_license()["LBA999999"]["status_code"] == "HALTED"

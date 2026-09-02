"""Append-only run_log.csv and failures.csv writers."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from models import RunLogRow, StatusCode

RUN_LOG_FIELDS = [
    "license_number",
    "requested_at",
    "status_code",
    "provider_name",
    "license_type",
    "license_status",
    "issued_date",
    "expiration_date",
    "pdf_path",
    "error_detail",
]


class RunWriters:
    def __init__(self, output_root: Path, input_column: str):
        self.output_root = output_root
        self.input_column = input_column
        self.run_log_path = output_root / "run_log.csv"
        self.failures_path = output_root / "failures.csv"
        output_root.mkdir(parents=True, exist_ok=True)
        self._run_log_fields = list(RUN_LOG_FIELDS)
        self._fail_fields: list[str] | None = None
        self.counts: Counter[str] = Counter()
        self.rows_written = 0
        # license_number -> latest csv dict (for upsert / resume)
        self._log_rows: dict[str, dict[str, str]] = {}
        self._fail_payloads: dict[str, dict[str, str]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        if not self.run_log_path.exists():
            return
        with self.run_log_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                lic = (row.get("license_number") or "").strip()
                if lic:
                    self._log_rows[lic.upper()] = dict(row)

    def latest_by_license(self) -> dict[str, dict[str, str]]:
        return dict(self._log_rows)

    def load_completed_ok_licenses(self) -> dict[str, str]:
        """license_number -> pdf_path for prior OK rows whose PDF still exists."""
        done: dict[str, str] = {}
        if not self.run_log_path.exists():
            return done
        with self.run_log_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("status_code") not in {StatusCode.OK.value, StatusCode.NO_EXPIRY.value}:
                    continue
                lic = (row.get("license_number") or "").strip()
                pdf = (row.get("pdf_path") or "").strip()
                if lic and pdf and Path(pdf).exists():
                    done[lic] = pdf
        return done

    def existing_row_count(self) -> int:
        if not self.run_log_path.exists():
            return 0
        with self.run_log_path.open(newline="", encoding="utf-8") as fh:
            return max(0, sum(1 for _ in fh) - 1)

    def append(self, row: RunLogRow, original: dict[str, str] | None = None) -> None:
        """Insert or replace the row for this license so resume does not duplicate."""
        self.counts[row.status_code] += 1
        self.rows_written += 1
        data = {k: row.to_csv_dict().get(k, "") for k in self._run_log_fields}
        key = (row.license_number or "").strip().upper()
        if key:
            self._log_rows[key] = data
            self._rewrite_run_log()
        else:
            with self.run_log_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=self._run_log_fields, extrasaction="ignore")
                if self.run_log_path.stat().st_size == 0:
                    writer.writeheader()
                writer.writerow(data)

        code = StatusCode(row.status_code) if row.status_code in StatusCode._value2member_map_ else StatusCode.ERROR
        skip_fail = code in {StatusCode.OK, StatusCode.SKIPPED_EXISTS}
        if key and skip_fail:
            self._fail_payloads.pop(key, None)
            self._rewrite_failures()
        elif not skip_fail:
            self._record_failure(row, original or {})

    def _rewrite_run_log(self) -> None:
        tmp = self.run_log_path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self._run_log_fields, extrasaction="ignore")
            writer.writeheader()
            for data in self._log_rows.values():
                writer.writerow(data)
        tmp.replace(self.run_log_path)

    def _record_failure(self, row: RunLogRow, original: dict[str, str]) -> None:
        payload = dict(original)
        if self.input_column not in payload:
            payload[self.input_column] = row.license_number
        payload["status_code"] = row.status_code
        payload["error_detail"] = row.error_detail
        key = (row.license_number or "").strip().upper()
        if key:
            self._fail_payloads[key] = payload
        if self._fail_fields is None:
            keys = list(payload.keys())
            if self.input_column in keys:
                keys.remove(self.input_column)
                keys = [self.input_column] + keys
            self._fail_fields = keys
        for k in payload:
            if k not in (self._fail_fields or []):
                self._fail_fields.append(k)
        self._rewrite_failures()

    def _rewrite_failures(self) -> None:
        if not self._fail_payloads:
            if self.failures_path.exists():
                self.failures_path.write_text("", encoding="utf-8")
            return
        if self._fail_fields is None:
            self._fail_fields = [self.input_column, "status_code", "error_detail"]
        tmp = self.failures_path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self._fail_fields, extrasaction="ignore")
            writer.writeheader()
            for payload in self._fail_payloads.values():
                writer.writerow(payload)
        tmp.replace(self.failures_path)

    def print_summary(self, expected_rows: int | None = None) -> None:
        print("\n=== run summary ===")
        for code in sorted(self.counts):
            print(f"  {code}: {self.counts[code]}")
        print(f"  rows_written_this_process: {self.rows_written}")
        if expected_rows is not None:
            total = self.existing_row_count() if self.rows_written == 0 else None
            print(f"  input_rows: {expected_rows}")
            print(f"  run_log path: {self.run_log_path}")
        print(f"  failures path: {self.failures_path}")

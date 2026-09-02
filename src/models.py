from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StatusCode(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    MISMATCH = "MISMATCH"
    DETAIL_LOAD_FAILED = "DETAIL_LOAD_FAILED"
    NO_EXPIRY = "NO_EXPIRY"
    SKIPPED_EXISTS = "SKIPPED_EXISTS"
    ERROR = "ERROR"
    HALTED = "HALTED"


NON_OK_FOR_FAILURES = {
    StatusCode.NOT_FOUND,
    StatusCode.AMBIGUOUS,
    StatusCode.MISMATCH,
    StatusCode.DETAIL_LOAD_FAILED,
    StatusCode.NO_EXPIRY,
    StatusCode.ERROR,
    StatusCode.HALTED,
}

# Expired/lapsed still produce an OK capture; this is a finding flag, not a status_code.
INACTIVE_STATUSES = {"expired", "lapsed", "inactive"}


@dataclass
class SearchHit:
    license_number: str
    full_name: str = ""
    license_type: str = ""
    status: str = ""
    encrypted_license_id: str = ""
    select_button: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LicenseRecord:
    first_name: str = ""
    middle: str = ""
    last_name: str = ""
    address: str = ""
    license_number: str = ""
    profession: str = ""
    license_type: str = ""
    sub_type: str = ""
    obtained_by: str = ""
    status: str = ""
    issued: str = ""
    expires: str = ""
    last_renewal_date: str = ""

    @property
    def provider_name(self) -> str:
        from naming import provider_display_name

        return provider_display_name(self.first_name, self.middle, self.last_name)

    def as_log_fields(self) -> dict[str, str]:
        return {
            "provider_name": self.provider_name,
            "license_type": self.license_type,
            "license_status": self.status,
            "issued_date": self.issued,
            "expiration_date": self.expires,
        }


@dataclass
class RunLogRow:
    license_number: str
    requested_at: str
    status_code: str
    provider_name: str = ""
    license_type: str = ""
    license_status: str = ""
    issued_date: str = ""
    expiration_date: str = ""
    pdf_path: str = ""
    error_detail: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def to_csv_dict(self) -> dict[str, str]:
        base = {
            "license_number": self.license_number,
            "requested_at": self.requested_at,
            "status_code": self.status_code,
            "provider_name": self.provider_name,
            "license_type": self.license_type,
            "license_status": self.license_status,
            "issued_date": self.issued_date,
            "expiration_date": self.expiration_date,
            "pdf_path": self.pdf_path,
            "error_detail": self.error_detail,
        }
        base.update(self.extra)
        return base


@dataclass
class InputRow:
    license_number: str
    license_type: str
    source: dict[str, Any] = field(default_factory=dict)

    def original_columns(self) -> dict[str, str]:
        return {k: "" if v is None else str(v) for k, v in self.source.items()}


class RecaptchaCircuitOpen(RuntimeError):
    """Three consecutive V3 Recaptcha failed in apex errors."""


class CloudflareChallenge(RuntimeError):
    """Cloudflare interstitial detected; halt for a human."""


class CssErrorModal(RuntimeError):
    """Lightning 'Sorry to interrupt — CSS Error' modal."""

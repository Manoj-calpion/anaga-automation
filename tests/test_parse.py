from __future__ import annotations

import json
from pathlib import Path

from detail import parse_fields_from_tokens, record_from_fields
from search import parse_aura_search_payload

FIXTURES = Path(__file__).parent / "fixtures"


def test_aura_payload_parses_spec_row():
    row = json.loads((FIXTURES / "search_row_lba000602.json").read_text(encoding="utf-8"))
    envelope = {
        "actions": [
            {
                "state": "SUCCESS",
                "returnValue": {"returnValue": {"rows": [row], "totalRows": 1}},
            }
        ]
    }
    hits, total, recaptcha = parse_aura_search_payload(json.dumps(envelope))
    assert recaptcha is False
    assert total == 1
    assert hits[0].license_number == "LBA000602"
    assert hits[0].full_name == "Andrea Smith"
    assert hits[0].encrypted_license_id.startswith("Op50nkHk")


def test_recaptcha_error_payload():
    body = json.dumps({"state": "ERROR", "error": [{"message": "V3 Recaptcha failed in apex"}]})
    hits, total, recaptcha = parse_aura_search_payload(body)
    assert recaptcha is True
    assert hits == []


def test_detail_tokens_lba000602():
    lines = (FIXTURES / "detail_tokens_lba000602.txt").read_text(encoding="utf-8").splitlines()
    fields = parse_fields_from_tokens(lines)
    rec = record_from_fields(fields)
    assert rec.first_name == "Andrea"
    assert rec.middle == "-"
    assert rec.last_name == "Smith"
    assert rec.license_number == "LBA000602"
    assert rec.license_type == "Behavior Analyst"
    assert rec.status == "Active"
    assert rec.issued == "10/02/2025"
    assert rec.expires == "08/31/2027"
    assert rec.provider_name == "Andrea Smith"

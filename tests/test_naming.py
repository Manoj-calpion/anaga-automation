from __future__ import annotations

from pathlib import Path

from naming import (
    MAX_PATH,
    PathTooLongError,
    build_filename_stem,
    format_expiry_for_filename,
    provider_display_name,
    resolve_pdf_path,
    sanitize_filename_component,
)


def test_provider_name_middle_absent():
    assert provider_display_name("Andrea", "", "Smith") == "Andrea Smith"
    assert provider_display_name("Andrea", "-", "Smith") == "Andrea Smith"
    assert provider_display_name("Andrea", " ", "Smith") == "Andrea Smith"


def test_provider_name_middle_present():
    assert provider_display_name("Andrea", "Marie", "Smith") == "Andrea Marie Smith"


def test_apostrophe_in_surname():
    stem, missing = build_filename_stem(
        first_name="Siobhan",
        middle_name="",
        last_name="O'Brien",
        state_code="GA",
        license_type="Behavior Analyst",
        expires="08/31/2027",
        date_format="MM-DD-YYYY",
        separator=" - ",
    )
    assert "O'Brien" in stem
    assert missing is False
    assert stem == "Siobhan O'Brien - GA - Behavior Analyst - 08-31-2027"


def test_hyphenated_surname_smith_dogbey():
    stem, _ = build_filename_stem(
        first_name="Ama",
        middle_name="",
        last_name="Smith-Dogbey",
        state_code="GA",
        license_type="Behavior Analyst",
        expires="01/15/2028",
        date_format="MM-DD-YYYY",
        separator=" - ",
    )
    assert "Smith-Dogbey" in stem
    assert stem == "Ama Smith-Dogbey - GA - Behavior Analyst - 01-15-2028"


def test_blank_expiry_uses_unknown():
    formatted, missing = format_expiry_for_filename("-", "MM-DD-YYYY")
    assert formatted == "UNKNOWN"
    assert missing is True
    formatted, missing = format_expiry_for_filename("", "MM-DD-YYYY")
    assert formatted == "UNKNOWN"
    assert missing is True
    stem, missing = build_filename_stem(
        first_name="Andrea",
        middle_name="",
        last_name="Smith",
        state_code="GA",
        license_type="Behavior Analyst",
        expires="-",
        date_format="MM-DD-YYYY",
        separator=" - ",
    )
    assert stem.endswith("UNKNOWN")
    assert missing is True


def test_acceptance_example_andrea_smith(tmp_path: Path):
    path, missing, _ = resolve_pdf_path(
        tmp_path,
        first_name="Andrea",
        middle_name="",
        last_name="Smith",
        state_code="GA",
        license_type="Behavior Analyst",
        expires="08/31/2027",
        date_format="MM-DD-YYYY",
        separator=" - ",
        license_number="LBA000602",
    )
    assert path.name == "Andrea Smith - GA - Behavior Analyst - 08-31-2027.pdf"
    assert missing is False


def test_yyyy_mm_dd_config():
    formatted, missing = format_expiry_for_filename("08/31/2027", "YYYY-MM-DD")
    assert formatted == "2027-08-31"
    assert missing is False


def test_illegal_characters_stripped():
    assert ":" not in sanitize_filename_component('Smith: "MD"/OB')
    assert "/" not in sanitize_filename_component("a/b")
    stem, _ = build_filename_stem(
        first_name="Ann",
        middle_name="",
        last_name='O"Brien',
        state_code="GA",
        license_type="Behavior Analyst",
        expires="08/31/2027",
        date_format="MM-DD-YYYY",
        separator=" - ",
    )
    assert '"' not in stem


def test_max_path_truncation(tmp_path: Path):
    long_last = "Smith" + ("Verylongname" * 20)
    path, _, _ = resolve_pdf_path(
        tmp_path,
        first_name="Andrea",
        middle_name="Marie",
        last_name=long_last,
        state_code="GA",
        license_type="Behavior Analyst",
        expires="08/31/2027",
        date_format="MM-DD-YYYY",
        separator=" - ",
        license_number="LBA000602",
        max_path=MAX_PATH,
    )
    assert len(str(path.resolve())) <= MAX_PATH
    assert path.suffix == ".pdf"


def test_deep_output_root_raises(tmp_path: Path):
    deep = tmp_path / ("d" * 240)
    deep.mkdir()
    try:
        resolve_pdf_path(
            deep,
            first_name="Andrea",
            middle_name="",
            last_name="Smith",
            state_code="GA",
            license_type="Behavior Analyst",
            expires="08/31/2027",
            date_format="MM-DD-YYYY",
            separator=" - ",
            license_number="LBA000602",
            max_path=MAX_PATH,
        )
        raised = False
    except PathTooLongError:
        raised = True
    assert raised


def test_collision_appends_license_number(tmp_path: Path):
    first = tmp_path / "Andrea Smith - GA - Behavior Analyst - 08-31-2027.pdf"
    first.write_bytes(b"pdf")
    path, _, note = resolve_pdf_path(
        tmp_path,
        first_name="Andrea",
        middle_name="",
        last_name="Smith",
        state_code="GA",
        license_type="Behavior Analyst",
        expires="08/31/2027",
        date_format="MM-DD-YYYY",
        separator=" - ",
        license_number="LBA000602",
        on_existing_file="skip",
    )
    assert "LBA000602" in path.name
    assert "collision" in note

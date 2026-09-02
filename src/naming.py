"""Pure filename builders for Georgia ABA verification PDFs.

No I/O except pathlib math. Unit-test this module without a browser.
"""

from __future__ import annotations

import re
from pathlib import Path

ILLEGAL_FS_CHARS = re.compile(r'[<>:"/\\|?*]')
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Windows MAX_PATH is 260 including the trailing NUL.
MAX_PATH = 259

BLANK_MARKERS = {"", "-", "—", "–", "n/a", "na", "none", "null"}


class PathTooLongError(ValueError):
    """Even a truncated filename cannot fit under MAX_PATH."""


def is_blank(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().lower() in BLANK_MARKERS


def provider_display_name(first: str, middle: str, last: str) -> str:
    parts: list[str] = []
    for raw in (first, middle, last):
        if is_blank(raw):
            continue
        token = " ".join(str(raw).split())
        if token:
            parts.append(token)
    return " ".join(parts)


def sanitize_filename_component(text: str, replacement: str = " ") -> str:
    cleaned = CONTROL_CHARS.sub("", text)
    cleaned = ILLEGAL_FS_CHARS.sub(replacement, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(" .")
    return cleaned


def format_expiry_for_filename(expires: str | None, date_format: str) -> tuple[str, bool]:
    """Return (formatted_or_UNKNOWN, missing).

    `expires` is the detail-page value, typically MM/DD/YYYY. `-` or blank → UNKNOWN.
    """
    if is_blank(expires):
        return "UNKNOWN", True
    raw = expires.strip()
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if not match:
        safe = sanitize_filename_component(raw.replace("/", "-"))
        return (safe or "UNKNOWN"), is_blank(safe)
    month, day, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    fmt = date_format.strip().upper()
    if fmt == "YYYY-MM-DD":
        return f"{year:04d}-{month:02d}-{day:02d}", False
    if fmt == "MM-DD-YYYY":
        return f"{month:02d}-{day:02d}-{year:04d}", False
    raise ValueError(f"Unsupported date_format_in_filename: {date_format!r}")


def build_filename_stem(
    *,
    first_name: str,
    middle_name: str,
    last_name: str,
    state_code: str,
    license_type: str,
    expires: str | None,
    date_format: str,
    separator: str,
) -> tuple[str, bool]:
    """Return (stem_without_pdf, expiry_missing)."""
    name = sanitize_filename_component(provider_display_name(first_name, middle_name, last_name))
    state = sanitize_filename_component(state_code)
    lic_type = sanitize_filename_component(license_type)
    expiry, missing = format_expiry_for_filename(expires, date_format)
    expiry = sanitize_filename_component(expiry)
    parts = [p for p in (name, state, lic_type, expiry) if p]
    stem = separator.join(parts)
    stem = CONTROL_CHARS.sub("", stem)
    stem = ILLEGAL_FS_CHARS.sub(" ", stem)
    stem = re.sub(r" +", " ", stem).strip().rstrip(" .")
    return stem, missing


def _fit_under_max_path(directory: Path, stem: str, suffix: str, max_path: int) -> str:
    """Shrink the stem so directory / (stem + suffix) fits in max_path chars."""
    directory = directory.resolve()
    extra = len(suffix)
    # +1 for the path separator between dir and file
    budget = max_path - len(str(directory)) - 1 - extra
    if budget < 8:
        raise PathTooLongError(
            f"output_root is too deep for a safe filename: {directory}"
        )
    if len(stem) <= budget:
        return stem
    truncated = stem[:budget].rstrip(" .-")
    if not truncated:
        raise PathTooLongError("filename budget exhausted after truncation")
    return truncated


def resolve_pdf_path(
    output_root: Path,
    *,
    first_name: str,
    middle_name: str,
    last_name: str,
    state_code: str,
    license_type: str,
    expires: str | None,
    date_format: str,
    separator: str,
    license_number: str,
    output_layout: str = "flat",
    provider_folder: str | None = None,
    on_existing_file: str = "skip",
    occupied: set[str] | None = None,
    max_path: int = MAX_PATH,
) -> tuple[Path, bool, str]:
    """Build the destination PDF path.

    Returns (path, expiry_missing, collision_note).
    Does not write files. `occupied` is a set of resolved path strings already
    claimed this run (and may include existing files on disk).
    """
    stem, expiry_missing = build_filename_stem(
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        state_code=state_code,
        license_type=license_type,
        expires=expires,
        date_format=date_format,
        separator=separator,
    )
    if output_layout == "per_provider_folder":
        folder_name = sanitize_filename_component(
            provider_folder or provider_display_name(first_name, middle_name, last_name)
            or license_number
        )
        directory = output_root / folder_name
    else:
        directory = output_root

    occupied = occupied if occupied is not None else set()
    suffix = ".pdf"
    stem = _fit_under_max_path(directory, stem, suffix, max_path)
    candidate = directory / f"{stem}{suffix}"
    note = ""

    def claimed(path: Path) -> bool:
        return str(path) in occupied or path.exists()

    if claimed(candidate):
        with_lic_stem = _fit_under_max_path(
            directory, f"{stem}{separator}{license_number}", suffix, max_path
        )
        with_lic = directory / f"{with_lic_stem}{suffix}"
        if on_existing_file == "version":
            version = 2
            while claimed(with_lic if version == 2 and claimed(candidate) else candidate):
                version_stem = _fit_under_max_path(
                    directory, f"{stem}{separator}{license_number}{separator}v{version}", suffix, max_path
                )
                with_lic = directory / f"{version_stem}{suffix}"
                if not claimed(with_lic):
                    break
                version += 1
                if version > 999:
                    raise PathTooLongError("exhausted version numbers")
            candidate = with_lic
            note = "versioned"
        elif on_existing_file == "overwrite":
            if claimed(candidate) and license_number not in candidate.name:
                candidate = with_lic
                note = "collision_appended_license"
        else:
            # skip caller short-circuits before save; still return deterministic path
            if license_number not in candidate.name:
                candidate = with_lic
                note = "collision_appended_license"

    if len(str(candidate.resolve())) > max_path:
        raise PathTooLongError(str(candidate))

    return candidate, expiry_missing, note

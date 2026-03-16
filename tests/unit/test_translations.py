"""PLAT-763: Verify strings.json is English-only and key parity with translations."""

import json
import re
from pathlib import Path

COMPONENT_DIR = Path(__file__).parent.parent.parent / "custom_components" / "tuya_cloudless"
STRINGS_JSON = COMPONENT_DIR / "strings.json"
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"
SV_JSON = TRANSLATIONS_DIR / "sv.json"
EN_JSON = TRANSLATIONS_DIR / "en.json"

SWEDISH_CHARS_RE = re.compile(r"[åäöÅÄÖ]")


def _flatten_keys(obj: dict, prefix: str = "") -> set[str]:
    """Recursively collect all leaf key paths from a nested dict."""
    keys: set[str] = set()
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= _flatten_keys(v, path)
        else:
            keys.add(path)
    return keys


def _flatten_values(obj: dict) -> list[str]:
    """Collect all string leaf values from a nested dict."""
    values: list[str] = []
    for v in obj.values():
        if isinstance(v, dict):
            values.extend(_flatten_values(v))
        elif isinstance(v, str):
            values.append(v)
    return values


def test_strings_json_exists() -> None:
    assert STRINGS_JSON.exists(), "strings.json is missing"


def test_strings_json_no_swedish_chars() -> None:
    """AC3: strings.json must not contain å, ä, ö."""
    data = json.loads(STRINGS_JSON.read_text(encoding="utf-8"))
    for value in _flatten_values(data):
        match = SWEDISH_CHARS_RE.search(value)
        assert match is None, (
            f"Swedish character '{match.group()}' found in strings.json value: {value!r}"
        )


def test_sv_json_exists() -> None:
    """AC2: translations/sv.json must exist."""
    assert SV_JSON.exists(), "translations/sv.json is missing"


def test_en_json_exists() -> None:
    """translations/en.json must exist."""
    assert EN_JSON.exists(), "translations/en.json is missing"


def test_strings_and_sv_key_parity() -> None:
    """AC5: Every key in strings.json must exist in translations/sv.json and vice versa."""
    strings_data = json.loads(STRINGS_JSON.read_text(encoding="utf-8"))
    sv_data = json.loads(SV_JSON.read_text(encoding="utf-8"))

    strings_keys = _flatten_keys(strings_data)
    sv_keys = _flatten_keys(sv_data)

    missing_in_sv = strings_keys - sv_keys
    extra_in_sv = sv_keys - strings_keys

    assert not missing_in_sv, (
        f"Keys in strings.json missing from translations/sv.json: {sorted(missing_in_sv)}"
    )
    assert not extra_in_sv, (
        f"Keys in translations/sv.json not present in strings.json: {sorted(extra_in_sv)}"
    )


def test_strings_and_en_json_identical_keys() -> None:
    """translations/en.json must have the same keys as strings.json."""
    strings_data = json.loads(STRINGS_JSON.read_text(encoding="utf-8"))
    en_data = json.loads(EN_JSON.read_text(encoding="utf-8"))

    strings_keys = _flatten_keys(strings_data)
    en_keys = _flatten_keys(en_data)

    missing_in_en = strings_keys - en_keys
    extra_in_en = en_keys - strings_keys

    assert not missing_in_en, (
        f"Keys in strings.json missing from translations/en.json: {sorted(missing_in_en)}"
    )
    assert not extra_in_en, (
        f"Keys in translations/en.json not present in strings.json: {sorted(extra_in_en)}"
    )


def test_sv_json_has_swedish_chars() -> None:
    """Sanity check: translations/sv.json should actually contain Swedish characters."""
    sv_data = json.loads(SV_JSON.read_text(encoding="utf-8"))
    all_values = _flatten_values(sv_data)
    has_swedish = any(SWEDISH_CHARS_RE.search(v) for v in all_values)
    assert has_swedish, "translations/sv.json contains no Swedish characters — might be wrong file"

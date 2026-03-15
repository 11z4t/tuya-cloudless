"""Unit tests for the device profile system."""

from __future__ import annotations

from pathlib import Path

import pytest
from tuya_cloudless.profiles import (
    DPSpec,
    EntitySpec,
    find_profile,
    find_profile_by_product_key,
    init_profiles,
    list_profiles,
    load_profile,
    load_profiles_from_dir,
)

# ── DPSpec ─────────────────────────────────────────────────────────────────────


def test_dp_spec_defaults() -> None:
    """DPSpec should use sensible defaults for optional fields."""
    spec = DPSpec(id="1", type="bool")
    assert spec.id == "1"
    assert spec.type == "bool"
    assert spec.scale == 1.0
    assert spec.min_raw is None
    assert spec.max_raw is None


def test_dp_spec_with_scale() -> None:
    """DPSpec should store custom scale and range values."""
    spec = DPSpec(id="19", type="int", scale=0.1, min_raw=0, max_raw=10000)
    assert spec.scale == pytest.approx(0.1)
    assert spec.min_raw == 0
    assert spec.max_raw == 10000


def test_dp_spec_is_frozen() -> None:
    """DPSpec instances must be immutable (frozen dataclass)."""
    from dataclasses import FrozenInstanceError

    spec = DPSpec(id="1", type="bool")
    with pytest.raises(FrozenInstanceError):
        spec.id = "2"  # type: ignore[misc]


# ── EntitySpec ─────────────────────────────────────────────────────────────────


def test_entity_spec_switch() -> None:
    """EntitySpec for a switch should have dp_power set."""
    spec = EntitySpec(
        platform="switch",
        name="main_switch",
        dp_power=DPSpec(id="1", type="bool"),
    )
    assert spec.platform == "switch"
    assert spec.dp_power is not None
    assert spec.dp_power.id == "1"
    assert spec.dp_value is None


def test_entity_spec_sensor_with_scale() -> None:
    """EntitySpec for a sensor should carry unit and device_class."""
    spec = EntitySpec(
        platform="sensor",
        name="power_consumption",
        dp_value=DPSpec(id="19", type="int", scale=0.1),
        unit="W",
        device_class="power",
        state_class="measurement",
    )
    assert spec.dp_value is not None
    assert spec.dp_value.scale == pytest.approx(0.1)
    assert spec.unit == "W"
    assert spec.device_class == "power"


def test_entity_spec_is_frozen() -> None:
    """EntitySpec must be immutable."""
    from dataclasses import FrozenInstanceError

    spec = EntitySpec(platform="switch", name="main")
    with pytest.raises(FrozenInstanceError):
        spec.platform = "sensor"  # type: ignore[misc]


# ── YAML loading ───────────────────────────────────────────────────────────────


@pytest.fixture()
def profiles_dir(tmp_path: Path) -> Path:
    """Return a temp directory with sample YAML profile files."""
    (tmp_path / "generic_switch.yaml").write_text(
        """
name: "Generic Switch"
model: "*"
entities:
  - platform: switch
    name: main_switch
    dp_power: {id: "1", type: bool}
""",
        encoding="utf-8",
    )
    (tmp_path / "smart_plug.yaml").write_text(
        """
name: "Smart Plug"
model: "sp*"
entities:
  - platform: switch
    name: main_switch
    dp_power: {id: "1", type: bool}
  - platform: sensor
    name: power_consumption
    dp_value: {id: "19", type: int, scale: 0.1}
    device_class: power
    unit: W
    state_class: measurement
  - platform: binary_sensor
    name: overload
    dp_power: {id: "26", type: bool}
    device_class: problem
""",
        encoding="utf-8",
    )
    return tmp_path


def test_load_profile_switch(profiles_dir: Path) -> None:
    """load_profile should parse a switch profile correctly."""
    profile = load_profile(profiles_dir / "generic_switch.yaml")
    assert profile.name == "Generic Switch"
    assert profile.model == "*"
    assert len(profile.entities) == 1
    entity = profile.entities[0]
    assert entity.platform == "switch"
    assert entity.dp_power is not None
    assert entity.dp_power.id == "1"


def test_load_profile_smart_plug(profiles_dir: Path) -> None:
    """load_profile should parse a multi-entity smart plug profile."""
    profile = load_profile(profiles_dir / "smart_plug.yaml")
    assert profile.name == "Smart Plug"
    assert len(profile.entities) == 3
    platforms = [e.platform for e in profile.entities]
    assert "switch" in platforms
    assert "sensor" in platforms
    assert "binary_sensor" in platforms


def test_load_profiles_from_dir(profiles_dir: Path) -> None:
    """load_profiles_from_dir should load all YAML files."""
    profiles = load_profiles_from_dir(profiles_dir)
    assert len(profiles) == 2
    names = {p.name for p in profiles}
    assert "Generic Switch" in names
    assert "Smart Plug" in names


def test_load_profiles_skips_invalid(tmp_path: Path) -> None:
    """load_profiles_from_dir should skip invalid YAML files without raising."""
    (tmp_path / "bad.yaml").write_text("not_a_profile: true\n", encoding="utf-8")
    (tmp_path / "good.yaml").write_text(
        'name: "Good"\nmodel: "*"\nentities: []\n', encoding="utf-8"
    )
    profiles = load_profiles_from_dir(tmp_path)
    assert len(profiles) == 1
    assert profiles[0].name == "Good"


def test_load_profiles_empty_dir(tmp_path: Path) -> None:
    """load_profiles_from_dir should return an empty list for an empty dir."""
    profiles = load_profiles_from_dir(tmp_path)
    assert profiles == []


def test_load_profiles_missing_dir(tmp_path: Path) -> None:
    """load_profiles_from_dir should return empty list if dir doesn't exist."""
    profiles = load_profiles_from_dir(tmp_path / "nonexistent")
    assert profiles == []


# ── Registry ───────────────────────────────────────────────────────────────────


def test_init_and_list_profiles(profiles_dir: Path) -> None:
    """init_profiles + list_profiles should expose loaded profiles."""
    init_profiles(profiles_dir)
    profiles = list_profiles()
    assert len(profiles) >= 2
    names = {p.name for p in profiles}
    assert "Generic Switch" in names
    assert "Smart Plug" in names


def test_find_profile_by_name(profiles_dir: Path) -> None:
    """find_profile should return a profile by exact name."""
    init_profiles(profiles_dir)
    profile = find_profile("Generic Switch")
    assert profile is not None
    assert profile.name == "Generic Switch"


def test_find_profile_not_found(profiles_dir: Path) -> None:
    """find_profile should return None for unknown names."""
    init_profiles(profiles_dir)
    assert find_profile("No Such Profile") is None


def test_find_profile_by_product_key_glob(profiles_dir: Path) -> None:
    """find_profile_by_product_key should match glob patterns."""
    init_profiles(profiles_dir)
    # "sp*" should match "sp_v2_abc"
    profile = find_profile_by_product_key("sp_v2_abc")
    assert profile is not None
    assert profile.name == "Smart Plug"


def test_find_profile_by_product_key_wildcard_fallback(profiles_dir: Path) -> None:
    """find_profile_by_product_key should fall back to '*' profile."""
    init_profiles(profiles_dir)
    # No specific pattern matches "xyz" — should fall back to Generic Switch (model="*")
    profile = find_profile_by_product_key("xyz_unknown")
    assert profile is not None
    assert profile.name == "Generic Switch"


# ── Real profiles integration ──────────────────────────────────────────────────


def test_real_profiles_load() -> None:
    """The profiles/ directory in the repo should contain at least 4 profiles."""

    repo_root = Path(__file__).resolve().parent.parent.parent
    real_profiles_dir = repo_root / "profiles"

    if not real_profiles_dir.is_dir():
        pytest.skip("profiles/ directory not found — run from repo root")

    profiles = load_profiles_from_dir(real_profiles_dir)
    assert len(profiles) >= 4, f"Expected ≥4 profiles, got {len(profiles)}"

    names = {p.name for p in profiles}
    assert "Generic Switch" in names
    assert "Generic Light" in names
    assert "Smart Plug" in names
    assert "Roller Blind" in names

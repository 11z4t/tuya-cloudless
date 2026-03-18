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


# ── _get_spec_dp_ids ───────────────────────────────────────────────────────────


def test_get_spec_dp_ids_switch() -> None:
    """_get_spec_dp_ids returns the dp_power id for a switch spec."""
    from tuya_cloudless.profiles import _get_spec_dp_ids

    spec = EntitySpec(
        platform="switch",
        name="main",
        dp_power=DPSpec(id="1", type="bool"),
    )
    ids = _get_spec_dp_ids(spec)
    assert ids == {"1"}


def test_get_spec_dp_ids_multiple_dps() -> None:
    """_get_spec_dp_ids collects all dp_* DPSpec IDs."""
    from tuya_cloudless.profiles import _get_spec_dp_ids

    spec = EntitySpec(
        platform="climate",
        name="thermo",
        dp_power=DPSpec(id="1", type="bool"),
        dp_mode=DPSpec(id="2", type="enum"),
        dp_temp_set=DPSpec(id="3", type="int"),
        dp_temp_current=DPSpec(id="4", type="int"),
    )
    ids = _get_spec_dp_ids(spec)
    assert ids == {"1", "2", "3", "4"}


def test_get_spec_dp_ids_empty_spec() -> None:
    """_get_spec_dp_ids returns empty set when no dp_* fields are set."""
    from tuya_cloudless.profiles import _get_spec_dp_ids

    spec = EntitySpec(platform="sensor", name="temp")
    ids = _get_spec_dp_ids(spec)
    assert ids == set()


def test_get_spec_dp_ids_light_spec() -> None:
    """_get_spec_dp_ids picks up brightness and color_temp DPSpec fields."""
    from tuya_cloudless.profiles import _get_spec_dp_ids

    spec = EntitySpec(
        platform="light",
        name="lamp",
        dp_power=DPSpec(id="20", type="bool"),
        dp_brightness=DPSpec(id="22", type="int"),
        dp_color_temp=DPSpec(id="23", type="int"),
    )
    ids = _get_spec_dp_ids(spec)
    assert ids == {"20", "22", "23"}


# ── _detect_profile_from_dps_core ─────────────────────────────────────────────


def test_detect_profile_from_dps_core_empty_dp_ids() -> None:
    """Returns None when dp_ids is empty."""
    from tuya_cloudless.profiles import DeviceProfile, _detect_profile_from_dps_core

    profiles = [DeviceProfile(name="Switch", model="sw*", entities=[])]
    result = _detect_profile_from_dps_core(set(), profiles)
    assert result is None


def test_detect_profile_from_dps_core_empty_profiles() -> None:
    """Returns None when profiles list is empty."""
    from tuya_cloudless.profiles import _detect_profile_from_dps_core

    result = _detect_profile_from_dps_core({"1", "2"}, [])
    assert result is None


def test_detect_profile_from_dps_core_best_match() -> None:
    """Selects the profile with the highest overlap fraction."""
    from tuya_cloudless.profiles import (
        DeviceProfile,
        DPSpec,
        EntitySpec,
        _detect_profile_from_dps_core,
    )

    # Profile A expects DPs 1, 2 — 2/2 = 100% overlap with observed {1,2,3}
    prof_a = DeviceProfile(
        name="ProfileA",
        model="pa*",
        entities=[
            EntitySpec(
                platform="switch",
                name="main",
                dp_power=DPSpec(id="1", type="bool"),
                dp_value=DPSpec(id="2", type="int"),
            )
        ],
    )
    # Profile B expects DPs 1, 2, 4, 5 — 2/4 = 50% overlap
    prof_b = DeviceProfile(
        name="ProfileB",
        model="pb*",
        entities=[
            EntitySpec(
                platform="sensor",
                name="s",
                dp_power=DPSpec(id="1", type="bool"),
                dp_value=DPSpec(id="2", type="int"),
                dp_brightness=DPSpec(id="4", type="int"),
                dp_color_temp=DPSpec(id="5", type="int"),
            )
        ],
    )

    result = _detect_profile_from_dps_core({"1", "2", "3"}, [prof_a, prof_b])
    assert result is not None
    assert result.name == "ProfileA"


def test_detect_profile_from_dps_core_wildcard_fallback() -> None:
    """Falls back to wildcard '*' profile when no specific profile has any overlap."""
    from tuya_cloudless.profiles import (
        DeviceProfile,
        DPSpec,
        EntitySpec,
        _detect_profile_from_dps_core,
    )

    wildcard = DeviceProfile(name="Generic", model="*", entities=[])
    specific = DeviceProfile(
        name="Specific",
        model="sp*",
        entities=[
            EntitySpec(
                platform="switch",
                name="main",
                dp_power=DPSpec(id="99", type="bool"),  # no overlap with {"1","2"}
            )
        ],
    )

    result = _detect_profile_from_dps_core({"1", "2"}, [wildcard, specific])
    assert result is not None
    assert result.name == "Generic"


def test_detect_profile_from_dps_core_no_overlap_no_wildcard() -> None:
    """Returns None when no profile overlaps and there's no wildcard fallback."""
    from tuya_cloudless.profiles import (
        DeviceProfile,
        DPSpec,
        EntitySpec,
        _detect_profile_from_dps_core,
    )

    specific = DeviceProfile(
        name="Specific",
        model="sp*",
        entities=[
            EntitySpec(
                platform="switch",
                name="main",
                dp_power=DPSpec(id="99", type="bool"),
            )
        ],
    )

    result = _detect_profile_from_dps_core({"1", "2"}, [specific])
    # No wildcard fallback, no overlap — returns None
    assert result is None


def test_detect_profile_from_dps_core_profile_with_empty_expected() -> None:
    """Profiles with no dp_* specs are skipped in scoring."""
    from tuya_cloudless.profiles import DeviceProfile, EntitySpec, _detect_profile_from_dps_core

    # Profile with entity but no DPSpec fields — expected will be empty → skipped
    empty_dp_profile = DeviceProfile(
        name="EmptyDP",
        model="em*",
        entities=[EntitySpec(platform="sensor", name="temp")],  # no dp_* set
    )
    wildcard = DeviceProfile(name="Fallback", model="*", entities=[])

    result = _detect_profile_from_dps_core({"1"}, [empty_dp_profile, wildcard])
    assert result is not None
    assert result.name == "Fallback"


# ── ProfileRegistry.detect_profile_from_dps ───────────────────────────────────


def test_registry_detect_profile_from_dps(profiles_dir: Path) -> None:
    """ProfileRegistry.detect_profile_from_dps finds best match from loaded profiles."""
    from tuya_cloudless.profiles import ProfileRegistry

    registry = ProfileRegistry()
    registry.init(profiles_dir)

    # Smart Plug has DP 1 (switch) + DP 19 (sensor) + DP 26 (binary_sensor)
    result = registry.detect_profile_from_dps({"1", "19", "26"})
    assert result is not None
    assert result.name == "Smart Plug"


def test_registry_detect_profile_from_dps_returns_none_empty() -> None:
    """ProfileRegistry.detect_profile_from_dps returns None for empty dp_ids."""
    from tuya_cloudless.profiles import ProfileRegistry

    registry = ProfileRegistry()
    result = registry.detect_profile_from_dps(set())
    assert result is None


# ── ProfileRegistry.find_profile_by_product_key ───────────────────────────────


def test_registry_find_profile_by_product_key(profiles_dir: Path) -> None:
    """ProfileRegistry.find_profile_by_product_key matches glob patterns."""
    from tuya_cloudless.profiles import ProfileRegistry

    registry = ProfileRegistry()
    registry.init(profiles_dir)

    result = registry.find_profile_by_product_key("sp_v3_xyz")
    assert result is not None
    assert result.name == "Smart Plug"


def test_registry_find_profile_by_product_key_wildcard_fallback(profiles_dir: Path) -> None:
    """ProfileRegistry.find_profile_by_product_key falls back to '*' profile."""
    from tuya_cloudless.profiles import ProfileRegistry

    registry = ProfileRegistry()
    registry.init(profiles_dir)

    result = registry.find_profile_by_product_key("totally_unknown_key")
    assert result is not None
    assert result.name == "Generic Switch"


# ── Module-level compat detect_profile_from_dps ───────────────────────────────


def test_compat_detect_profile_from_dps(profiles_dir: Path) -> None:
    """Module-level detect_profile_from_dps uses the compat registry."""
    from tuya_cloudless.profiles import detect_profile_from_dps

    init_profiles(profiles_dir)
    # Smart Plug has DP 19 which no other profile has
    result = detect_profile_from_dps({"1", "19", "26"})
    assert result is not None
    assert result.name == "Smart Plug"


def test_compat_detect_profile_from_dps_empty() -> None:
    """Module-level detect_profile_from_dps returns None for empty dp_ids."""
    from tuya_cloudless.profiles import detect_profile_from_dps

    result = detect_profile_from_dps(set())
    assert result is None


# ── ProfileRegistry.__len__ ───────────────────────────────────────────────────


def test_registry_len(profiles_dir: Path) -> None:
    """ProfileRegistry.__len__ returns the number of loaded profiles."""
    from tuya_cloudless.profiles import ProfileRegistry

    registry = ProfileRegistry()
    assert len(registry) == 0
    registry.init(profiles_dir)
    assert len(registry) == 2

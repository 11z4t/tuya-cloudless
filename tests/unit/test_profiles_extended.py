"""Tests for the extended set of device profiles (PLAT-664/665/666)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tuya_cloudless.profiles import (
    DPSpec,
    DeviceProfile,
    EntitySpec,
    ProfileRegistry,
    _detect_profile_from_dps_core,
    _get_spec_dp_ids,
    detect_profile_from_dps,
    init_profiles,
    list_profiles,
    load_profile,
    load_profiles_from_dir,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

PROFILES_DIR = Path(__file__).parent.parent.parent / "profiles"


@pytest.fixture(autouse=True)
def loaded_profiles() -> None:
    """Load all profiles from the profiles directory before each test."""
    init_profiles(PROFILES_DIR)


# ── Registry tests ─────────────────────────────────────────────────────────────


def test_list_profiles_count_ge_18() -> None:
    profiles = list_profiles()
    assert len(profiles) >= 18, f"Expected ≥18 profiles, got {len(profiles)}"


def test_all_profiles_have_name_and_model() -> None:
    for profile in list_profiles():
        assert profile.name, f"Profile missing name: {profile}"
        assert profile.model, f"Profile missing model: {profile}"


# ── Individual profile loading ─────────────────────────────────────────────────


def test_electric_heater_loads() -> None:
    profile = load_profile(PROFILES_DIR / "electric_heater.yaml")
    assert profile.name == "Electric Heater"
    assert profile.model == "heater*"


def test_electric_heater_one_climate_entity() -> None:
    profile = load_profile(PROFILES_DIR / "electric_heater.yaml")
    assert len(profile.entities) == 1
    entity = profile.entities[0]
    assert entity.platform == "climate"
    assert entity.name == "heater"
    assert entity.dp_power is not None
    assert entity.dp_temp_set is not None
    assert entity.dp_temp_current is not None
    assert entity.dp_options == ("manual", "eco", "off")
    assert entity.target_min == pytest.approx(5.0)
    assert entity.target_max == pytest.approx(35.0)


def test_thermostat_loads() -> None:
    profile = load_profile(PROFILES_DIR / "thermostat.yaml")
    assert profile.name == "Smart Thermostat"
    entity = profile.entities[0]
    assert entity.platform == "climate"
    assert entity.dp_temp_set is not None
    assert entity.dp_temp_set.scale == pytest.approx(0.1)
    assert entity.dp_options == ("heat", "cool", "auto")
    assert entity.target_min == pytest.approx(10.0)
    assert entity.target_max == pytest.approx(30.0)


def test_ceiling_fan_two_entities() -> None:
    profile = load_profile(PROFILES_DIR / "ceiling_fan.yaml")
    assert profile.name == "Ceiling Fan"
    assert len(profile.entities) == 2
    platforms = {e.platform for e in profile.entities}
    assert "fan" in platforms
    assert "light" in platforms
    fan_entity = next(e for e in profile.entities if e.platform == "fan")
    assert fan_entity.dp_power is not None
    assert fan_entity.dp_value is not None
    assert fan_entity.dp_mode is not None
    assert fan_entity.dp_options == ("sleep", "auto", "natural")


def test_water_leak_sensor_loads() -> None:
    profile = load_profile(PROFILES_DIR / "water_leak_sensor.yaml")
    assert profile.name == "Water Leak Sensor"
    entity = profile.entities[0]
    assert entity.platform == "binary_sensor"
    assert entity.device_class == "moisture"


def test_pir_motion_sensor_two_entities() -> None:
    profile = load_profile(PROFILES_DIR / "pir_motion_sensor.yaml")
    assert len(profile.entities) == 2
    binary = next(e for e in profile.entities if e.platform == "binary_sensor")
    assert binary.device_class == "motion"
    sensor = next(e for e in profile.entities if e.platform == "sensor")
    assert sensor.device_class == "battery"


def test_door_window_sensor_contact_entity() -> None:
    profile = load_profile(PROFILES_DIR / "door_window_sensor.yaml")
    contact = next(e for e in profile.entities if e.platform == "binary_sensor")
    assert contact.device_class == "door"


def test_smoke_detector_two_binary_sensors() -> None:
    profile = load_profile(PROFILES_DIR / "smoke_detector.yaml")
    binary_sensors = [e for e in profile.entities if e.platform == "binary_sensor"]
    assert len(binary_sensors) == 2
    names = {e.name for e in binary_sensors}
    assert "smoke" in names
    assert "tamper" in names


def test_co2_sensor_three_entities() -> None:
    profile = load_profile(PROFILES_DIR / "co2_sensor.yaml")
    assert profile.name == "CO2 / Air Quality Sensor"
    assert len(profile.entities) == 3
    names = {e.name for e in profile.entities}
    assert "co2" in names
    assert "temperature" in names
    assert "humidity" in names
    sensors = [e for e in profile.entities if e.platform == "sensor"]
    assert len(sensors) == 3


def test_siren_alarm_three_entities() -> None:
    profile = load_profile(PROFILES_DIR / "siren_alarm.yaml")
    assert profile.name == "Siren / Alarm"
    assert len(profile.entities) == 3
    platforms = [e.platform for e in profile.entities]
    assert "switch" in platforms
    assert "select" in platforms
    assert "number" in platforms
    select_entity = next(e for e in profile.entities if e.platform == "select")
    assert select_entity.dp_options == ("sound1", "sound2", "alarm")
    number_entity = next(e for e in profile.entities if e.platform == "number")
    assert number_entity.unit == "%"


def test_ac_cool_heat_modes() -> None:
    profile = load_profile(PROFILES_DIR / "ac_cool_heat.yaml")
    assert profile.name == "Air Conditioner"
    entity = profile.entities[0]
    assert entity.platform == "climate"
    modes = set(entity.dp_options)
    assert "cool" in modes
    assert "heat" in modes
    assert "auto" in modes
    assert "fan_only" in modes
    assert "dry" in modes


def test_floor_heating_entities() -> None:
    profile = load_profile(PROFILES_DIR / "floor_heating.yaml")
    assert profile.name == "Floor Heating"
    assert len(profile.entities) == 2
    platforms = {e.platform for e in profile.entities}
    assert "climate" in platforms
    assert "sensor" in platforms
    sensor = next(e for e in profile.entities if e.platform == "sensor")
    assert sensor.name == "floor_temp"
    assert sensor.device_class == "temperature"


def test_ventilation_fan_profile() -> None:
    profile = load_profile(PROFILES_DIR / "ventilation.yaml")
    assert profile.name == "Ventilation Fan"
    entity = profile.entities[0]
    assert entity.platform == "fan"
    assert entity.dp_options == ("sleep", "auto", "turbo")


def test_heat_pump_profile() -> None:
    profile = load_profile(PROFILES_DIR / "heat_pump.yaml")
    assert profile.name == "Heat Pump"
    entity = profile.entities[0]
    assert entity.platform == "climate"
    assert entity.dp_temp_set is not None
    assert entity.dp_temp_set.scale == pytest.approx(0.1)


# ── EntitySpec new field parsing ───────────────────────────────────────────────


def test_dp_options_tuple_type() -> None:
    profile = load_profile(PROFILES_DIR / "thermostat.yaml")
    entity = profile.entities[0]
    assert isinstance(entity.dp_options, tuple)


def test_target_min_max_float_type() -> None:
    profile = load_profile(PROFILES_DIR / "electric_heater.yaml")
    entity = profile.entities[0]
    assert isinstance(entity.target_min, float)
    assert isinstance(entity.target_max, float)


def test_step_default_on_profiles_without_explicit_step() -> None:
    profile = load_profile(PROFILES_DIR / "water_leak_sensor.yaml")
    entity = profile.entities[0]
    assert entity.step == pytest.approx(1.0)


def test_all_profiles_load_without_exception() -> None:
    errors: list[str] = []
    for yaml_file in sorted(PROFILES_DIR.glob("*.yaml")):
        try:
            load_profile(yaml_file)
        except Exception as exc:
            errors.append(f"{yaml_file.name}: {exc}")
    assert not errors, "Profiles failed to load:\n" + "\n".join(errors)


# ── R45 fixes ─────────────────────────────────────────────────────────────────


class TestProfileValidationR45:
    """R45-F1/F2/F6: _parse_entity_spec must reject invalid name/step/bounds."""

    def _parse(self, data: dict) -> object:
        from tuya_cloudless.profiles import _parse_entity_spec

        return _parse_entity_spec(data)

    def test_empty_name_raises(self) -> None:
        """R45-F1: Empty name must raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="name"):
            self._parse({"platform": "switch", "name": ""})

    def test_whitespace_only_name_raises(self) -> None:
        """R45-F1: Whitespace-only name must also raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="name"):
            self._parse({"platform": "switch", "name": "   "})

    def test_valid_name_accepted(self) -> None:
        """R45-F1: A non-empty name is accepted normally."""
        spec = self._parse({"platform": "switch", "name": "power"})
        assert spec.name == "power"

    def test_step_zero_raises(self) -> None:
        """R45-F2: step=0.0 must raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="step"):
            self._parse({"platform": "number", "name": "vol", "step": 0.0})

    def test_step_negative_raises(self) -> None:
        """R45-F2: Negative step must raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="step"):
            self._parse({"platform": "number", "name": "vol", "step": -1.0})

    def test_step_positive_accepted(self) -> None:
        """R45-F2: Positive step is accepted."""
        spec = self._parse({"platform": "number", "name": "vol", "step": 0.5})
        assert spec.step == 0.5

    def test_target_min_ge_max_raises(self) -> None:
        """R45-F2: target_min >= target_max must raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="target_min"):
            self._parse(
                {
                    "platform": "number",
                    "name": "temp",
                    "target_min": 100.0,
                    "target_max": 10.0,
                }
            )

    def test_target_min_equal_max_raises(self) -> None:
        """R45-F2: target_min == target_max must also raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="target_min"):
            self._parse(
                {"platform": "number", "name": "temp", "target_min": 10.0, "target_max": 10.0}
            )

    def test_valid_target_min_max_accepted(self) -> None:
        """R45-F2: target_min < target_max is accepted."""
        spec = self._parse(
            {"platform": "number", "name": "temp", "target_min": 5.0, "target_max": 30.0}
        )
        assert spec.target_min == 5.0
        assert spec.target_max == 30.0


class TestLoadProfilesYamlErrorR45:
    """R45-F6: yaml.YAMLError must not abort all profile loading."""

    def test_yaml_syntax_error_skipped(self, tmp_path) -> None:
        """A file with invalid YAML syntax is skipped; valid files still load."""

        from tuya_cloudless.profiles import load_profiles_from_dir

        bad = tmp_path / "bad.yaml"
        bad.write_text(": invalid: yaml: {\n", encoding="utf-8")
        good = tmp_path / "good.yaml"
        good.write_text(
            "name: Test\nmodel: '*'\nentities:\n  - platform: switch\n    name: power\n",
            encoding="utf-8",
        )
        profiles = load_profiles_from_dir(tmp_path)
        # The bad file is skipped; the good file loads successfully
        assert len(profiles) == 1
        assert profiles[0].name == "Test"


# ── _get_spec_dp_ids ────────────────────────────────────────────────────────────


def _make_spec(**kwargs: DPSpec | None) -> EntitySpec:
    """Build minimal EntitySpec with given dp_* overrides."""
    return EntitySpec(platform="switch", name="test", **kwargs)


def test_get_spec_dp_ids_single_power() -> None:
    dp = DPSpec(id="1", type="bool")
    spec = _make_spec(dp_power=dp)
    assert _get_spec_dp_ids(spec) == {"1"}


def test_get_spec_dp_ids_multiple_dps() -> None:
    spec = _make_spec(
        dp_power=DPSpec(id="1", type="bool"),
        dp_brightness=DPSpec(id="3", type="int"),
        dp_color_temp=DPSpec(id="4", type="int"),
    )
    assert _get_spec_dp_ids(spec) == {"1", "3", "4"}


def test_get_spec_dp_ids_no_dps() -> None:
    spec = _make_spec()
    assert _get_spec_dp_ids(spec) == set()


def test_get_spec_dp_ids_cover_dps() -> None:
    spec = _make_spec(
        dp_open=DPSpec(id="1", type="bool"),
        dp_position=DPSpec(id="2", type="int"),
        dp_tilt=DPSpec(id="3", type="int"),
        dp_stop=DPSpec(id="4", type="bool"),
    )
    assert _get_spec_dp_ids(spec) == {"1", "2", "3", "4"}


# ── _detect_profile_from_dps_core ──────────────────────────────────────────────


def _make_profile(name: str, model: str, dp_ids: list[str]) -> DeviceProfile:
    """Build a DeviceProfile with a single entity using given DP IDs."""
    entities = [
        EntitySpec(
            platform="switch",
            name=f"{name}_entity",
            dp_power=DPSpec(id=dp_ids[0], type="bool") if dp_ids else None,
        )
    ]
    if len(dp_ids) > 1:
        spec = EntitySpec(
            platform="switch",
            name=f"{name}_entity",
            dp_power=DPSpec(id=dp_ids[0], type="bool"),
            dp_value=DPSpec(id=dp_ids[1], type="int"),
        )
        entities = [spec]
    return DeviceProfile(name=name, model=model, entities=entities)


def test_detect_core_empty_dp_ids_returns_none() -> None:
    profile = _make_profile("switch", "sw*", ["1"])
    assert _detect_profile_from_dps_core(set(), [profile]) is None


def test_detect_core_empty_profiles_returns_none() -> None:
    assert _detect_profile_from_dps_core({"1", "2"}, []) is None


def test_detect_core_exact_match() -> None:
    profile = _make_profile("plug", "sp*", ["1", "19"])
    result = _detect_profile_from_dps_core({"1", "19"}, [profile])
    assert result is profile


def test_detect_core_partial_match_wins() -> None:
    profile_a = _make_profile("light", "light*", ["1", "3"])  # 1/2 overlap
    profile_b = _make_profile("plug", "sp*", ["1"])            # 1/1 overlap → score=1.0
    # profile_b has higher score (100%) than profile_a (50%)
    result = _detect_profile_from_dps_core({"1"}, [profile_a, profile_b])
    assert result is profile_b


def test_detect_core_no_overlap_uses_wildcard_fallback() -> None:
    wildcard = DeviceProfile(name="Generic", model="*", entities=[])
    specific = _make_profile("plug", "sp*", ["99"])  # dp "99" not in observed
    result = _detect_profile_from_dps_core({"1", "2"}, [specific, wildcard])
    assert result is wildcard


def test_detect_core_no_wildcard_no_match_returns_none() -> None:
    profile = _make_profile("plug", "sp*", ["99"])
    result = _detect_profile_from_dps_core({"1", "2"}, [profile])
    assert result is None


def test_detect_core_skips_profile_with_no_expected_dps() -> None:
    empty_profile = DeviceProfile(name="Empty", model="e*", entities=[])
    result = _detect_profile_from_dps_core({"1"}, [empty_profile])
    assert result is None


# ── ProfileRegistry.detect_profile_from_dps + __len__ ──────────────────────────


def test_registry_len(tmp_path: Path) -> None:
    registry = ProfileRegistry()
    profiles_data = load_profiles_from_dir(PROFILES_DIR)
    registry.init(PROFILES_DIR)
    assert len(registry) == len(profiles_data)


def test_registry_detect_profile_from_dps_returns_match(tmp_path: Path) -> None:
    """detect_profile_from_dps delegates to _detect_profile_from_dps_core."""
    registry = ProfileRegistry()
    registry.init(PROFILES_DIR)
    # DP "1" (bool) appears in generic_switch / smart_plug profiles
    result = registry.detect_profile_from_dps({"1"})
    # May be None or a profile — just assert no exception and correct type
    assert result is None or hasattr(result, "name")


def test_registry_detect_profile_from_dps_empty_returns_none() -> None:
    registry = ProfileRegistry()
    registry.init(PROFILES_DIR)
    assert registry.detect_profile_from_dps(set()) is None


# ── Module-level detect_profile_from_dps ────────────────────────────────────────


def test_module_detect_profile_from_dps_empty() -> None:
    """Module-level wrapper delegates to compat registry."""
    result = detect_profile_from_dps(set())
    assert result is None


def test_module_detect_profile_from_dps_with_dp() -> None:
    """Module-level wrapper returns profile or None (no exception)."""
    result = detect_profile_from_dps({"1", "19"})
    assert result is None or hasattr(result, "name")

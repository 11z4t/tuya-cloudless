"""Tests for the extended set of device profiles (PLAT-664/665/666)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tuya_cloudless.profiles import (
    init_profiles,
    list_profiles,
    load_profile,
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

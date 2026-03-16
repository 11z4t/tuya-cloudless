"""Device profile system for Tuya Cloudless.

YAML-defined profiles map device types to entity specifications, enabling
automatic entity creation without code changes for new device models.

Profile files live in the ``profiles/`` directory and are loaded once at
integration startup. Each profile maps to one or more Home Assistant entities
(switch, light, sensor, binary_sensor, cover).
"""

from __future__ import annotations

import fnmatch

__all__ = [
    "DPSpec",
    "DeviceProfile",
    "EntitySpec",
    "ProfileRegistry",
    "detect_profile_from_dps",
    "find_profile",
    "find_profile_by_product_key",
    "init_profiles",
    "list_profiles",
    "load_profile",
    "load_profiles_from_dir",
]
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DPSpec:
    """Specification for a single Data Point (DP).

    A data point is the atomic unit of device state in the Tuya protocol.
    Each DP has a string ID (e.g. "1", "19") and a value type.

    Attributes:
        id:      DP string identifier, e.g. "1" or "19".
        type:    Value type: "bool", "int", "str", "enum", or "raw".
        scale:   Multiply raw value by this to get display value (e.g. 0.1
                 for 10x granularity). Defaults to 1.0 (no scaling).
        min_raw: Minimum accepted raw value from the device (None = no limit).
        max_raw: Maximum accepted raw value from the device (None = no limit).
    """

    id: str
    type: str
    scale: float = 1.0
    min_raw: int | None = None
    max_raw: int | None = None


@dataclass(frozen=True)
class EntitySpec:
    """Specification for a single Home Assistant entity.

    Describes which HA platform to use, which DPs map to which controls,
    and optional metadata (device class, unit, state class).

    Attributes:
        platform:      HA platform: "switch", "light", "sensor",
                       "binary_sensor", "cover", "number", "select",
                       "climate", or "fan".
        name:          Translation key shown in HA (e.g. "main_switch").
        dp_power:      Primary on/off DP (switch, binary_sensor).
        dp_value:      Numeric or enum state DP (sensor).
        dp_brightness: Brightness DP (light only, 0-1000 raw).
        dp_color_temp: Colour temperature DP (light only, 0=warm, 1000=cool).
        dp_open:          Open/close DP (cover only; True = open).
        dp_position:      Position DP (cover; 0-100).
        dp_tilt:          Tilt position DP (cover; 0-100, optional).
        dp_direction:     Direction DP (cover; enum string).
        dp_hs_hue:        Hue DP (light; 0-360 raw typical).
        dp_hs_saturation: Saturation DP (light; 0-1000 raw typical).
        dp_color_mode:    Color mode DP (light; enum "white"/"colour").
        dp_stop:          Stop-movement DP (cover; sends True to halt motor).
        dp_scene:         Scene/effect DP (light; enum or str — value = effect name).
        dp_colour_data:   Compound HSV colour DP (light; 12-char hex "HHHHSSSSBBBB").
        effects:          Supported effect names for the light.
        device_class:     HA device class string (e.g. "power", "current").
        state_class:      HA state class string (e.g. "measurement").
        unit:             Unit of measurement (e.g. "W", "A", "%").
    """

    platform: str
    name: str
    dp_power: DPSpec | None = None
    dp_value: DPSpec | None = None
    dp_brightness: DPSpec | None = None
    dp_color_temp: DPSpec | None = None
    dp_open: DPSpec | None = None
    dp_position: DPSpec | None = None
    dp_tilt: DPSpec | None = None
    dp_stop: DPSpec | None = None
    dp_direction: DPSpec | None = None
    dp_hs_hue: DPSpec | None = None
    dp_hs_saturation: DPSpec | None = None
    dp_color_mode: DPSpec | None = None
    dp_scene: DPSpec | None = None
    dp_colour_data: DPSpec | None = None
    effects: tuple[str, ...] = ()
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None
    # Climate / fan / number / select fields
    dp_mode: DPSpec | None = None
    dp_temp_set: DPSpec | None = None
    dp_temp_current: DPSpec | None = None
    dp_oscillate: DPSpec | None = None
    dp_options: tuple[str, ...] = ()
    target_min: float | None = None
    target_max: float | None = None
    step: float = 1.0


@dataclass
class DeviceProfile:
    """A device profile mapping a product model to entity specs.

    Profiles are loaded from YAML files in the ``profiles/`` directory.
    One profile may define multiple entities (e.g. a smart plug with a
    switch, two measurement sensors, and an overload alert).

    Attributes:
        name:     Human-readable profile name shown in the setup wizard.
        model:    Glob pattern matched against device product key ("*" = any).
        entities: Ordered list of entity specifications for this device type.
    """

    name: str
    model: str
    entities: list[EntitySpec] = field(default_factory=list)


# ── YAML parsing ──────────────────────────────────────────────────────────────


def _parse_dp_spec(data: dict[str, Any] | None) -> DPSpec | None:
    """Parse a DP specification dict from YAML.

    Args:
        data: Dict with keys ``id``, ``type``, and optional ``scale``,
              ``min_raw``, ``max_raw``. ``None`` is accepted (returns ``None``).

    Returns:
        :class:`DPSpec` instance, or ``None`` if *data* is ``None``.

    Raises:
        KeyError: If required keys ``id`` or ``type`` are missing.
        TypeError: If values have unexpected types.
    """
    if data is None:
        return None
    return DPSpec(
        id=str(data["id"]),
        type=str(data["type"]),
        scale=float(data.get("scale", 1.0)),
        min_raw=int(data["min_raw"]) if "min_raw" in data else None,
        max_raw=int(data["max_raw"]) if "max_raw" in data else None,
    )


def _parse_entity_spec(data: dict[str, Any]) -> EntitySpec:
    """Parse an entity specification dict from YAML.

    Args:
        data: Dict representing a single entity in a profile YAML file.

    Returns:
        :class:`EntitySpec` instance.

    Raises:
        KeyError: If required keys ``platform`` or ``name`` are missing.
    """
    return EntitySpec(
        platform=str(data["platform"]),
        name=str(data.get("name", "")),
        dp_power=_parse_dp_spec(data.get("dp_power")),
        dp_value=_parse_dp_spec(data.get("dp_value")),
        dp_brightness=_parse_dp_spec(data.get("dp_brightness")),
        dp_color_temp=_parse_dp_spec(data.get("dp_color_temp")),
        dp_open=_parse_dp_spec(data.get("dp_open")),
        dp_position=_parse_dp_spec(data.get("dp_position")),
        dp_tilt=_parse_dp_spec(data.get("dp_tilt")),
        dp_stop=_parse_dp_spec(data.get("dp_stop")),
        dp_direction=_parse_dp_spec(data.get("dp_direction")),
        dp_hs_hue=_parse_dp_spec(data.get("dp_hs_hue")),
        dp_hs_saturation=_parse_dp_spec(data.get("dp_hs_saturation")),
        dp_color_mode=_parse_dp_spec(data.get("dp_color_mode")),
        dp_scene=_parse_dp_spec(data.get("dp_scene")),
        dp_colour_data=_parse_dp_spec(data.get("dp_colour_data")),
        effects=tuple(data.get("effects", [])),
        device_class=str(data["device_class"]) if "device_class" in data else None,
        state_class=str(data["state_class"]) if "state_class" in data else None,
        unit=str(data["unit"]) if "unit" in data else None,
        dp_mode=_parse_dp_spec(data.get("dp_mode")),
        dp_temp_set=_parse_dp_spec(data.get("dp_temp_set")),
        dp_temp_current=_parse_dp_spec(data.get("dp_temp_current")),
        dp_oscillate=_parse_dp_spec(data.get("dp_oscillate")),
        dp_options=tuple(str(v) for v in data.get("dp_options", [])),
        target_min=float(data["target_min"]) if "target_min" in data else None,
        target_max=float(data["target_max"]) if "target_max" in data else None,
        step=float(data.get("step", 1.0)),
    )


def load_profile(path: Path) -> DeviceProfile:
    """Load a single device profile from a YAML file.

    Args:
        path: Absolute path to the ``.yaml`` profile file.

    Returns:
        Populated :class:`DeviceProfile`.

    Raises:
        ImportError: If PyYAML is not installed.
        KeyError: If required fields are missing in the YAML.
        OSError: If the file cannot be read.
    """
    import yaml  # Late import — optional runtime dep

    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    entities = [_parse_entity_spec(e) for e in raw.get("entities", [])]
    return DeviceProfile(
        name=str(raw["name"]),
        model=str(raw.get("model", "*")),
        entities=entities,
    )


def load_profiles_from_dir(profiles_dir: Path) -> list[DeviceProfile]:
    """Load all ``.yaml`` profiles from a directory.

    Files are sorted alphabetically so loading order is deterministic.
    Invalid files are logged and skipped rather than raising an error.

    Args:
        profiles_dir: Directory to scan for ``*.yaml`` files.

    Returns:
        List of successfully loaded :class:`DeviceProfile` objects.
    """
    profiles: list[DeviceProfile] = []
    if not profiles_dir.is_dir():
        _LOGGER.warning("Profiles directory not found: %s", profiles_dir)
        return profiles

    for yaml_file in sorted(profiles_dir.glob("*.yaml")):
        try:
            profile = load_profile(yaml_file)
            profiles.append(profile)
            _LOGGER.debug(
                "Loaded profile '%s' (model=%s) from %s",
                profile.name,
                profile.model,
                yaml_file.name,
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            _LOGGER.warning("Skipping invalid profile %s: %s", yaml_file.name, exc)

    return profiles


# ── DP-based profile detection (PLAT-778) ────────────────────────────────────


def _get_spec_dp_ids(spec: EntitySpec) -> set[str]:
    """Return the set of DP IDs referenced by a single EntitySpec.

    Iterates over all ``dp_*`` attributes of *spec* and collects their ``.id``
    values so that callers can compare a live DP snapshot against a profile.

    Args:
        spec: Entity specification from a loaded device profile.

    Returns:
        Set of DP ID strings (e.g. ``{"1", "19"}``).
    """
    ids: set[str] = set()
    for attr_name in (
        "dp_power", "dp_value", "dp_brightness", "dp_color_temp",
        "dp_open", "dp_position", "dp_tilt", "dp_stop", "dp_direction",
        "dp_hs_hue", "dp_hs_saturation", "dp_color_mode", "dp_scene",
        "dp_colour_data", "dp_mode", "dp_temp_set", "dp_temp_current",
        "dp_oscillate",
    ):
        val = getattr(spec, attr_name, None)
        if isinstance(val, DPSpec):
            ids.add(val.id)
    return ids


def _detect_profile_from_dps_core(
    dp_ids: set[str],
    profiles: list[DeviceProfile],
) -> DeviceProfile | None:
    """Core implementation: find best-matching profile for observed DP IDs.

    Scores each profile by the fraction of its expected DPs that appear in
    *dp_ids*.  A wildcard-model (``"*"``) profile is only returned as a
    last-resort fallback when no specific profile has any overlap.

    Args:
        dp_ids:   Set of DP IDs observed in the device's DP_QUERY response.
        profiles: List of candidate profiles to score.

    Returns:
        Best-matching :class:`DeviceProfile`, or ``None`` if *dp_ids* is empty.
    """
    if not dp_ids or not profiles:
        return None

    best_profile: DeviceProfile | None = None
    best_score: float = 0.0
    wildcard_fallback: DeviceProfile | None = None

    for profile in profiles:
        if profile.model == "*":
            if wildcard_fallback is None:
                wildcard_fallback = profile
            continue

        expected: set[str] = set()
        for spec in profile.entities:
            expected.update(_get_spec_dp_ids(spec))

        if not expected:
            continue

        overlap = len(dp_ids & expected)
        if overlap == 0:
            continue

        # Score = fraction of profile's expected DPs found on this device.
        # Higher score → profile is a better fit for the observed DP set.
        score = overlap / len(expected)
        if score > best_score:
            best_score = score
            best_profile = profile

    return best_profile or wildcard_fallback


# ── Per-instance profile registry ────────────────────────────────────────────


class ProfileRegistry:
    """Per-hass-instance profile registry (no global mutable state).

    Create one instance per Home Assistant instance and store it in
    ``hass.data[DOMAIN]``. This prevents sharing mutable profile state
    across multiple HA instances running in the same Python process.

    Usage in integration setup::

        registry = ProfileRegistry()
        registry.init(PROFILES_DIR)          # sync — run in executor
        hass.data[DOMAIN]["profile_registry"] = registry
    """

    __slots__ = ("_profiles",)

    def __init__(self) -> None:
        self._profiles: list[DeviceProfile] = []

    def init(self, profiles_dir: Path) -> None:
        """Load profiles from *profiles_dir* into this registry.

        Call once per instance. Thread-safe for single-writer scenarios
        (HA event loop is single-threaded).

        Args:
            profiles_dir: Directory containing ``*.yaml`` profile files.
        """
        self._profiles = load_profiles_from_dir(profiles_dir)
        _LOGGER.info(
            "Profile registry ready: %d profiles loaded from %s",
            len(self._profiles),
            profiles_dir,
        )

    def list_profiles(self) -> list[DeviceProfile]:
        """Return all profiles in this registry (shallow copy)."""
        return list(self._profiles)

    def find_profile(self, name: str) -> DeviceProfile | None:
        """Find a profile by its exact display name.

        Args:
            name: Profile name as stored in config entry data.

        Returns:
            Matching :class:`DeviceProfile`, or ``None`` if not found.
        """
        for profile in self._profiles:
            if profile.name == name:
                return profile
        return None

    def detect_profile_from_dps(self, dp_ids: set[str]) -> DeviceProfile | None:
        """Find the best-matching profile for a set of observed device DP IDs.

        Delegates to the module-level :func:`detect_profile_from_dps`.

        Args:
            dp_ids: Set of DP IDs observed from the device's DP_QUERY response.

        Returns:
            Best-matching :class:`DeviceProfile`, or ``None`` if no match.
        """
        return _detect_profile_from_dps_core(dp_ids, self._profiles)

    def find_profile_by_product_key(self, product_key: str) -> DeviceProfile | None:
        """Find the best-matching profile for a device product key.

        Uses glob-style matching against each profile's ``model`` pattern.
        The first match wins (alphabetical profile file order).

        Args:
            product_key: Product key string from UDP device discovery.

        Returns:
            Best-matching :class:`DeviceProfile`, or ``None`` if none match.
        """
        fallback: DeviceProfile | None = None
        for profile in self._profiles:
            if profile.model == "*":
                if fallback is None:
                    fallback = profile
                continue
            if fnmatch.fnmatch(product_key.lower(), profile.model.lower()):
                return profile
        return fallback

    def __len__(self) -> int:
        return len(self._profiles)


# ── Module-level compatibility helpers ────────────────────────────────────────
# These wrap a single module-level ProfileRegistry for callers (config_flow,
# tests) that cannot easily receive a hass-scoped registry.  Do NOT use these
# from integration setup code — use a per-hass ProfileRegistry via hass.data.

_COMPAT_REGISTRY: ProfileRegistry = ProfileRegistry()


def init_profiles(profiles_dir: Path) -> None:
    """Load profiles into the module-level compatibility registry.

    .. note::
        For HA integration setup, prefer creating a :class:`ProfileRegistry`
        directly and storing it in ``hass.data[DOMAIN]``.

    Args:
        profiles_dir: Directory containing ``*.yaml`` profile files.
    """
    _COMPAT_REGISTRY.init(profiles_dir)


def list_profiles() -> list[DeviceProfile]:
    """Return all profiles in the module-level compatibility registry."""
    return _COMPAT_REGISTRY.list_profiles()


def find_profile(name: str) -> DeviceProfile | None:
    """Find a profile by name in the module-level compatibility registry."""
    return _COMPAT_REGISTRY.find_profile(name)


def find_profile_by_product_key(product_key: str) -> DeviceProfile | None:
    """Find the best-matching profile in the module-level compatibility registry."""
    return _COMPAT_REGISTRY.find_profile_by_product_key(product_key)


def detect_profile_from_dps(dp_ids: set[str]) -> DeviceProfile | None:
    """Find the best-matching profile in the module-level compatibility registry.

    Args:
        dp_ids: Set of DP IDs observed from the device's DP_QUERY response.

    Returns:
        Best-matching :class:`DeviceProfile`, or ``None`` if no match.
    """
    return _COMPAT_REGISTRY.detect_profile_from_dps(dp_ids)

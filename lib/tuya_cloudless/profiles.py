"""Device profile system for Tuya Cloudless.

YAML-defined profiles map device types to entity specifications, enabling
automatic entity creation without code changes for new device models.

Profile files live in the ``profiles/`` directory and are loaded once at
integration startup. Each profile maps to one or more Home Assistant entities
(switch, light, sensor, binary_sensor, cover).
"""

from __future__ import annotations

import fnmatch
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
                       "binary_sensor", or "cover".
        name:          Translation key shown in HA (e.g. "main_switch").
        dp_power:      Primary on/off DP (switch, binary_sensor).
        dp_value:      Numeric or enum state DP (sensor).
        dp_brightness: Brightness DP (light only, 0-1000 raw).
        dp_color_temp: Colour temperature DP (light only, 0=warm, 1000=cool).
        dp_open:          Open/close DP (cover only; True = open).
        dp_position:      Position DP (cover; 0-100).
        dp_direction:     Direction DP (cover; enum string).
        dp_hs_hue:        Hue DP (light; 0-360 raw typical).
        dp_hs_saturation: Saturation DP (light; 0-1000 raw typical).
        dp_color_mode:    Color mode DP (light; enum "white"/"colour").
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
    dp_direction: DPSpec | None = None
    dp_hs_hue: DPSpec | None = None
    dp_hs_saturation: DPSpec | None = None
    dp_color_mode: DPSpec | None = None
    effects: tuple[str, ...] = ()
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None


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
        dp_direction=_parse_dp_spec(data.get("dp_direction")),
        dp_hs_hue=_parse_dp_spec(data.get("dp_hs_hue")),
        dp_hs_saturation=_parse_dp_spec(data.get("dp_hs_saturation")),
        dp_color_mode=_parse_dp_spec(data.get("dp_color_mode")),
        effects=tuple(data.get("effects", [])),
        device_class=str(data["device_class"]) if "device_class" in data else None,
        state_class=str(data["state_class"]) if "state_class" in data else None,
        unit=str(data["unit"]) if "unit" in data else None,
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


# ── Global profile registry ───────────────────────────────────────────────────

_REGISTRY: list[DeviceProfile] = []


def init_profiles(profiles_dir: Path) -> None:
    """Load all profiles from *profiles_dir* into the module-level registry.

    Call this once at integration startup. Thread-safe for single-writer
    scenarios (HA event loop is single-threaded).

    Args:
        profiles_dir: Directory containing ``*.yaml`` profile files.
    """
    global _REGISTRY
    _REGISTRY = load_profiles_from_dir(profiles_dir)
    _LOGGER.info(
        "Profile registry ready: %d profiles loaded from %s",
        len(_REGISTRY),
        profiles_dir,
    )


def list_profiles() -> list[DeviceProfile]:
    """Return all profiles in the registry.

    Returns:
        Shallow copy of the registry list (stable across mutations).
    """
    return list(_REGISTRY)


def find_profile(name: str) -> DeviceProfile | None:
    """Find a profile by its exact display name.

    Args:
        name: Profile name as stored in config entry data.

    Returns:
        Matching :class:`DeviceProfile`, or ``None`` if not found.
    """
    for profile in _REGISTRY:
        if profile.name == name:
            return profile
    return None


def find_profile_by_product_key(product_key: str) -> DeviceProfile | None:
    """Find the best-matching profile for a device product key.

    Uses glob-style matching against each profile's ``model`` pattern.
    The first match wins (alphabetical profile file order).

    Args:
        product_key: Product key string from UDP device discovery.

    Returns:
        Best-matching :class:`DeviceProfile`, or ``None`` if none match.
    """
    for profile in _REGISTRY:
        if profile.model == "*":
            continue  # Generic catch-all — only match as last resort
        if fnmatch.fnmatch(product_key.lower(), profile.model.lower()):
            return profile

    # Fall back to the first wildcard profile
    for profile in _REGISTRY:
        if profile.model == "*":
            return profile

    return None

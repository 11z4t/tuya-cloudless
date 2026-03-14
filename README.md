# Tuya Cloudless

[![CI](https://github.com/11z4t/tuya-cloudless/actions/workflows/ci.yml/badge.svg)](https://github.com/11z4t/tuya-cloudless/actions/workflows/ci.yml)
[![Quality: Platinum](https://img.shields.io/badge/quality-platinum_%E2%AD%90%E2%AD%90%E2%AD%90%E2%AD%90-gold)](https://github.com/11z4t/tuya-cloudless)

Cloud-free local control for Tuya WiFi devices via Home Assistant.

## Features

- **100% Local** — No cloud dependency after initial device setup
- **Tuya Local Protocol** — Direct communication with devices on your LAN
- **HACS Compatible** — Easy installation via Home Assistant Community Store
- **Device Profiles** — YAML-based device definitions for easy extensibility

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** > **Custom repositories**
3. Add `https://github.com/11z4t/tuya-cloudless`
4. Install **Tuya Cloudless**
5. Restart Home Assistant

### Manual

Copy `custom_components/tuya_cloudless/` to your HA `custom_components/` directory.

## Development

```bash
# Install with dev dependencies
pip install -e ".[test]"

# Run all checks
bash scripts/run-checks.sh

# Run tests
pytest tests/unit/ -v
```

## Architecture

```
lib/tuya_cloudless/          # Standalone library (no HA imports)
custom_components/tuya_cloudless/  # HA integration wrapper
tests/                       # Unit + integration + security tests
profiles/                    # Device YAML profiles
```

## License

MIT

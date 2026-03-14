# Tuya Cloudless Integration for Home Assistant

**Premium local control for Tuya devices — no cloud required**

[![CI](https://img.shields.io/github/actions/workflow/status/4recon/tuya-cloudless/ci.yml?branch=main)](https://github.com/4recon/tuya-cloudless/actions)
[![codecov](https://codecov.io/gh/4recon/tuya-cloudless/branch/main/graph/badge.svg)](https://codecov.io/gh/4recon/tuya-cloudless)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

Tuya Cloudless enables **100% local control** of Tuya WiFi devices without any cloud dependency. Pair devices using Web Bluetooth on your phone, activate them with a local mock cloud, and control them via the local TCP protocol.

### Key Features

- 🔒 **Zero cloud dependency** — all operations are local
- 📱 **Web Bluetooth pairing** — easy setup via browser (port 8099)
- ⚡ **Real-time control** — instant response via TCP (port 6668)
- 🔐 **Full protocol support** — v3.1, v3.2, v3.3 (AES-ECB), v3.4, v3.5 (AES-GCM)
- 🌍 **Multilingual** — English and Swedish from day one
- 🏆 **Premium quality** — type hints, async/await, proper error handling

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Home Assistant                          │
│  ┌───────────────────────────────────────────────────────┐  │
│  │         custom_components/tuya_cloudless              │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │   Config    │  │  Entities   │  │  Diagnostics │  │  │
│  │  │    Flow     │  │  (lights,   │  │              │  │  │
│  │  │             │  │  switches)  │  │              │  │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────────────┘  │  │
│  │         │                │                            │  │
│  │         └────────────────┴────────────────────────────┤  │
│  │                    lib/tuya_cloudless                 │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │  │
│  │  │   Protocol   │  │    Crypto    │  │  Discovery │  │  │
│  │  │   Parser     │  │  (AES-ECB/   │  │   (UDP)    │  │  │
│  │  │  (v3.1-3.5)  │  │    GCM)      │  │            │  │  │
│  │  └──────────────┘  └──────────────┘  └────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
        │                        │                   │
        ▼                        ▼                   ▼
  Web Bluetooth API         TCP 6668           UDP Broadcast
  (port 8099, pairing)    (local control)      (discovery)
        │                        │                   │
        └────────────────────────┴───────────────────┘
                              │
                    ┌─────────┴──────────┐
                    │   Tuya WiFi Device │
                    │  (local network)   │
                    └────────────────────┘
```

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add `https://git.malmgrens.me/4recon/tuya-cloudless` as repository
6. Category: "Integration"
7. Click "Add"
8. Search for "Tuya Cloudless" and install

### Manual

1. Copy `custom_components/tuya_cloudless` to your HA `config/custom_components/` directory
2. Restart Home Assistant
3. Add integration via UI

## Quick Start

1. **Add Integration**
   Configuration → Devices & Services → Add Integration → "Tuya Cloudless"

2. **Pair Device**
   Open `http://YOUR_HA_IP:8099` on your phone and follow the Web Bluetooth pairing wizard

3. **Activate Device**
   The integration will automatically activate the device using the local mock cloud

4. **Control Device**
   Devices appear as lights, switches, or other entities depending on type

## Supported Devices

This integration supports Tuya WiFi devices with protocol versions 3.1-3.5:

- 💡 **Lights** — dimmable, RGB, RGBW, CCT
- 🔌 **Switches** — single/multi-gang power outlets
- 📊 **Sensors** — temperature, humidity, power monitoring
- 🌡️ **Climate** — thermostats, heaters
- 🚪 **Covers** — blinds, curtains, garage doors

**Note:** BLE-only devices require the separate `tuya-ble-mesh` integration.

## Protocol Support

| Version | Encryption | Status |
|---------|------------|--------|
| 3.1     | None       | ✅ Supported |
| 3.2     | None       | ✅ Supported |
| 3.3     | AES-ECB    | ✅ Supported |
| 3.4     | AES-GCM    | ✅ Supported |
| 3.5     | AES-GCM    | ✅ Supported |

## Development

```bash
# Clone repository
git clone https://git.malmgrens.me/4recon/tuya-cloudless.git
cd tuya-cloudless

# Install dependencies
pip install -e .
pip install -r requirements-dev.txt

# Run tests
pytest --cov=lib --cov=custom_components

# Lint code
ruff check .
mypy lib/ custom_components/
```

## References

- [TinyTuya](https://github.com/jasonacox/tinytuya) — Protocol reference
- [LocalTuya](https://github.com/rospogriern/localtuya) — HA integration reference
- [Web Bluetooth API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Bluetooth_API) — Browser BLE standard

## License

MIT License — see [LICENSE](LICENSE) for details

## Support

- 📝 [Documentation](docs/)
- 🐛 [Issue Tracker](https://git.malmgrens.me/4recon/tuya-cloudless/issues)
- 💬 [Discussions](https://git.malmgrens.me/4recon/tuya-cloudless/discussions)

---

**Made with ❤️ by 4recon** — Premium quality, zero compromises

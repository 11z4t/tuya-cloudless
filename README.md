# Tuya Cloudless

[![CI](https://github.com/11z4t/tuya-cloudless/actions/workflows/ci.yml/badge.svg)](https://github.com/11z4t/tuya-cloudless/actions)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Local control of Tuya WiFi devices — **no cloud, no registration, no data leaves your home**.

## How it works

**No Tuya account. No Tuya app. Nothing sent to the cloud.**

1. **BLE pairing** — Put your device in pairing mode. Home Assistant opens a pairing tool in your browser. You select the device over Bluetooth, enter your WiFi password, and the device connects to your network — all locally.

2. **Local activation** — When the device connects to WiFi it sends a one-time activation request. Tuya devices normally send this to Tuya's cloud servers. Tuya Cloudless intercepts it on your local network instead and responds with a randomly generated local key. The device stores the key — it is now fully activated without ever talking to Tuya.

3. **Local TCP control** — After activation, all communication goes directly over TCP port 6668 on your LAN. The local key encrypts every message. No internet connection required.

4. **UDP discovery** — Devices broadcast their presence on UDP 6666/6667. The integration listens and updates device availability in real time.

See the [wiki](https://github.com/11z4t/tuya-cloudless/wiki/How-It-Works) for a detailed technical explanation.

## Requirements

- Home Assistant 2024.4 or later
- **Chrome or Edge** for BLE pairing (Web Bluetooth — Safari/Firefox not supported)
- **HTTPS on your HA** — required by the browser for Bluetooth access. [How to set this up →](https://github.com/11z4t/tuya-cloudless/wiki/HTTPS-Setup)

## Protocol support

| Version | Encryption | Used by |
|---------|-----------|---------|
| 3.1 | None / MD5-key AES-ECB | Older devices |
| 3.2 | MD5-key AES-ECB | Common smart plugs |
| 3.3 | MD5-key AES-ECB + HMAC-SHA256 | Most 2020–2023 devices |
| 3.4 | ECDH session key + AES-GCM | New devices (2023+) |
| 3.5 | ECDH session key + AES-GCM + extended DPS | Latest |

## Installation

### HACS (recommended)
1. Open HACS → Integrations → ⋮ → Custom repositories
2. Paste `https://github.com/11z4t/tuya-cloudless`, category **Integration**
3. Download **Tuya Cloudless** and restart Home Assistant
4. Go to **Settings → Devices & Services → Add integration → Tuya Cloudless**
5. Choose **BLE Pairing** and follow the wizard

[Full installation guide →](https://github.com/11z4t/tuya-cloudless/wiki/Installation)

### Manual
Copy `custom_components/tuya_cloudless/` to your HA `custom_components/` directory.

## Security

- All key material stays on your HA instance
- No outbound cloud calls after initial activation mock
- Secrets stored in HA config entry (encrypted at rest by HA)
- See [SECURITY.md](SECURITY.md) for responsible disclosure

## Differences from cloud-based Tuya integrations

| Feature | Tuya Cloudless | Official Tuya HA |
|---------|---------------|-----------------|
| Cloud account required | ✗ | ✓ |
| Data sent to Tuya servers | ✗ | ✓ |
| Works without internet | ✓ | ✗ |
| Instant response | ✓ | ~500 ms cloud latency |
| New device support | WiFi + BLE-combo | WiFi only |

## Related projects

- [tuya-ble-mesh](https://github.com/kvista-se/tuya-ble-mesh) — BLE mesh (Malmbergs lights, no WiFi required)

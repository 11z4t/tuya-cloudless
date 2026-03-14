# Tuya Cloudless

[![CI](https://git.malmgrens.me/4recon/tuya-cloudless/actions/workflows/ci.yml/badge.svg)](https://git.malmgrens.me/4recon/tuya-cloudless/actions)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Local control of Tuya WiFi devices — **no cloud, no registration, no data leaves your home**.

## How it works

1. **Web Bluetooth pairing** — A page hosted at `http://your-ha:8099` lets you pair Tuya devices directly from your phone browser using Web Bluetooth. The device's WiFi credentials are configured locally; no Tuya cloud account needed.
2. **Fake cloud activation** — A minimal local mock of the Tuya activation API intercepts the device's first-boot cloud call and responds with a valid-looking session. The device believes it is registered.
3. **Local TCP control** — Runtime commands are sent over TCP port 6668 using the Tuya LAN protocol (v3.1–3.5). Responses are parsed locally.
4. **UDP discovery** — Devices broadcast discovery packets on UDP 6666/6667. The integration listens passively and updates device availability in real time.

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
Add this repo as a custom HACS integration repository.

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

- [tuya-ble-mesh](https://git.malmgrens.me/4recon/tuya-ble-mesh) — BLE mesh (Malmbergs lights, no WiFi required)

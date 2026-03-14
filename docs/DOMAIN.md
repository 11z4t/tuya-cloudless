# Domain Knowledge — Tuya Local Protocol

## Overview

Tuya WiFi devices communicate via a local TCP protocol on port 6668 (v3.3+) or 6667 (v3.1).
After initial cloud pairing, devices can be controlled locally using their device ID and local key.

## Protocol Versions

- **v3.1** — Legacy, unencrypted payload
- **v3.3** — AES-ECB encrypted, most common
- **v3.4** — AES-GCM encrypted, newer devices
- **v3.5** — Latest, enhanced security

## Key Concepts

- **Device ID** — Unique identifier for each device
- **Local Key** — 16-byte AES key for encryption (obtained from Tuya IoT Platform)
- **DPS (Data Points)** — Device state represented as key-value pairs (e.g., `{"1": true}` = switch on)
- **Sequence Number** — Monotonically increasing per session

## Message Format

```
Header (16 bytes) | Payload (variable) | CRC32 (4 bytes) | Suffix (4 bytes)
```

## Device Discovery

- Devices broadcast UDP on port 6666 (unencrypted) and 6667 (encrypted)
- Discovery messages contain device ID, IP, protocol version

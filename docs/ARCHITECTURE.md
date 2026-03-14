# Architecture — Tuya Cloudless

## Overview

Tuya Cloudless provides cloud-free local control for Tuya WiFi devices via Home Assistant.
The integration communicates directly with devices on the local network using the Tuya Local protocol.

## Layer Separation

```
┌─────────────────────────────────────────────┐
│  Home Assistant Integration Layer           │
│  custom_components/tuya_cloudless/          │
│  - Config flow, entities, services          │
│  - MAY import homeassistant.*               │
├─────────────────────────────────────────────┤
│  Standalone Library Layer                   │
│  lib/tuya_cloudless/                        │
│  - Protocol, crypto, device management      │
│  - MUST NOT import homeassistant            │
├─────────────────────────────────────────────┤
│  Device Profiles                            │
│  profiles/                                  │
│  - YAML device definitions                  │
│  - No code changes for new devices          │
└─────────────────────────────────────────────┘
```

## Structural Rules

See `CLAUDE.md` for the complete list of structural rules (S1-S9).

## Key Modules

- `protocol.py` — All network I/O and Tuya Local protocol communication
- `crypto.py` — All cryptographic operations (AES, MD5 for protocol)
- `exceptions.py` — Custom exception hierarchy

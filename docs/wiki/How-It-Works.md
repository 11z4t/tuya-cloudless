# How It Works

Tuya Cloudless pairs and controls devices entirely on your local network. No Tuya account. No cloud traffic after first setup.

---

## Overview

```
┌──────────────────────────────────────────────────────────┐
│  Your home network                                       │
│                                                          │
│  [Browser]  ──── BLE ────►  [Tuya Device]               │
│     │                            │                       │
│     │◄── SSE events              │ (connects to WiFi)    │
│                                  ▼                       │
│                       POST /api/tuya/device/active       │
│                                  ▼                       │
│                       [HA + Tuya Cloudless]              │
│                         generates local_key              │
│                         stores in config entry           │
│                                  │                       │
│                        TCP 6668 ◄┘                       │
│                    (runtime control)                     │
└──────────────────────────────────────────────────────────┘
```

---

## Step 1 — BLE Provisioning

When a Tuya WiFi+BLE combo device is in pairing mode, it broadcasts a BLE service:

- **Service UUID:** `0000fd50-0000-1000-8000-00805f9b34fb`
- **Write characteristic:** `00000001-0000-1001-8001-00805f9b07d0`
- **Notify characteristic:** `00000002-0000-1001-8001-00805f9b07d0`

The pairing page (served at `http://your-ha:8099`) uses **Web Bluetooth** (Chrome/Edge only) to:

1. Scan for devices advertising the Tuya BLE service
2. Perform a handshake (CRC-validated frame exchange)
3. Send a WiFi config payload via the BLE write characteristic

The payload format is JSON:
```json
{
  "s": "MyWiFiNetwork",
  "p": "wifi-password",
  "t": "abc123…",
  "r": "az",
  "activator": "http://192.168.1.10:8099"
}
```

The `activator` field tells the device where to call after connecting to WiFi — pointing to **our local server**, not the Tuya cloud.

### BLE frame format

```
magic(2=0x55AA) | version(1=0x04) | cmd(1) | seq(2 BE) | len(2 BE) | payload | CRC16-MODBUS(2 LE)
```

Frames are split into 20-byte BLE chunks:
```
[chunk_no][total_chunks][data...]
```

---

## Step 2 — Fake-Cloud Activation

After joining WiFi, the Tuya device makes an HTTP POST to the `activator` URL:

```
POST http://192.168.1.10:8099/api/tuya/device/active
Content-Type: application/json

{
  "gw_id": "bf1234567890abcdef",
  "product_key": "key8lttjfm9p8dxu",
  "token": "abc123…",
  "sw_ver": "2.0"
}
```

**This is where the magic happens.** Tuya Cloudless intercepts this call and responds with a fake-cloud activation response:

```json
{
  "t": 1710000000,
  "success": true,
  "result": {
    "gwId": "bf1234567890abcdef",
    "active": 2,
    "localKey": "a1b2c3d4e5f6a7b8",
    "timezone": "UTC",
    "netType": 0
  }
}
```

The `localKey` in this response is **generated randomly** by Tuya Cloudless (`secrets.token_hex(16)`). The device stores it and uses it to encrypt all future local communication.

The same key is stored in the Home Assistant config entry — no Tuya cloud involvement.

> **Why does the device accept this?**
> Tuya firmware at activation time accepts *any* valid-looking activation response. It does not verify TLS certificates or validate the server's identity. The device just needs the correct JSON structure with `"success": true` and a `localKey`.

---

## Step 3 — Local TCP Control

After activation, all communication goes directly over your LAN:

- **Port:** TCP 6668
- **Discovery:** UDP broadcast on 6666/6667
- **Protocol:** Tuya LAN protocol v3.1–3.5 (AES-ECB or AES-GCM encrypted)

The `local_key` from step 2 is used to encrypt and authenticate every message. It never leaves your network.

---

## Why no Tuya account is needed

Traditional Tuya integrations (including the official HA integration) require:
1. A Tuya Cloud developer account
2. Registering your device via the Tuya app
3. Fetching the `local_key` via the cloud API

Tuya Cloudless skips all of this by intercepting the **one-time activation call** that the device makes on first boot. The device gets a locally-generated key, and that's all it ever needs for local communication.

You could factory-reset a brand new Tuya device, run BLE pairing, and have it working in Home Assistant — **without ever touching the Tuya app or cloud**.

---

## Security properties

| Property | Details |
|---|---|
| local_key generation | `secrets.token_hex(16)` — 128-bit cryptographically random |
| key storage | HA config entry (encrypted at rest by HA) |
| runtime traffic | AES-128 (v3.1–3.3) or AES-256-GCM (v3.4–3.5), LAN only |
| cloud traffic | None after activation |
| BLE window | ~30 seconds during pairing mode |

See [Security](Security) for more details.

---

## HTTPS requirement for BLE pairing

Web Bluetooth is a browser security feature that requires a **secure context** — the page must be served over HTTPS (or `localhost`).

- `http://192.168.x.x:8099` — **does not work** in Chrome/Edge
- `https://your-ha-domain:8099` — works
- `http://localhost:8099` — works (dev only)

See **[HTTPS Setup](HTTPS-Setup)** for how to configure HTTPS on your Home Assistant instance.

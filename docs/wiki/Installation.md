# Installation

## Requirements

- Home Assistant 2024.1 or later
- HACS (Home Assistant Community Store)
- Your Tuya device must already be paired via the Tuya or Smart Life app (one time only)
- Your device and Home Assistant must be on the same local network

---

## Step 1 — Install via HACS

1. Open HACS in Home Assistant
2. Click **Integrations**
3. Click the three dots (⋮) → **Custom repositories**
4. Paste: `https://github.com/11z4t/tuya-cloudless`
5. Category: **Integration**
6. Click **Add**
7. Search for **Tuya Cloudless** and click **Download**
8. Restart Home Assistant

---

## Step 2 — Find your device key

You need the **local key** for your device. This is a 16-character code that Tuya assigns to each device.

### Option A: Use tinytuya wizard (recommended)

```bash
pip install tinytuya
python3 -m tinytuya wizard
```

Follow the prompts. You will need your Tuya Cloud account credentials (this is a one-time step — the integration itself never connects to the cloud).

### Option B: Use an existing tool

- [Tuya-cli](https://github.com/TuyaAPI/cli)
- [LocalTuya](https://github.com/rospogrigio/localtuya) device scanner

---

## Step 3 — Set up the integration

See **[Setup](Setup)** for the full walkthrough.

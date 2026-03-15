# Supported Devices

Any Tuya-compatible device with a known local key is supported.

The integration uses **device profiles** to decide which controls appear in Home Assistant. If your exact device is not listed, choose the closest profile and customize it.

---

## Works out of the box

| Device type | Profile | What you get |
|---|---|---|
| Smart switch / socket | Generic Switch | On/off |
| Dimmable light bulb | Generic Light | On/off, brightness, color temperature |
| Smart plug with energy monitoring | Smart Plug | On/off, power (W), current (A), overload alert |
| Motorized roller blind / curtain | Roller Blind | Open, close, set position % |

---

## Protocol versions

| Version | Encryption | Notes |
|---|---|---|
| 3.1 | None | Old devices, plaintext |
| 3.3 | AES-ECB | Most common |
| 3.4 | AES-GCM + ECDH | Newer devices |
| 3.5 | AES-GCM + ECDH | Latest |

All versions are supported. The version is set automatically when you select it during setup.

---

## Adding support for a new device

See **[Device Profiles](Device-Profiles)** for a guide to adding your own device type.

Contributions are welcome — see **[CONTRIBUTING](https://github.com/11z4t/tuya-cloudless/blob/main/CONTRIBUTING.md)**.

# Tuya Cloudless

Control your Tuya smart home devices locally — no cloud required after the first setup.

Tuya Cloudless is a Home Assistant integration that communicates directly with your devices over your local network. Once set up, your devices work even if the internet is down.

---

## What can it do?

| What you get | How |
|---|---|
| Turn lights and switches on/off | Local TCP connection |
| Dim lights, change color temperature | Protocol v3.1–3.5 |
| Read power usage from smart plugs | Real-time DPS updates |
| Open and close roller blinds | Cover platform |
| Get alerts when a device overloads | Binary sensor platform |
| Works without internet | 100% local |

---

## Pages

- **[Installation](Installation)** — How to install in Home Assistant
- **[Setup](Setup)** — Step-by-step setup wizard walkthrough
- **[Supported Devices](Supported-Devices)** — Which devices work out of the box
- **[Device Profiles](Device-Profiles)** — How device profiles work + how to add your own
- **[Troubleshooting](Troubleshooting)** — Common problems and solutions
- **[Protocol Versions](Protocol-Versions)** — v3.1, v3.3, v3.4, v3.5 — what's the difference?
- **[Security](Security)** — How your device key is stored and used

---

## Quick start

1. Install via HACS → Custom repositories → `https://github.com/11z4t/tuya-cloudless`
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add integration → Tuya Cloudless**
4. Follow the setup wizard

See **[Setup](Setup)** for the full walkthrough.

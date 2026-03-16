# Installation

## Vad du behöver

- **Home Assistant** version 2024.4 eller senare
- **HACS** installerat (Home Assistant Community Store)
- En **Tuya WiFi-enhet** i nytt/återställt tillstånd (eller i parkopplingsläge)
- Din enhet och Home Assistant måste vara på **samma WiFi-nätverk**
- **Chrome eller Edge** — för BLE-parkopplingen (Safari och Firefox fungerar inte)
- **HTTPS på din HA** — krävs av webbläsaren för Bluetooth. Se [HTTPS Setup](HTTPS-Setup).

> **Inget Tuya-konto behövs.** Du behöver inte registrera dig hos Tuya eller logga in i Tuya-appen. Allt sker lokalt på ditt nätverk.

---

## Steg 1 — Installera via HACS

1. Öppna **HACS** i Home Assistant
2. Klicka på **Integrationer**
3. Klicka på de tre prickarna (⋮) → **Egna förråd**
4. Klistra in: `https://github.com/11z4t/tuya-cloudless`
5. Kategori: **Integration**
6. Klicka **Lägg till**
7. Sök efter **Tuya Cloudless** och klicka **Ladda ner**
8. Starta om Home Assistant

---

## Steg 2 — Lägg till din enhet

1. Gå till **Inställningar → Enheter och tjänster → Lägg till integration**
2. Sök efter **Tuya Cloudless**
3. Välj **BLE Parkoppla** (rekommenderat för nya enheter)
4. Följ guiden — se **[Setup](Setup)** för en detaljerad genomgång

Det är allt. Inget konto, inga API-nycklar, ingen molnanslutning.

---

## English

## What you need

- **Home Assistant** 2024.4 or later
- **HACS** installed
- A **Tuya WiFi device** in pairing mode (new or factory reset)
- Your device and Home Assistant on the **same WiFi network**
- **Chrome or Edge** — required for BLE pairing (Safari and Firefox don't work)
- **HTTPS on your HA** — required by the browser for Bluetooth. See [HTTPS Setup](HTTPS-Setup).

> **No Tuya account required.** You don't need to sign up with Tuya or use the Tuya app. Everything happens on your local network.

---

## Step 1 — Install via HACS

1. Open **HACS** in Home Assistant
2. Click **Integrations**
3. Click the three dots (⋮) → **Custom repositories**
4. Paste: `https://github.com/11z4t/tuya-cloudless`
5. Category: **Integration**
6. Click **Add**
7. Search for **Tuya Cloudless** and click **Download**
8. Restart Home Assistant

---

## Step 2 — Add your device

1. Go to **Settings → Devices & Services → Add integration**
2. Search for **Tuya Cloudless**
3. Choose **BLE Pairing** (recommended for new devices)
4. Follow the wizard — see **[Setup](Setup)** for a detailed walkthrough

That's it. No account, no API keys, no cloud.

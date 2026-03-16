# Tuya Cloudless

Styr dina Tuya-enheter direkt från Home Assistant — **utan molnkonto, utan Tuya-app, utan att data lämnar ditt hem**.

Control your Tuya devices directly from Home Assistant — **no cloud account, no Tuya app, no data leaves your home**.

---

## Vad kan det göra? / What can it do?

| Funktion | Hur |
|---|---|
| Tänd/släck lampor och strömbrytare | Lokal TCP-anslutning |
| Dimra lampor, ändra färgtemperatur | Protokoll v3.1–3.5 |
| Läs av strömförbrukning | Realtidsuppdateringar |
| Öppna och stäng persienner | Cover-plattformen |
| Funkar utan internet | 100% lokalt |

---

## Sidor / Pages

### Kom igång / Getting started
- **[Installation](Installation)** — Installera i Home Assistant / Install in Home Assistant
- **[Setup](Setup)** — Steg-för-steg: lägg till din enhet / Step-by-step: add your device
- **[HTTPS Setup](HTTPS-Setup)** — Krävs för BLE-parkopplingen / Required for BLE pairing

### Lär dig mer / Learn more
- **[How It Works](How-It-Works)** — Teknisk förklaring av BLE, fake-cloud och lokal nyckel / Technical explanation
- **[Supported Devices](Supported-Devices)** — Vilka enheter stöds / Which devices are supported
- **[Device Profiles](Device-Profiles)** — Lägg till egna enhetsprofiler / Add your own device profiles
- **[Protocol Versions](Protocol-Versions)** — v3.1, v3.3, v3.4, v3.5 — vad är skillnaden?
- **[Security](Security)** — Hur din enhetsnyckel lagras och används / How your device key is stored

### Hjälp / Help
- **[Troubleshooting](Troubleshooting)** — Vanliga problem och lösningar / Common problems and solutions

---

## Snabbstart / Quick start

1. Installera via HACS → Egna förråd → `https://github.com/11z4t/tuya-cloudless`
2. Starta om Home Assistant
3. Gå till **Inställningar → Enheter och tjänster → Lägg till integration → Tuya Cloudless**
4. Välj **BLE Parkoppling** och följ guiden

Se **[Setup](Setup)** för en fullständig genomgång.

---

## Varför inget molnkonto? / Why no cloud account?

Tuya-enheter har en inbyggd aktiveringsmekanism: när de ansluts till WiFi första gången anropar de en URL för att registrera sig. Normalt pekar den URL:en på Tuyas servrar.

Tuya Cloudless pekar om den URL:en till din **lokala Home Assistant** istället. HA svarar med en giltig aktiveringssignal och en slumpmässigt genererad lokal nyckel. Enheten lagrar nyckeln och använder den för all framtida kommunikation — direkt mot HA, utan mellanhänder.

**Du har aldrig behövt berätta för Tuya att enheten existerar.**

---

Tuya devices have a built-in activation mechanism: when first connected to WiFi, they call a URL to register themselves. Normally that URL points to Tuya's servers.

Tuya Cloudless redirects that URL to your **local Home Assistant** instead. HA responds with a valid activation response and a randomly generated local key. The device stores the key and uses it for all future communication — directly to HA, no middleman.

**You never had to tell Tuya the device exists.**

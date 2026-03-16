# Setup — Lägg till en enhet

Gå till **Inställningar → Enheter och tjänster → Lägg till integration** och sök på **Tuya Cloudless**.

---

## Välj metod

Du får tre alternativ:

| Alternativ | När ska du använda det? |
|---|---|
| **BLE Parkoppling** | Ny enhet eller fabriksåterställd enhet — **rekommenderas** |
| **Sök nätverk** | Enheten är redan på WiFi (t.ex. inställd via Tuya-appen) |
| **Manuell inmatning** | Du har enhetens IP och nyckel och vill skriva in dem själv |

---

## BLE Parkoppla (rekommenderat)

Det här alternativet parkopplar enheten utan Tuya-app och utan molnkonto.

### Förberedelser

1. **Fabriksåterställ din Tuya-enhet** — håll in knappen tills lampan blinkar snabbt (se enhetens manual)
2. Kontrollera att du använder **Chrome eller Edge** — inte Safari eller Firefox
3. Kontrollera att du är inloggad i HA via **HTTPS** (adressen ska börja med `https://`). Se [HTTPS Setup](HTTPS-Setup) om du är osäker.

### Genomgång

**Steg 1 — HA öppnar parkopplingsverktyget**

När du väljer BLE Parkoppla öppnar Home Assistant automatiskt en webbsida med parkopplingsverktyget. Det tar en sekund.

**Steg 2 — Ange WiFi-uppgifter**

Fyll i ditt WiFi-nätverksnamn (SSID) och lösenord. Det är WiFi:t som enheten ska ansluta till — vanligtvis ditt hemnätverk.

> Enheten måste vara på **samma nätverk** som Home Assistant för att fungera efter parkopplingen.

**Steg 3 — Skanna och parkoppla**

Klicka **Sök & Parkoppla**. Webbläsaren öppnar en Bluetooth-dialog och letar efter din Tuya-enhet.

- Välj din enhet i listan
- Webbläsaren skickar WiFi-uppgifterna till enheten via Bluetooth
- Enheten startar om och ansluter till WiFi

**Steg 4 — Enheten aktiveras**

När enheten ansluter till WiFi skickar den en aktiveringsbegäran till din Home Assistant. Tuya Cloudless svarar och genererar en lokal nyckel (allt sker på ditt eget nätverk — ingenting skickas till Tuya).

Parkopplingsverktyget visar "Enhet aktiverad ✓".

**Steg 5 — Ge enheten ett namn**

Tillbaka i HA-guiden får du ange ett namn (t.ex. "Vardagsrumslampa") och välja enhetstyp (profiltyp).

Klicka **Skicka** — enheten läggs till i Home Assistant!

---

## Sök nätverk

Välj det här om din enhet redan är ansluten till WiFi.

1. Integreringen söker av nätverket i 5 sekunder
2. Välj din enhet i listan
3. Ange den lokala nyckeln (om du inte har den, använd BLE Parkoppling istället)

---

## Manuell inmatning

Fyll i:
- **Enhets-ID** — unikt ID som börjar med `bf` eller `eb`
- **Säkerhetsnyckel** — 16 tecken, lokal nyckel
- **IP-adress** — enhetens IP på ditt nätverk

---

## Efter installation

Din enhet syns nu under **Inställningar → Enheter och tjänster → Tuya Cloudless**.

Öppna enheten för att se alla entiteter (strömbrytare, sensorer, m.m.) och för att konfigurera inställningar.

---

## Om enheten slutar svara

Se **[Troubleshooting](Troubleshooting)**.

---

---

# Setup — Add a device (English)

Go to **Settings → Devices & Services → Add integration** and search for **Tuya Cloudless**.

---

## Choose a method

| Method | When to use |
|---|---|
| **BLE Pairing** | New device or factory-reset device — **recommended** |
| **Search network** | Device is already on WiFi (e.g. set up via Tuya app) |
| **Manual entry** | You already have the device IP and key |

---

## BLE Pairing (recommended)

This pairs the device without the Tuya app and without a cloud account.

### Before you start

1. **Factory reset your Tuya device** — hold the button until the light flashes rapidly (see the device manual)
2. Use **Chrome or Edge** — not Safari or Firefox
3. Make sure you're logged into HA via **HTTPS** (the address must start with `https://`). See [HTTPS Setup](HTTPS-Setup).

### Walkthrough

**Step 1 — HA opens the pairing tool**

When you choose BLE Pairing, Home Assistant automatically opens the pairing tool in your browser. This takes a moment.

**Step 2 — Enter WiFi credentials**

Enter the name (SSID) and password of the WiFi network the device should join — usually your home network.

> The device must be on the **same network** as Home Assistant to work after pairing.

**Step 3 — Scan and pair**

Click **Scan & Pair**. Your browser opens a Bluetooth picker and scans for your Tuya device.

- Select your device from the list
- The browser sends the WiFi credentials to the device over Bluetooth
- The device reboots and connects to WiFi

**Step 4 — The device activates**

When the device joins WiFi, it sends an activation request to your Home Assistant. Tuya Cloudless responds and generates a local key — everything happens on your local network, nothing is sent to Tuya.

The pairing tool shows "Device activated ✓".

**Step 5 — Name your device**

Back in the HA wizard, enter a name (e.g. "Living room lamp") and choose a device profile.

Click **Submit** — the device is added to Home Assistant!

---

## Search network

Use this if your device is already connected to WiFi.

1. The integration scans your network for 5 seconds
2. Select your device from the list
3. Enter the local key (if you don't have it, use BLE Pairing instead)

---

## Manual entry

Fill in:
- **Device ID** — unique ID starting with `bf` or `eb`
- **Security key** — 16-character local key
- **IP address** — the device's IP on your network

---

## After setup

Your device now appears under **Settings → Devices & Services → Tuya Cloudless**.

Open the device to see all entities (switches, sensors, etc.) and to configure settings.

---

## If the device stops responding

See **[Troubleshooting](Troubleshooting)**.

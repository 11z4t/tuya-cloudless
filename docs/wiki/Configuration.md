# Konfigurationsparametrar

Dessa parametrar ställs in när du lägger till en enhet (konfigurationsflödet) samt i enhetsinställningarna efteråt.

---

## Inställningar vid tillägg (config flow)

| Parameter | Beskrivning | Exempel |
|-----------|-------------|---------|
| **Enhetsnamn** | Ett valfritt namn för enheten i Home Assistant | `Vardagsrumslampa` |
| **Gateway-ID** | Enhetens unika ID — börjar vanligtvis med `bf` eller `eb`. Sätts automatiskt vid BLE-parkopplingen. | `bf1234567890abcd` |
| **Lokal nyckel** | 16-teckens nyckel för lokal kryptering. Genereras automatiskt vid BLE-parkopplingen. | `a1b2c3d4e5f60000` |
| **IP-adress** | Enhetens IP på ditt nätverk. Identifieras automatiskt vid nätverkssökning. | `192.168.1.100` |
| **Protokollversion** | Tuya-protokollversionen för enheten. Välj `auto` om du är osäker — integreringen försöker identifiera versionen automatiskt. | `3.3`, `3.4`, `3.5`, `auto` |
| **Profil** | Enhetsprofil som avgör vilka entiteter som skapas (strömbrytare, sensor, ljus, m.m.). | `Generic Switch`, `Siren / Alarm` |

---

## Avancerade inställningar (options)

Dessa kan ändras efter att enheten lagts till via **Inställningar → Enheter och tjänster → Tuya Cloudless → Konfigurera**.

| Parameter | Standardvärde | Beskrivning |
|-----------|--------------|-------------|
| **Hjärtslagsintervall** | 20 sekunder | Hur ofta integreringen skickar ett keepalive till enheten. |
| **Kommandotimeout** | 10 sekunder | Hur länge integreringen väntar på svar från enheten. |
| **Max återkopplingsintervall** | 300 sekunder | Maximalt väntetid mellan återkopplingsförsök vid frånkoppling. |

---

# Configuration parameters (English)

These parameters are set when adding a device (config flow) and in the device settings afterwards.

---

## Setup parameters (config flow)

| Parameter | Description | Example |
|-----------|-------------|---------|
| **Device name** | A friendly name for the device in Home Assistant | `Living room lamp` |
| **Gateway ID** | The device's unique identifier — usually starts with `bf` or `eb`. Set automatically during BLE pairing. | `bf1234567890abcd` |
| **Local key** | 16-character key for local encryption. Generated automatically during BLE pairing. | `a1b2c3d4e5f60000` |
| **IP address** | The device's IP on your network. Detected automatically during network search. | `192.168.1.100` |
| **Protocol version** | The Tuya protocol version for the device. Choose `auto` if unsure — the integration will attempt to detect it automatically. | `3.3`, `3.4`, `3.5`, `auto` |
| **Profile** | Device profile that determines which entities are created (switch, sensor, light, etc.). | `Generic Switch`, `Siren / Alarm` |

---

## Advanced settings (options)

These can be changed after adding the device via **Settings → Devices & Services → Tuya Cloudless → Configure**.

| Parameter | Default | Description |
|-----------|---------|-------------|
| **Heartbeat interval** | 20 seconds | How often the integration sends a keepalive to the device. |
| **Command timeout** | 10 seconds | How long the integration waits for a response from the device. |
| **Max reconnect delay** | 300 seconds | Maximum wait time between reconnection attempts when disconnected. |

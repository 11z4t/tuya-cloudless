# BLE Provisioning — Hardware Test Guide

Denna guide beskriver hur du testar BLE-provisioneringen mot en riktig Tuya-enhet.

## Förutsättningar

- Home Assistant med Tuya Cloudless installerad
- En Tuya WiFi+BLE-enhet (t.ex. smart plug, strömbrytare) i fabriksläge
- Chrome eller Edge (Web Bluetooth krävs)
- HA och Tuya-enheten på samma WiFi-nät

## Steg 1 — Starta HA och verifiera pairingsservern

```bash
# Kontrollera att port 8099 lyssnar (från HA-värdens terminal)
ss -tlnp | grep 8099

# Förväntad output:
# LISTEN 0 128 0.0.0.0:8099 ...
```

## Steg 2 — Öppna parningsverktyget

Öppna Chrome/Edge och navigera till:

```
http://<HA-IP>:8099
```

Exempel: `http://192.168.5.22:8099`

## Steg 3 — Fabriksåterställ enheten

Håll in parkopplingsknappen tills indikatorn blinkar **snabbt** (2-3 blink/s).
Enheten är nu i BLE-provisioneringsläge.

## Steg 4 — Para ihop

1. Fyll i **SSID** och **lösenord** för ditt WiFi
2. Klicka **Scan & Pair**
3. Chrome visar en dialogruta med Bluetooth-enheter — välj din Tuya-enhet
4. Vänta ~15 sekunder

## Förväntat förlopp

```
Handshake…
Sending WiFi credentials…
Credentials sent ✓ — waiting for device to activate…
[SSE-event mottas]
✓ Device activated! Local key generated.
```

Sedan visas:
- **Device ID** — enhetens gw_id
- **IP-adress** — IP enheten fick från DHCP
- **Local key** — 32-teckens hex-nyckel
- Knapp: **Add to Home Assistant**

## Steg 5 — Lägg till i HA

Klicka **Add to Home Assistant** — config flow öppnas med förifyllda uppgifter.
Klicka **Anslut** för att verifiera och spara.

## Felsökning

### "Bluetooth scan cancelled"
Enheten är inte i provisioneringsläge. Gör fabriksåterställning igen.

### "No Tuya BLE device found"
- Enheten kanske inte stöder service UUID `0000fd50-...`
- Prova att hålla enheten närmare datorn (< 1 m)
- Kontrollera att BLE är aktiverat i Chrome: `chrome://flags/#enable-web-bluetooth`

### "BLE response timeout" på handshake
Enheten svarade inte på handshake. Möjliga orsaker:
- Enheten använder ett annat BLE-protokoll (äldre Tuya-firmware)
- Prova fabriksåterställning igen

### Aktivering startar men local_key visas aldrig
Enheten anslöt till WiFi men nådde inte pairingsservern.
Kontrollera att HA-hosten (port 8099) är nåbar från WiFi-nätverket:

```bash
# Från en annan dator på samma WiFi:
curl http://<HA-IP>:8099/api/provision/result/test
# Förväntad: {"status":"pending"}
```

### Enheten POSTar till rätt URL?
Kolla HA-loggen:
```
grep "Tuya device activation" /config/home-assistant.log
```

## Loggar att samla vid fel

1. Chrome DevTools Console (F12) — hela flödet
2. HA-loggen: `grep -i "tuya.*pairing\|activation" /config/home-assistant.log`
3. Aktiveringsflöde i pairingsservern: DEBUG-nivå i HA

## Kända begränsningar

| Begränsning | Status |
|-------------|--------|
| Web Bluetooth kräver Chrome/Edge | Permanent |
| Mobil kräver HTTPS | Planerat (HTTPS-proxy) |
| Endast Tuya BLE prov. protokoll v4 | Äldre firmware kan sakna stöd |
| bleak ej installerat = ingen Python-path | Kräver `pip install bleak` |

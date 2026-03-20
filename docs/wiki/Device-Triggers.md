# Enhetstriggrar

Tuya Cloudless registrerar enhetstriggrar som du kan använda i automatiseringar.

---

## Tillgängliga triggrar

| Trigger | Händelse | Beskrivning |
|---------|----------|-------------|
| `connected` | `tuya_cloudless_connected` | Enheten ansluter till Home Assistant (TCP-förbindelsen upprättas) |
| `disconnected` | `tuya_cloudless_disconnected` | Enheten kopplas från (timeout, nätverksfel eller omstart) |
| `dp_changed` | `tuya_cloudless_dp_changed` | En Data Point (DP) på enheten ändras |

---

## Använda en trigger i en automatisering

### Via gränssnittet

1. Gå till **Inställningar → Automatiseringar och scener**
2. Klicka **Skapa automatisering**
3. Under **Utlösare**, klicka **Lägg till utlösare**
4. Välj **Enhet**
5. Välj din Tuya Cloudless-enhet
6. Välj en av triggertyperna (`connected`, `disconnected`, `dp_changed`)

### Via YAML

```yaml
trigger:
  - platform: device
    domain: tuya_cloudless
    device_id: !input device_id
    type: connected   # eller: disconnected, dp_changed
```

---

## Exempel — Avisering vid frånkoppling

```yaml
automation:
  alias: "Tuya-enhet tappade anslutning"
  trigger:
    - platform: device
      domain: tuya_cloudless
      device_id: "abc123"
      type: disconnected
  action:
    - service: notify.mobile_app
      data:
        message: "Enheten har tappat anslutningen!"
```

---

# Device triggers (English)

Tuya Cloudless registers device triggers that you can use in automations.

---

## Available triggers

| Trigger | Event | Description |
|---------|-------|-------------|
| `connected` | `tuya_cloudless_connected` | The device connects to Home Assistant (TCP connection established) |
| `disconnected` | `tuya_cloudless_disconnected` | The device disconnects (timeout, network error, or restart) |
| `dp_changed` | `tuya_cloudless_dp_changed` | A Data Point (DP) on the device changes |

---

## Using a trigger in an automation

### Via the UI

1. Go to **Settings → Automations & Scenes**
2. Click **Create automation**
3. Under **Trigger**, click **Add trigger**
4. Select **Device**
5. Select your Tuya Cloudless device
6. Choose one of the trigger types (`connected`, `disconnected`, `dp_changed`)

### Via YAML

```yaml
trigger:
  - platform: device
    domain: tuya_cloudless
    device_id: !input device_id
    type: connected   # or: disconnected, dp_changed
```

---

## Example — Notify on disconnect

```yaml
automation:
  alias: "Tuya device lost connection"
  trigger:
    - platform: device
      domain: tuya_cloudless
      device_id: "abc123"
      type: disconnected
  action:
    - service: notify.mobile_app
      data:
        message: "The device has lost its connection!"
```

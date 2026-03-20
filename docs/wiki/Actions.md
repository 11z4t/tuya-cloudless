# Actions (tjänster)

Tuya Cloudless erbjuder en tjänst (action) för avancerad styrning av enheter.

---

## `tuya_cloudless.send_raw_dps`

Skickar ett eller flera rå Data Point-värden (DPS) direkt till en Tuya-enhet.

Använd detta för att styra DP:er som inte är exponerade som entiteter, t.ex. för enheter som saknar en färdig profil.

### Parametrar

| Parameter | Typ | Beskrivning |
|-----------|-----|-------------|
| `entry_id` | string | Konfigurations-ID för målenheten. Syns i enhetsinformation under Inställningar → Enheter och tjänster. |
| `dps` | object | Dictionary med DP-ID:n (strängar) som nycklar och önskade värden. Exempel: `{"1": true, "19": 500}`. Värden kan vara `bool`, `int` eller `str`. |

### Exempel

```yaml
service: tuya_cloudless.send_raw_dps
data:
  entry_id: "abc123def456"
  dps:
    "1": true
    "19": 500
```

### Hitta entry_id

1. Gå till **Inställningar → Enheter och tjänster → Tuya Cloudless**
2. Klicka på enheten
3. Klicka på **"..."** → **Enhetsinformation**
4. Kopiera värdet under **Konfigurations-ID**

---

# Actions (English)

Tuya Cloudless provides one action (service) for advanced device control.

---

## `tuya_cloudless.send_raw_dps`

Sends one or more raw Data Point (DP) values directly to a Tuya device.

Use this for advanced control of DPs not exposed as entities, for example for devices without a matching profile.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `entry_id` | string | The config entry ID of the target device. Visible in device info under Settings → Devices & Services. |
| `dps` | object | Dictionary with DP IDs (strings) as keys and desired values. Example: `{"1": true, "19": 500}`. Values can be `bool`, `int`, or `str`. |

### Example

```yaml
service: tuya_cloudless.send_raw_dps
data:
  entry_id: "abc123def456"
  dps:
    "1": true
    "19": 500
```

### Finding the entry_id

1. Go to **Settings → Devices & Services → Tuya Cloudless**
2. Click on the device
3. Click **"..."** → **Device info**
4. Copy the value under **Configuration entry ID**

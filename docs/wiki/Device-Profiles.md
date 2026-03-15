# Device Profiles

A device profile tells the integration which controls to create in Home Assistant for your device.

Profiles are YAML files in the `profiles/` directory.

---

## Built-in profiles

| Profile | Entities created |
|---|---|
| **Generic Switch** | 1 switch (on/off) |
| **Generic Light** | 1 light (on/off, dim, color temperature) |
| **Smart Plug** | 1 switch, power sensor, current sensor, overload alert |
| **Roller Blind** | 1 cover (open, close, set position) |

---

## How to add a new profile

Create a new file in the `profiles/` directory — for example `profiles/my_device.yaml`.

### Minimal example (switch)

```yaml
name: "My Switch"
model: "abc*"
entities:
  - platform: switch
    name: main_switch
    dp_power: {id: "1", type: bool}
```

### Full example (smart plug with power monitoring)

```yaml
name: "Smart Plug"
model: "sp*"
entities:
  - platform: switch
    name: main_switch
    dp_power: {id: "1", type: bool}

  - platform: sensor
    name: power_consumption
    dp_value: {id: "19", type: int, scale: 0.1}
    device_class: power
    unit: W
    state_class: measurement

  - platform: binary_sensor
    name: overload
    dp_power: {id: "26", type: bool}
    device_class: problem
```

---

## DP field types

| Type | Use for |
|---|---|
| `bool` | On/off values |
| `int` | Numbers — use `scale` to convert (e.g. `scale: 0.1` for values like 230.0) |
| `str` | Text values |
| `enum` | Fixed options (e.g. `"white"`, `"colour"`) |
| `raw` | Raw bytes |

---

## Finding DP IDs for your device

Use the [tinytuya wizard](https://github.com/jasonacox/tinytuya) to scan your device and list its data points:

```bash
python3 -m tinytuya scan
```

Each data point has an ID (like `"1"`, `"19"`) and a type. Match these to the fields in your profile.

---

## Testing your profile

After creating the file, restart Home Assistant and add the device again — or go to **Settings → Devices & Services → Tuya Cloudless → [your device] → Reconfigure** and pick the new profile.

You can also test from the command line:

```bash
python3 -c "
from tuya_cloudless.profiles import load_profile
from pathlib import Path
p = load_profile(Path('profiles/my_device.yaml'))
print(f'Profile: {p.name}')
for e in p.entities:
    print(f'  {e.platform}: {e.name}')
"
```

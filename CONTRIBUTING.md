# Contributing to Tuya Cloudless

Thank you for your interest in contributing!
This guide covers how to run the checks, add support for a new device, and submit a pull request.

---

## Getting started

```bash
git clone git@192.168.5.40:4recon/tuya-cloudless.git
cd tuya-cloudless
pip install -e ".[test]"
```

Run all checks (must pass before committing):

```bash
bash scripts/run-checks.sh
```

This runs: `ruff`, `mypy --strict`, `bandit`, `detect-secrets`, and `pytest`.

---

## Adding a new device profile

Device profiles are YAML files in the `profiles/` directory.
Each file describes which controls appear in Home Assistant for that device type.

**1. Create a new file** — e.g. `profiles/my_device.yaml`:

```yaml
name: "My Device Name"   # Shown in the setup wizard
model: "abc*"            # Glob pattern matched against the product key; use "*" to match any

entities:
  # Switch (on/off)
  - platform: switch
    name: main_switch
    dp_power: {id: "1", type: bool}

  # Measurement sensor
  - platform: sensor
    name: power_consumption
    dp_value: {id: "19", type: int, scale: 0.1}
    device_class: power
    unit: W
    state_class: measurement

  # Alert sensor (true = problem active)
  - platform: binary_sensor
    name: overload
    dp_power: {id: "26", type: bool}
    device_class: problem

  # Dimmable light
  - platform: light
    name: main_light
    dp_power: {id: "1", type: bool}
    dp_brightness: {id: "3", type: int, min_raw: 10, max_raw: 1000}
    dp_color_temp: {id: "4", type: int, min_raw: 0, max_raw: 1000}

  # Cover / blind
  - platform: cover
    name: cover
    dp_open: {id: "1", type: bool}
    dp_position: {id: "2", type: int, min_raw: 0, max_raw: 100}
    device_class: blind
```

**DP field types:**

| Type    | Description                                     |
|---------|-------------------------------------------------|
| `bool`  | True / False (on/off)                           |
| `int`   | Integer — use `scale` to convert to real value  |
| `str`   | String value                                    |
| `enum`  | Enum string (e.g. `"white"`, `"colour"`)        |
| `raw`   | Raw bytes                                       |

**2. Test that the profile loads correctly:**

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

**3. Run the full test suite:**

```bash
bash scripts/run-checks.sh
python3 -m pytest tests/ -q
```

---

## Commit message format

Use these prefixes to keep the history readable:

| Prefix       | When to use                                   |
|--------------|-----------------------------------------------|
| `feat:`      | New feature (new platform, new profile, etc.) |
| `fix:`       | Bug fix                                       |
| `security:`  | Security-related change                       |
| `test:`      | Adding or fixing tests                        |
| `docs:`      | Documentation only                            |
| `refactor:`  | Code cleanup without behaviour change         |
| `chore:`     | Build, CI, or dependency updates              |

Example: `feat: add roller blind cover profile`

---

## Pull request checklist

Before opening a pull request, verify:

- [ ] `bash scripts/run-checks.sh` passes with 0 errors
- [ ] `python3 -m pytest tests/ -q` passes with 0 failures
- [ ] `python3 -m ruff check lib/ custom_components/` shows 0 errors
- [ ] `python3 -m mypy --strict lib/ custom_components/` shows 0 errors
- [ ] If you added a profile: the YAML loads correctly (see command above)
- [ ] New code has type hints on all public methods
- [ ] No secrets, keys, or credentials anywhere in the code or tests

---

## Project structure

```
tuya-cloudless/
├── lib/tuya_cloudless/        Pure Python library (no HA imports)
│   ├── crypto.py              Encryption (AES-ECB, AES-GCM, ECDH)
│   ├── protocol.py            Frame encode/decode
│   ├── discovery.py           UDP device discovery
│   ├── profiles.py            YAML profile loader
│   └── exceptions.py          Custom exceptions
├── custom_components/tuya_cloudless/   Home Assistant integration
│   ├── __init__.py            Entry setup + profile resolution
│   ├── coordinator.py         TCP connection + DPS state management
│   ├── config_flow.py         Setup wizard + re-authentication
│   ├── diagnostics.py         HA diagnostics export
│   ├── switch.py              Switch platform
│   ├── light.py               Light platform
│   ├── sensor.py              Sensor platform
│   ├── binary_sensor.py       Binary sensor platform
│   ├── cover.py               Cover platform
│   └── translations/          UI translations (EN, SV, DE, FR, ...)
├── profiles/                  Device YAML profiles (add new devices here)
├── tests/
│   ├── unit/                  Unit tests (no network, no HA)
│   ├── integration/           Integration tests (loopback TCP)
│   └── helpers/               Test helpers (FakeTuyaDevice, etc.)
└── scripts/run-checks.sh      Full validation pipeline
```

---

## Security

Please report security issues privately — do not open a public GitHub issue.
See [SECURITY.md](SECURITY.md) for the disclosure process.

# Contributing to Tuya Cloudless

Thank you for your interest in contributing! This project follows premium quality standards.

## Development Setup

```bash
# Clone repository
git clone https://git.malmgrens.me/4recon/tuya-cloudless.git
cd tuya-cloudless

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .
pip install -r requirements-dev.txt
```

## Code Quality Standards

### 1. Type Hints — MANDATORY

All public methods and functions MUST have type hints:

```python
# ✅ CORRECT
async def send_command(self, device_id: str, command: dict[str, Any]) -> bool:
    """Send command to device."""
    ...

# ❌ WRONG
async def send_command(self, device_id, command):
    ...
```

### 2. Async/Await — EVERYWHERE

All I/O operations MUST use `asyncio`:

```python
# ✅ CORRECT
async def discover_devices(self) -> list[TuyaDevice]:
    async with aiohttp.ClientSession() as session:
        ...

# ❌ WRONG
def discover_devices(self):
    response = requests.get(...)
```

### 3. Custom Exceptions — ONLY

Use custom exceptions defined in `lib/tuya_cloudless/exceptions.py`:

```python
# ✅ CORRECT
raise TuyaProtocolError(f"Invalid protocol version: {version}")

# ❌ WRONG
raise Exception("Invalid protocol version")
raise ValueError("Invalid protocol version")
```

### 4. Library Isolation

Keep protocol logic in `lib/` separate from Home Assistant integration:

```
lib/tuya_cloudless/          # Pure Python, HA-agnostic
├── protocol.py              # Protocol parser
├── crypto.py                # Encryption/decryption
├── discovery.py             # Device discovery
└── exceptions.py            # Custom exceptions

custom_components/tuya_cloudless/  # HA-specific
├── __init__.py              # Integration setup
├── config_flow.py           # Configuration UI
├── light.py                 # Light entities
└── switch.py                # Switch entities
```

### 5. Testing — 80%+ Coverage

All new code MUST have tests:

```bash
# Run tests
pytest

# Check coverage
pytest --cov=lib --cov=custom_components

# Minimum 80% coverage required
```

### 6. Linting — Zero Errors

```bash
# Format code
black .

# Lint code
ruff check .

# Type checking
mypy lib/ custom_components/
```

## Commit Messages

Follow conventional commits:

```
feat: add support for RGB lights
fix: correct AES-GCM decryption for v3.5
docs: update installation instructions
test: add protocol parser tests
refactor: extract crypto functions to separate module
```

## Pull Request Process

1. **Create feature branch**: `git checkout -b feature/my-feature`
2. **Write tests** for your changes
3. **Run all checks**: `pytest && ruff check . && mypy lib/ custom_components/`
4. **Update documentation** if needed
5. **Commit and push**: `git push origin feature/my-feature`
6. **Create pull request** with clear description

## Code Review Checklist

Before submitting, verify:

- [ ] All tests pass (`pytest`)
- [ ] Code coverage ≥ 80%
- [ ] No linting errors (`ruff check .`)
- [ ] Type hints on all public methods (`mypy`)
- [ ] Custom exceptions only (no `Exception`, `ValueError`, etc.)
- [ ] Async/await for all I/O
- [ ] English + Swedish translations updated
- [ ] Documentation updated

## Translation Files

When adding new strings, update BOTH files:

```
custom_components/tuya_cloudless/translations/
├── en.json    # English (required)
└── sv.json    # Swedish (required)
```

Example:

```json
// en.json
{
  "config": {
    "step": {
      "user": {
        "title": "Connect Tuya Device",
        "description": "Enter device IP and local key"
      }
    }
  }
}

// sv.json
{
  "config": {
    "step": {
      "user": {
        "title": "Anslut Tuya-enhet",
        "description": "Ange enhetens IP och lokala nyckel"
      }
    }
  }
}
```

## Questions?

Open an issue or discussion on [Gitea](https://git.malmgrens.me/4recon/tuya-cloudless/issues).

---

**Remember:** This is a premium integration. Quality over speed. 10/10 or nothing.

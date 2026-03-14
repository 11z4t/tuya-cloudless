# Tuya Cloudless — Claude Code Project Context

> **STOP. READ THIS ENTIRE FILE BEFORE WRITING ANY CODE.**
> This file defines the rules you MUST follow. No shortcuts. No exceptions.

---

## SUPREME SECURITY RULES

### RULE 0: NEVER EXPOSE SECRETS IN TERMINAL OUTPUT

You (Claude Code) send terminal output back to an AI API as prompt context.
**ANYTHING visible in your terminal IS INCLUDED IN THE AI PROMPT.**

You MUST NEVER run commands that display secrets:

```bash
# FORBIDDEN — these leak secrets to AI context:
cat .env
echo $API_KEY
env | grep KEY
op item get "vault" --fields password

# SAFE — verify existence without showing value:
test -f .env && echo "EXISTS" || echo "MISSING"
[ -n "$API_KEY" ] && echo "SET" || echo "NOT SET"
op read "op://vault/item/field" > /dev/null 2>&1 && echo "OK" || echo "FAIL"
```

### RULE 1: ALL SECRETS VIA 1PASSWORD

1Password is the ONLY source of secrets. Never read secrets from files, env
vars, or hardcoded values in production code.

### RULE 2: NEVER LOG/PRINT SECRETS IN CODE

```python
# FORBIDDEN at every log level, even in tests:
_LOGGER.debug("Key: %s", key)
print(token)

# ONLY acceptable pattern:
_LOGGER.debug("Key [REDACTED], length: %d", len(key))
```

---

## STRUCTURAL RULES

Before creating ANY file or function, verify:

1. **S1:** `lib/` NEVER imports `homeassistant` or `custom_components`
2. **S2:** Network I/O ONLY in `protocol.py` — all Tuya protocol communication
3. **S3:** Crypto operations ONLY in `crypto.py`
4. **S4:** Async everywhere. No `time.sleep()`, no blocking I/O
5. **S5:** Type hints on EVERY function — parameters AND return type
6. **S6:** Custom exceptions only — never bare Exception/ValueError
7. **S7:** New devices via YAML profiles, not code changes
8. **S8:** Secrets accessed ONLY through proper secret management
9. **S9:** Headless design. No `input()`. No interactive prompts.

---

## BEFORE EVERY COMMIT

```bash
bash scripts/run-checks.sh   # ALL must pass. No exceptions.
```

Runs: ruff, mypy --strict, bandit, detect-secrets, pytest.
ANY failure -> fix before committing.

---

## PROJECT OVERVIEW

HACS-compatible HA integration for cloud-free local control of Tuya WiFi devices.
Communicates directly with devices on local network using Tuya Local protocol.
No Tuya Cloud dependency after initial device pairing.

### Key Documentation
- `docs/ARCHITECTURE.md` — Architecture + structural rules
- `docs/SECURITY.md` — Security rules
- `docs/DOMAIN.md` — Tuya Local protocol knowledge

### Code Structure
- `lib/tuya_cloudless/` — Standalone local control library (NO HA imports)
- `custom_components/tuya_cloudless/` — HA integration wrapper
- `tests/` — Unit + integration + security tests
- `profiles/` — Device YAML profiles

### Quick Start
```bash
pip install -e ".[test]"
bash scripts/run-checks.sh      # Full validation pipeline
pytest tests/unit/ -v            # Unit tests only
```

### Commits
Small, tested, working. `feat:`, `fix:`, `test:`, `security:`, `docs:`

# Security — Tuya Cloudless

## Section 0: Absolute Rules

1. **No secrets in terminal output** — Claude Code sends terminal output to AI APIs
2. **1Password is the ONLY secret store** — No .env files, no hardcoded values
3. **Never log secrets** — Use `[REDACTED]` placeholders
4. **Custom exceptions only** — No bare `Exception` or `ValueError`
5. **No secrets in git history** — Pre-commit scanning with detect-secrets

## Crypto

- Tuya Local protocol uses AES-ECB/CBC for device communication
- MD5 used for protocol checksums (not security-critical)
- All crypto operations isolated in `lib/tuya_cloudless/crypto.py`

## Local Key Security

- Device local keys are sensitive — they grant full device control
- Keys must be stored in 1Password or HA's built-in credential storage
- Never log or display local keys

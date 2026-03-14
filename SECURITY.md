# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✓         |

## Reporting a vulnerability

Report security issues to **security@zimpligo.se** (not public GitHub issues).

Expected response: acknowledgement within 48 hours, patch within 14 days for critical issues.

## Security design principles

1. **Key isolation** — All cryptographic key material lives exclusively in HA's encrypted config entry store. Keys are never written to log files or included in exception messages.
2. **No cloud egress** — After the one-time activation mock, no traffic is sent to Tuya servers.
3. **Constant-time comparison** — Tag/MAC verification uses `hmac.compare_digest` to prevent timing attacks.
4. **Input validation** — All packet payloads are length-checked before parsing. Oversized payloads are rejected before decryption.
5. **Exception safety** — Custom exception hierarchy ensures error messages never leak key material.

# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.4.x   | Yes       |
| < 0.4   | No        |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report security issues to **security@kvista.se** (not public GitHub issues).

Expected response: acknowledgement within 48 hours, patch within 14 days for
critical issues.

Alternatively, use the GitHub Security Advisory feature:
<https://github.com/11z4t/tuya-cloudless/security/advisories/new>

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected versions

## Security Design Principles

1. **Key isolation** — All cryptographic key material lives exclusively in HA's
   encrypted config entry store. Keys are never written to log files or included
   in exception messages.
2. **No cloud egress** — After the one-time activation mock, no traffic is sent
   to Tuya servers.
3. **Constant-time comparison** — Tag/MAC verification uses `hmac.compare_digest`
   to prevent timing attacks.
4. **Input validation** — All packet payloads are length-checked before parsing.
   Oversized payloads are rejected before decryption.
5. **Exception safety** — Custom exception hierarchy ensures error messages never
   leak key material.

## Known Cryptographic Limitations

### AES-128-CBC with Static Zero IV (Protocol v3.1–v3.3)

Protocol versions 3.1, 3.2, and 3.3 use AES-128-CBC encryption with a static
zero initialization vector (IV). This is a **protocol-level constraint** imposed
by the Tuya Local protocol specification and cannot be changed without breaking
compatibility with the physical devices.

**Implications:**
- Identical plaintext blocks produce identical ciphertext blocks.
- CBC mode without a random IV does not provide semantic security.
- An attacker with access to the local network can observe traffic patterns.

**Recommendation:** Where possible, use devices that support protocol v3.4 or
v3.5, which use ECDH-negotiated session keys and AES-GCM (see below). If your
device only supports v3.1–v3.3, ensure your local network is adequately
segmented and that untrusted devices cannot observe traffic.

### ECDH Session Keys with AES-GCM (Protocol v3.4–v3.5)

Protocol versions 3.4 and 3.5 negotiate an ephemeral session key on every new
TCP connection using X25519 ECDH key exchange. Payloads are then encrypted with
AES-128-GCM, which provides both confidentiality and integrity (authenticated
encryption). This is significantly stronger than the static-IV CBC used in
earlier protocol versions.

**Recommendation:** Prefer devices and firmware versions that support v3.4 or
v3.5 for the strongest security posture.

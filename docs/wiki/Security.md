# Security

## How your device key is stored

The local key is stored in Home Assistant's encrypted configuration storage. It is never logged, never sent to any cloud service, and never included in diagnostics exports.

---

## Network traffic

All communication happens **locally** on your network between Home Assistant and your device. No data is sent to Tuya servers after the initial device pairing.

The integration connects to your device over TCP port 6668 (the standard Tuya LAN port).

---

## Diagnostics

When you download diagnostics:
- DP values (sensor readings, switch states) are included
- The local key is **not** included
- The IP address is included (it is not considered sensitive)
- Error messages are included but never contain key material

---

## Reporting security issues

Please do not open a public GitHub issue for security problems.

Email **security@kvista.se** instead (see SECURITY.md in the repository) with:
- A description of the vulnerability
- Steps to reproduce it (if possible)
- Your suggested fix (optional)

We aim to respond within 48 hours and will credit you in the release notes.

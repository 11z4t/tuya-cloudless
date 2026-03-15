# Protocol Versions

Tuya has released several versions of their local LAN protocol. This integration supports all of them.

---

## Which version does my device use?

Check the device information in the Tuya app, or use the tinytuya scan tool. The version is shown as a number like `3.3` or `3.4`.

If you are unsure, try `3.3` first — it works for most devices.

---

## Version differences

### v3.1
- **Encryption:** None (plaintext)
- **Used by:** Very old devices
- Works the same as v3.3 for most purposes

### v3.3
- **Encryption:** AES-128-ECB
- **Used by:** Most Tuya devices made before 2022
- Most common version

### v3.4
- **Encryption:** AES-128-GCM with session key
- **Session key:** Negotiated via X25519 ECDH on each connection
- **Used by:** Newer devices (2022+)

### v3.5
- **Encryption:** AES-128-GCM with session key
- Same as v3.4 with minor protocol differences
- **Used by:** Latest devices

---

## Session key negotiation (v3.4/v3.5)

For v3.4 and v3.5 devices, the integration automatically negotiates a fresh encryption key every time it connects. This provides forward secrecy — if the session is intercepted, past sessions remain protected.

You do not need to do anything differently — just select the correct version during setup.

# Troubleshooting

## Device is unavailable / not responding

**Check:**
1. Is the device on the same network as Home Assistant?
2. Is the IP address correct? (Devices sometimes change IP after a restart — assign a fixed IP in your router)
3. Is the local key correct?

**To verify the key:** Go to **Settings → Devices & Services → Tuya Cloudless → [device] → Configure** and re-enter the key.

---

## "Authentication error" in the logs

The local key has changed. This can happen if:
- You reset the device
- The device was re-paired in the Tuya app

**Fix:** Re-enter the local key via **Configure** on the device page.

---

## Entities show as unavailable after setup

The device has not connected yet. Wait 10–20 seconds and refresh the page.

If it stays unavailable:
- Check that port 6668 (TCP) is not blocked by a firewall
- Check the Home Assistant logs for error messages

---

## Device connects but values don't update

The device may use a different DP ID than the selected profile expects.

**Fix:**
1. Download diagnostics (see below)
2. Look at the `state.dps` section — what DP IDs are being received?
3. Create a custom profile with the correct IDs (see **[Device Profiles](Device-Profiles)**)

---

## How to download diagnostics

1. Go to **Settings → Devices & Services → Tuya Cloudless**
2. Click on your device
3. Click **Download diagnostics**

The file contains connection state, DP values, and error information — useful when reporting issues.

---

## Reporting an issue

Please include:
- The diagnostics file (with local key removed)
- Your Home Assistant version
- Your device model and firmware version

Open an issue at: https://github.com/11z4t/tuya-cloudless/issues

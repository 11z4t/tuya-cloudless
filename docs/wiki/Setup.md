# Setup

Go to **Settings → Devices & Services → Add integration** and search for **Tuya Cloudless**.

The setup has three steps.

---

## Step 1 — Find your device

The integration scans your network for Tuya devices automatically.

- If your device appears in the list, select it.
- If it does not appear, you can enter the IP address manually.

Your device must be **on the same network** as Home Assistant for automatic discovery to work.

---

## Step 2 — Enter the device key

Enter the **local key** for your device — a 16-character code.

You get this from the Tuya Cloud console or by using the tinytuya wizard.
See **[Installation](Installation)** for how to find your key.

> **Tip:** The key is stored securely in Home Assistant's encrypted storage. You only enter it once.

---

## Step 3 — Choose a device profile

Select what type of device you have:

| Profile | Use for |
|---|---|
| Generic Switch | Any on/off switch |
| Generic Light | Dimmable and color-temperature lights |
| Smart Plug | Plugs with power monitoring |
| Roller Blind | Motorized blinds and covers |

If your device is not in the list, choose the closest match and then customize the profile.
See **[Device Profiles](Device-Profiles)** to add your own.

---

## After setup

Your device appears under **Settings → Devices & Services → Tuya Cloudless**.

You can also go directly to the device page to see all entities, run diagnostics, or update the device key if it changes.

---

## Update device key (re-authentication)

If your device stops responding and the logs show an authentication error:

1. Go to **Settings → Devices & Services → Tuya Cloudless**
2. Find your device and click **Configure**
3. Enter the new local key

You do not need to delete and re-add the device.

# Ta bort integrationen

---

## Ta bort en enhet

1. Gå till **Inställningar → Enheter och tjänster → Tuya Cloudless**
2. Hitta enheten du vill ta bort
3. Klicka på **"..."** → **Ta bort**
4. Bekräfta borttagningen

Alla entiteter som tillhör enheten tas bort från Home Assistant. Själva den fysiska enheten påverkas inte — den fortsätter vara ansluten till ditt WiFi-nätverk.

---

## Ta bort hela integrationen

För att ta bort **alla** enheter och avinstallera integrationen:

1. Gå till **Inställningar → Enheter och tjänster**
2. Klicka på **Tuya Cloudless**
3. Klicka på de tre prickarna (⋮) → **Ta bort**
4. Bekräfta borttagningen
5. Starta om Home Assistant

### Ta bort integrationsfilerna (valfritt)

Om du inte längre vill ha integreringen installerad:

**Via HACS:**
1. Öppna **HACS → Integrationer**
2. Sök efter **Tuya Cloudless**
3. Klicka på de tre prickarna (⋮) → **Ta bort**
4. Starta om Home Assistant

**Manuellt:**
Ta bort mappen `custom_components/tuya_cloudless/` från din Home Assistant-konfigurationsmapp.

---

# Removing the integration (English)

---

## Remove a single device

1. Go to **Settings → Devices & Services → Tuya Cloudless**
2. Find the device you want to remove
3. Click **"..."** → **Delete**
4. Confirm the deletion

All entities belonging to the device are removed from Home Assistant. The physical device is not affected — it remains connected to your WiFi network.

---

## Remove the entire integration

To remove **all** devices and uninstall the integration:

1. Go to **Settings → Devices & Services**
2. Click on **Tuya Cloudless**
3. Click the three dots (⋮) → **Delete**
4. Confirm the deletion
5. Restart Home Assistant

### Remove the integration files (optional)

If you no longer want the integration installed:

**Via HACS:**
1. Open **HACS → Integrations**
2. Search for **Tuya Cloudless**
3. Click the three dots (⋮) → **Remove**
4. Restart Home Assistant

**Manually:**
Delete the folder `custom_components/tuya_cloudless/` from your Home Assistant configuration directory.

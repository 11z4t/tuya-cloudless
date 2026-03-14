#!/bin/bash
set -e
PASS=0; FAIL=0
echo '=== PLAT-544 QC ==='
COUNT=$(grep -rn 'except Exception' lib/tuya_cloudless/crypto.py 2>/dev/null | wc -l)
[ "$COUNT" = "0" ] && { echo 'PASS: No bare Exception in crypto.py'; ((++PASS)); } || { echo "FAIL: $COUNT bare Exception in crypto.py"; ((++FAIL)); }
grep -q 'InvalidTag' lib/tuya_cloudless/crypto.py && { echo 'PASS: InvalidTag imported'; ((++PASS)); } || { echo 'FAIL: InvalidTag not imported'; ((++FAIL)); }
python3 -m py_compile lib/tuya_cloudless/crypto.py && { echo 'PASS: py_compile crypto.py'; ((++PASS)); } || { echo 'FAIL: py_compile crypto.py'; ((++FAIL)); }
echo ''
echo '=== PLAT-542 QC ==='
[ -f custom_components/tuya_cloudless/config_flow.py ] && { echo 'PASS: config_flow.py exists'; ((++PASS)); } || { echo 'FAIL: config_flow.py missing'; ((++FAIL)); }
LINES=$(wc -l < custom_components/tuya_cloudless/config_flow.py 2>/dev/null || echo 0)
[ "$LINES" -ge 80 ] && { echo "PASS: config_flow.py has $LINES lines"; ((++PASS)); } || { echo "FAIL: config_flow.py only $LINES lines"; ((++FAIL)); }
grep -q 'async_setup_entry' custom_components/tuya_cloudless/__init__.py && { echo 'PASS: async_setup_entry exists'; ((++PASS)); } || { echo 'FAIL: async_setup_entry missing'; ((++FAIL)); }
grep -q 'async_unload_entry' custom_components/tuya_cloudless/__init__.py && { echo 'PASS: async_unload_entry exists'; ((++PASS)); } || { echo 'FAIL: async_unload_entry missing'; ((++FAIL)); }
[ -f custom_components/tuya_cloudless/strings.json ] && { echo 'PASS: strings.json exists'; ((++PASS)); } || { echo 'FAIL: strings.json missing'; ((++FAIL)); }
[ -f custom_components/tuya_cloudless/translations/en.json ] && { echo 'PASS: en.json exists'; ((++PASS)); } || { echo 'FAIL: en.json missing'; ((++FAIL)); }
[ -f custom_components/tuya_cloudless/translations/sv.json ] && { echo 'PASS: sv.json exists'; ((++PASS)); } || { echo 'FAIL: sv.json missing'; ((++FAIL)); }
grep -q '"config_flow": true' custom_components/tuya_cloudless/manifest.json && { echo 'PASS: config_flow true'; ((++PASS)); } || { echo 'FAIL: config_flow not true'; ((++FAIL)); }
ALL_EX=$(grep -rn 'except Exception' lib/ custom_components/ 2>/dev/null | wc -l)
[ "$ALL_EX" = "0" ] && { echo 'PASS: 0 bare Exception in entire codebase'; ((++PASS)); } || { echo "FAIL: $ALL_EX bare Exception in codebase"; ((++FAIL)); }
HA_IN_LIB=$(grep -rn 'import homeassistant' lib/ 2>/dev/null | wc -l)
[ "$HA_IN_LIB" = "0" ] && { echo 'PASS: 0 homeassistant imports in lib/'; ((++PASS)); } || { echo "FAIL: $HA_IN_LIB homeassistant imports in lib/"; ((++FAIL)); }
JARGON=$(grep -rin 'UDP\|AES\|protocol version\|daemon\|unicast' custom_components/tuya_cloudless/translations/ custom_components/tuya_cloudless/strings.json 2>/dev/null | wc -l)
[ "$JARGON" = "0" ] && { echo 'PASS: 0 jargon in user-facing strings'; ((++PASS)); } || { echo "FAIL: $JARGON jargon in user-facing strings"; ((++FAIL)); }
echo ''
echo "=== RESULTAT: $PASS passed, $FAIL failed ==="
[ $FAIL -eq 0 ] && echo 'QC: PASS — leverera till outbox' || echo 'QC: FAIL — FIXA INNAN LEVERANS'

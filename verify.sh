#!/bin/bash
PASS=0; FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
count_matches() { grep -c "$1" "$2" 2>/dev/null; true; }

echo '=== PLAT-544 QC ==='
COUNT=$(grep 'except Exception' lib/tuya_cloudless/crypto.py 2>/dev/null | wc -l)
[ "$COUNT" = "0" ] && pass "No bare Exception in crypto.py" || fail "$COUNT bare Exception in crypto.py"
grep -q 'InvalidTag' lib/tuya_cloudless/crypto.py && pass "InvalidTag imported" || fail "InvalidTag not imported"
python3 -m py_compile lib/tuya_cloudless/crypto.py 2>/dev/null && pass "py_compile crypto.py" || fail "py_compile crypto.py"

echo ''
echo '=== PLAT-542 QC ==='
[ -f custom_components/tuya_cloudless/config_flow.py ] && pass "config_flow.py exists" || fail "config_flow.py missing"
LINES=$(wc -l < custom_components/tuya_cloudless/config_flow.py 2>/dev/null || echo 0)
[ "$LINES" -ge 80 ] && pass "config_flow.py has $LINES lines" || fail "config_flow.py only $LINES lines"
grep -q 'async_setup_entry' custom_components/tuya_cloudless/__init__.py && pass "async_setup_entry exists" || fail "async_setup_entry missing"
grep -q 'async_unload_entry' custom_components/tuya_cloudless/__init__.py && pass "async_unload_entry exists" || fail "async_unload_entry missing"
[ -f custom_components/tuya_cloudless/strings.json ] && pass "strings.json exists" || fail "strings.json missing"
[ -f custom_components/tuya_cloudless/translations/en.json ] && pass "en.json exists" || fail "en.json missing"
[ -f custom_components/tuya_cloudless/translations/sv.json ] && pass "sv.json exists" || fail "sv.json missing"
grep -q '"config_flow": true' custom_components/tuya_cloudless/manifest.json && pass "config_flow true in manifest.json" || fail "config_flow not true in manifest.json"
ALL_EX=$(grep -rn 'except Exception' lib/ custom_components/ 2>/dev/null | grep -v '\.pyc' | wc -l)
[ "$ALL_EX" = "0" ] && pass "0 bare Exception in entire codebase" || fail "$ALL_EX bare Exception in codebase"
HA_IN_LIB=$(grep -rn 'import homeassistant' lib/ 2>/dev/null | wc -l)
[ "$HA_IN_LIB" = "0" ] && pass "0 homeassistant imports in lib/" || fail "$HA_IN_LIB homeassistant imports in lib/"
JARGON=$(grep -rin 'UDP\|AES\|protocol version\|daemon\|unicast' custom_components/tuya_cloudless/translations/ custom_components/tuya_cloudless/strings.json 2>/dev/null | wc -l)
[ "$JARGON" = "0" ] && pass "0 jargon in user-facing strings" || fail "$JARGON jargon in user-facing strings"

echo ''
echo "=== RESULTAT: $PASS passed, $FAIL failed ==="
[ $FAIL -eq 0 ] && echo 'QC: PASS — leverera till outbox' || echo 'QC: FAIL — FIXA INNAN LEVERANS'

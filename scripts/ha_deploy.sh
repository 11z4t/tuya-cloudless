#!/usr/bin/env bash
# =============================================================================
# Tuya Cloudless — Deploy + verify pipeline for dev/prod HA
#
# Syncs custom_components/tuya_cloudless/ to a Home Assistant instance,
# restarts HA, then runs the Playwright E2E tests to verify the integration.
#
# Prerequisites:
#   - SSH key access to HA host (hassio@ or root@)
#   - HA running with SSH addon (for SUPERVISOR_TOKEN-based API calls)
#   OR
#   - HA_TOKEN env var with a long-lived token
#
# Usage:
#   ./scripts/ha_deploy.sh                          # dev-HA (192.168.9.10)
#   ./scripts/ha_deploy.sh --host 192.168.5.22     # prod-HA
#   ./scripts/ha_deploy.sh --skip-restart          # sync only, no restart
#   ./scripts/ha_deploy.sh --skip-tests            # deploy only, no E2E tests
#   HA_TOKEN=xxx ./scripts/ha_deploy.sh --no-ssh   # use token, no SSH
# =============================================================================
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
HA_HOST="${HA_HOST:-192.168.9.10}"
HA_PORT="${HA_PORT:-8123}"
HA_SSH_USER="${HA_SSH_USER:-hassio}"
HA_SSH="${HA_SSH_USER}@${HA_HOST}"
HA_URL="http://${HA_HOST}:${HA_PORT}"
DOMAIN="tuya_cloudless"
TIMEOUT=180  # seconds to wait for HA restart
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKIP_RESTART=false
SKIP_TESTS=false
NO_SSH=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --host=*) HA_HOST="${arg#*=}"; HA_SSH="${HA_SSH_USER}@${HA_HOST}"; HA_URL="http://${HA_HOST}:${HA_PORT}" ;;
    --host)   shift; HA_HOST="$1"; HA_SSH="${HA_SSH_USER}@${HA_HOST}"; HA_URL="http://${HA_HOST}:${HA_PORT}" ;;
    --skip-restart) SKIP_RESTART=true ;;
    --skip-tests)   SKIP_TESTS=true ;;
    --no-ssh)       NO_SSH=true ;;
  esac
done

# ── API helpers ───────────────────────────────────────────────────────────────

# Make HA API call — uses SUPERVISOR_TOKEN via SSH addon (default)
# or HA_TOKEN env var (--no-ssh mode)
ha_api() {
  local method="${1}" path="${2}" body="${3:-}"
  if [[ "$NO_SSH" == "true" ]]; then
    # Direct REST API with long-lived token
    if [[ -z "${HA_TOKEN:-}" ]]; then
      fail "HA_TOKEN must be set when using --no-ssh"
    fi
    local url="${HA_URL}/api${path}"
    if [[ "$method" == "GET" ]]; then
      curl -s -H "Authorization: Bearer ${HA_TOKEN}" "$url"
    elif [[ "$method" == "DELETE" ]]; then
      curl -s -X DELETE -H "Authorization: Bearer ${HA_TOKEN}" "$url"
    else
      echo "$body" | curl -s -X "$method" \
        -H "Authorization: Bearer ${HA_TOKEN}" \
        -H "Content-Type: application/json" \
        -d @- "$url"
    fi
  else
    # SSH → supervisor API (SUPERVISOR_TOKEN lives inside addon)
    local addon="addon_a0d7b954_ssh"
    local url="http://supervisor/core/api${path}"
    if [[ "$method" == "GET" ]]; then
      ssh "$HA_SSH" "sudo docker exec $addon bash -c \
        'curl -s -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" $url'" 2>/dev/null
    elif [[ "$method" == "DELETE" ]]; then
      ssh "$HA_SSH" "sudo docker exec $addon bash -c \
        'curl -s -X DELETE -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" $url'" 2>/dev/null
    else
      local tmp
      tmp=$(ssh "$HA_SSH" "mktemp" 2>/dev/null)
      echo "$body" | ssh "$HA_SSH" "cat > $tmp" 2>/dev/null
      ssh "$HA_SSH" "sudo docker cp $tmp $addon:/tmp/api_body.json && \
        sudo docker exec $addon bash -c \
        'curl -s -X $method -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" \
         -H \"Content-Type: application/json\" -d @/tmp/api_body.json $url' && \
        rm -f $tmp" 2>/dev/null
    fi
  fi
}

wait_for_ha() {
  local elapsed=0
  while [[ $elapsed -lt $TIMEOUT ]]; do
    local resp
    resp=$(ha_api GET "/" 2>/dev/null || echo "")
    if echo "$resp" | grep -q '"message"'; then
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
    echo -n "."
  done
  echo ""
  return 1
}

# ════════════════════════════════════════════════════════════════════
echo "═══ Tuya Cloudless — Deploy + Verify ═══"
echo "    Target: ${HA_URL}"
echo ""

# ── Step 0: Pre-flight ────────────────────────────────────────────
echo "── Step 0: Pre-flight ──"

if [[ "$NO_SSH" != "true" ]]; then
  ssh "$HA_SSH" "echo ok" >/dev/null 2>&1 || \
    fail "Cannot SSH to $HA_SSH — add your key or use --no-ssh with HA_TOKEN"
  log "SSH access OK"
fi

# Verify HA is running (lightweight check)
PING=$(curl -s -m 5 "${HA_URL}/" 2>/dev/null | head -1 || echo "")
[[ -n "$PING" ]] || fail "HA not responding at ${HA_URL}"
log "HA reachable"

# ── Step 1: Remove existing config entries ────────────────────────
echo ""
echo "── Step 1: Remove existing config entries ──"

ENTRIES=$(ha_api GET "/config/config_entries/entry" | python3 -c "
import sys, json
try:
  data = json.load(sys.stdin)
  tc = [e for e in data if e.get('domain') == '${DOMAIN}']
  for e in tc: print(e['entry_id'])
except: pass
" 2>/dev/null || echo "")

if [[ -n "$ENTRIES" ]]; then
  while IFS= read -r eid; do
    [[ -z "$eid" ]] && continue
    ha_api DELETE "/config/config_entries/entry/${eid}" >/dev/null
    log "Removed entry: $eid"
  done <<< "$ENTRIES"
else
  log "No existing entries"
fi

# ── Step 2: Sync files ─────────────────────────────────────────────
echo ""
echo "── Step 2: Sync custom component ──"

SRC="${REPO_DIR}/custom_components/${DOMAIN}/"
DEST_SSH="${HA_SSH}:/config/custom_components/${DOMAIN}/"

if [[ "$NO_SSH" == "true" ]]; then
  warn "Skipping file sync (--no-ssh) — use HACS or copy manually"
else
  # Ensure destination dir exists
  ssh "$HA_SSH" "mkdir -p /config/custom_components/${DOMAIN}/" 2>/dev/null || \
  ssh "$HA_SSH" "mkdir -p /homeassistant/custom_components/${DOMAIN}/" 2>/dev/null || true

  # Try /config first, then /homeassistant (HAOS path)
  DEST=$(ssh "$HA_SSH" "test -d /config/custom_components && echo /config || echo /homeassistant" 2>/dev/null)
  DEST_SSH="${HA_SSH}:${DEST}/custom_components/${DOMAIN}/"

  rsync -az --delete --exclude='__pycache__' --exclude='*.pyc' \
    --no-group --no-owner --chmod=ugo=rwX \
    "$SRC" "$DEST_SSH" 2>/dev/null || \
    fail "rsync failed — check SSH and path"
  log "Files synced → ${DEST}/custom_components/${DOMAIN}/"
fi

# ── Step 3: Restart HA ─────────────────────────────────────────────
echo ""
echo "── Step 3: Restart HA ──"

if [[ "$SKIP_RESTART" == "true" ]]; then
  log "Skipping restart (--skip-restart)"
else
  if [[ "$NO_SSH" != "true" ]]; then
    ssh "$HA_SSH" "sudo docker restart homeassistant" >/dev/null 2>&1 || \
    ha_api POST "/services/homeassistant/restart" "{}" >/dev/null 2>/dev/null || true
    log "Restart triggered"
    sleep 20
    echo -n "   Waiting for HA"
    if wait_for_ha; then
      echo ""
      log "HA is up"
    else
      fail "HA did not respond within ${TIMEOUT}s"
    fi
    sleep 10  # extra time for integrations to load
  else
    ha_api POST "/services/homeassistant/restart" "{}" >/dev/null 2>/dev/null || true
    log "Restart triggered (via API)"
    sleep 30
  fi
fi

# ── Step 4: Verify integration is loaded ─────────────────────────
echo ""
echo "── Step 4: Verify integration loads ──"

# Check that starting a config flow works
FLOW_RESP=$(ha_api POST "/config/config_entries/flow" "{\"handler\":\"${DOMAIN}\"}" 2>/dev/null || echo "")
FLOW_TYPE=$(echo "$FLOW_RESP" | python3 -c "
import sys, json
try:
  d = json.load(sys.stdin)
  print(d.get('type', d.get('step_id', 'unknown')))
except: print('error')
" 2>/dev/null)

if [[ "$FLOW_TYPE" == "error" || -z "$FLOW_TYPE" ]]; then
  warn "Could not start config flow — integration may not be loaded yet"
  echo "  Response: $(echo "$FLOW_RESP" | head -c 200)"
else
  log "Config flow started: type=${FLOW_TYPE}"

  # Abort the test flow
  FLOW_ID=$(echo "$FLOW_RESP" | python3 -c \
    "import sys,json; print(json.load(sys.stdin).get('flow_id',''))" 2>/dev/null || echo "")
  [[ -n "$FLOW_ID" ]] && ha_api DELETE "/config/config_entries/flow/${FLOW_ID}" >/dev/null 2>/dev/null || true
fi

# ── Step 5: Run E2E tests ─────────────────────────────────────────
echo ""
echo "── Step 5: Run E2E tests ──"

if [[ "$SKIP_TESTS" == "true" ]]; then
  log "Skipping tests (--skip-tests)"
else
  cd "$REPO_DIR"
  HA_URL="${HA_URL}" \
  HA_USER="${HA_USER:-test}" \
  HA_PASS="${HA_PASS:-carmabox123}" \
  npx playwright test --config=playwright.ha.config.js 2>&1 || {
    warn "Some E2E tests failed — check output above"
    exit 1
  }
  log "E2E tests passed"
fi

# ── Step 6: Check HA logs ─────────────────────────────────────────
echo ""
echo "── Step 6: Check logs ──"

if [[ "$NO_SSH" != "true" ]]; then
  ERRORS=$(ssh "$HA_SSH" \
    "sudo docker logs homeassistant --since 5m 2>&1" 2>/dev/null | \
    grep -i "${DOMAIN}" | grep -iE "error|exception|traceback" || echo "")

  if [[ -z "$ERRORS" ]]; then
    log "No Tuya Cloudless errors in recent logs"
  else
    warn "Errors found in logs:"
    echo "$ERRORS" | head -20
  fi
else
  log "Log check skipped (--no-ssh)"
fi

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════"
log "DEPLOY COMPLETE — ${HA_URL}"

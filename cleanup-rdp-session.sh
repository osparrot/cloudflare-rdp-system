#!/bin/bash
# cleanup-rdp-session.sh <session-sub-or-fqdn>
# Remove service, config, tunnel, DNS, logs, and credentials for an ephemeral session.
# Usage:
#   CF_API_TOKEN=... CF_ZONE_ID=... /usr/local/bin/cleanup-rdp-session.sh session-xxxx
set -euo pipefail

CF_API_TOKEN="${CF_API_TOKEN:-}"
CF_ZONE_ID="${CF_ZONE_ID:-}"
BASE_DOMAIN="${BASE_DOMAIN:-rdp.accesscontrole.com}"
JQ_BIN="$(command -v jq || true)"
CLOUDFLARED_BIN="$(command -v cloudflared || true)"

usage() {
  echo "Usage: $0 <session-sub>  (e.g. session-ab12cd) or FQDN"
  exit 1
}

[[ $# -ge 1 ]] || usage
IDENT="$1"

# Normalize input to SUB and FQDN
if [[ "$IDENT" == *"."* ]]; then
  FQDN="$IDENT"
  SUB="${IDENT%%.*}"
else
  SUB="$IDENT"
  FQDN="${SUB}.${BASE_DOMAIN}"
fi

UUID_JSON_DIR="/etc/cloudflared"
SESSION_CONFIG="/etc/cloudflared/${SUB}.yml"
SESSION_SERVICE="cloudflared-${SUB}.service"
SERVICE_PATH="/etc/systemd/system/${SESSION_SERVICE}"
USER_CREDS_FILE="/etc/rdp-session-${SUB}.creds"
LOG_FILE="/var/log/cloudflared-rdp/${SUB}.log"

echo "Cleaning up ephemeral session: $SUB (FQDN: $FQDN)"

# Stop and remove systemd service
if systemctl list-unit-files | grep -q "${SESSION_SERVICE}"; then
  systemctl stop "${SESSION_SERVICE}" 2>/dev/null || true
  systemctl disable --now "${SESSION_SERVICE}" 2>/dev/null || true
fi
rm -f "${SERVICE_PATH}" "${SESSION_CONFIG}"
systemctl daemon-reload

# Stop and remove possible expire timer
if systemctl list-unit-files | grep -q "${SUB}-expire.timer"; then
  systemctl stop "${SUB}-expire.timer" 2>/dev/null || true
  systemctl disable --now "${SUB}-expire.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SUB}-expire.timer" "/etc/systemd/system/${SUB}-expire.service"
fi
systemctl daemon-reload

# Remove user creds and logs
rm -f "${USER_CREDS_FILE}" "${LOG_FILE}"

# Remove watchdog script and timer if they exist
if [[ -f "/usr/local/bin/rdp_watch_${SUB}.sh" ]]; then
  rm -f "/usr/local/bin/rdp_watch_${SUB}.sh"
fi
if systemctl list-unit-files | grep -q "${SUB}-watch.timer"; then
  systemctl stop "${SUB}-watch.timer" 2>/dev/null || true
  systemctl disable --now "${SUB}-watch.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SUB}-watch.timer" "/etc/systemd/system/${SUB}-watch.service"
fi
systemctl daemon-reload

# Remove DNS record via Cloudflare API (if token + zone provided)
if [[ -n "${CF_API_TOKEN}" && -n "${CF_ZONE_ID}" && -x "${JQ_BIN:-}" ]]; then
  echo "Removing DNS records for ${FQDN} via Cloudflare API..."
  RECS_JSON="$(curl -sS -X GET "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=${FQDN}" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json")"
  IDS="$(echo "${RECS_JSON}" | ${JQ_BIN} -r '.result[]?.id' || true)"
  if [[ -n "$IDS" ]]; then
    for id in $IDS; do
      echo "Deleting DNS record id $id ..."
      curl -sS -X DELETE "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${id}" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" >/dev/null || true
    done
    echo "DNS records removed."
  else
    echo "No DNS record found for ${FQDN} (or query failed)."
  fi
else
  echo "CF_API_TOKEN/CF_ZONE_ID not provided or jq not available — skipping Cloudflare DNS cleanup."
fi

# Attempt to delete the tunnel using cloudflared (best-effort)
if [[ -x "${CLOUDFLARED_BIN}" ]]; then
  # Try to delete a tunnel with the same name (session name)
  if "${CLOUDFLARED_BIN}" tunnel list --no-header 2>/dev/null | grep -q "${SUB}"; then
    echo "Deleting cloudflared tunnel ${SUB} ..."
    "${CLOUDFLARED_BIN}" tunnel delete "${SUB}" || true
  fi
fi

# Remove credentials JSON file if present (try to find a JSON for this session)
for f in "${UUID_JSON_DIR}/${SUB}"*.json; do
  if [[ -f "$f" ]]; then
    rm -f "$f"
  fi
done

echo "Cleanup complete for session ${SUB}."
exit 0

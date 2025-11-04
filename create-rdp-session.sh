#!/bin/bash
# create-rdp-session.sh
# Create ephemeral Cloudflare Tunnel + DNS + systemd service for an RDP session (localhost:3389).
# Usage (as root):
#   CF_API_TOKEN=... CF_ZONE_ID=... BASE_DOMAIN=rdp.accesscontrole.com \
#     /usr/local/bin/create-rdp-session.sh "user1,user2" [SESSION_TTL_HOURS]
#
# - USERS CSV optional (default "runneradmin")
# - SESSION_TTL_HOURS optional: if set, session will auto-cleanup after that many hours

set -euo pipefail

# ---------- Config & env ----------
CF_API_TOKEN="${CF_API_TOKEN:-}"   # optional but recommended for DNS cleanup
CF_ZONE_ID="${CF_ZONE_ID:-}"       # optional but recommended for DNS cleanup
BASE_DOMAIN="${BASE_DOMAIN:-rdp.accesscontrole.com}"
USERS_ARG="${1:-runneradmin}"
SESSION_TTL_HOURS="${2:-}"         # optional TTL in hours
CLOUDFLARED_BIN="$(command -v cloudflared || true)"
JQ_BIN="$(command -v jq || true)"
OPENSSL_BIN="$(command -v openssl || true)"
CURL_BIN="$(command -v curl || true)"

# ---------- Helpers ----------
err() { echo "❌ $*" >&2; exit 1; }
info() { echo "ℹ️  $*"; }

# ---------- Sanity ----------
[[ $(id -u) -eq 0 ]] || err "Run as root (sudo)."
[[ -x "$CLOUDFLARED_BIN" ]] || err "cloudflared not found. Install and run 'cloudflared tunnel login' first."
[[ -x "$JQ_BIN" ]] || err "jq not found. apt install -y jq"
[[ -x "$OPENSSL_BIN" ]] || err "openssl not found."
[[ -x "$CURL_BIN" ]] || err "curl not found."

# ---------- Build session identifiers ----------
HEX="$($OPENSSL_BIN rand -hex 4)"
SUB="session-${HEX}"
TUNNEL_NAME="${SUB}"   # we will create the tunnel named the same as session
FQDN="${SUB}.${BASE_DOMAIN}"
SESSION_SERVICE="cloudflared-${SUB}.service"
SESSION_CONFIG="/etc/cloudflared/${SUB}.yml"
CRED_JSON_DIR="/etc/cloudflared"
CRED_JSON_TMP_DIR="/root/.cloudflared"   # where cloudflared writes credentials initially
LOG_DIR="/var/log/cloudflared-rdp"
mkdir -p "$LOG_DIR" "$CRED_JSON_DIR"
LOG_FILE="$LOG_DIR/${SUB}.log"
USER_CREDS_FILE="/etc/rdp-session-${SUB}.creds"

info "Creating ephemeral RDP session: $SUB"
info "FQDN: $FQDN"

# ---------- Create the tunnel ----------
info "Creating tunnel: $TUNNEL_NAME"
# This creates a credentials JSON in ~/.cloudflared (or /root/.cloudflared) and prints the UUID
# The command will output something like: "Tunnel credentials written to /home/.../.cloudflared/<uuid>.json"
CREATE_OUT="$($CLOUDFLARED_BIN tunnel create "$TUNNEL_NAME" 2>&1)" || {
  echo "$CREATE_OUT" >&2
  err "cloudflared tunnel create failed"
}
echo "$CREATE_OUT" | sed -n '1,200p'

# find the newest credentials JSON created by cloudflared
CREDFILE="$(ls -t /root/.cloudflared/*.json /home/*/.cloudflared/*.json 2>/dev/null | head -n1 || true)"
if [[ -z "$CREDFILE" || ! -f "$CREDFILE" ]]; then
  # try ~/.cloudflared in current user
  CREDFILE="$(ls -t ~/.cloudflared/*.json 2>/dev/null | head -n1 || true)"
fi
[[ -n "$CREDFILE" ]] || err "Could not locate credentials JSON created by cloudflared. Check cloudflared output and try again."

# move credentials JSON to /etc/cloudflared and set strict perms
UUID="$(basename "$CREDFILE" .json)"
sudo mv -f "$CREDFILE" "${CRED_JSON_DIR}/${UUID}.json"
sudo chown root:root "${CRED_JSON_DIR}/${UUID}.json"
sudo chmod 600 "${CRED_JSON_DIR}/${UUID}.json"
info "Tunnel credentials moved to ${CRED_JSON_DIR}/${UUID}.json"

# ---------- Write per-session cloudflared config ----------
cat > "$SESSION_CONFIG" <<EOF
tunnel: ${UUID}
credentials-file: ${CRED_JSON_DIR}/${UUID}.json

ingress:
  - hostname: ${FQDN}
    service: tcp://localhost:3389
  - service: http_status:404
EOF
chmod 640 "$SESSION_CONFIG"
info "Session config written: $SESSION_CONFIG"

# ---------- Create systemd service to run the tunnel for this session ----------
SERVICE_PATH="/etc/systemd/system/${SESSION_SERVICE}"
cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Cloudflared ephemeral tunnel for ${FQDN}
After=network.target

[Service]
Type=simple
ExecStart=${CLOUDFLARED_BIN} tunnel --config ${SESSION_CONFIG} run
Restart=on-failure
RestartSec=5
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$SERVICE_PATH"
systemctl daemon-reload
systemctl enable --now "${SESSION_SERVICE}"
sleep 1

if ! systemctl is-active --quiet "${SESSION_SERVICE}"; then
  echo "=== service logs ==="
  journalctl -u "${SESSION_SERVICE}" -n 80 --no-pager || true
  err "Session systemd service failed to start"
fi
info "Session service started: ${SESSION_SERVICE}"

# ---------- Create DNS route (CNAME) using cloudflared CLI -----------
# This requires the cloudflared login to have permission to create DNS records for the zone.
info "Registering DNS: creating CNAME for $FQDN pointing to the tunnel"
$CLOUDFLARED_BIN tunnel route dns "$TUNNEL_NAME" "$FQDN" >/dev/null || {
  # If cloudflared route dns failed, attempt to cleanup created service+files and error
  err "cloudflared tunnel route dns failed. Ensure cloudflared is logged in and has zone access."
}
info "DNS route created for $FQDN"

# ---------- Generate per-user credentials ----------
: > "$USER_CREDS_FILE"
chmod 600 "$USER_CREDS_FILE"
declare -A CREDS
IFS=',' read -r -a USERS <<< "$USERS_ARG"
for u in "${USERS[@]}"; do
  p="$($OPENSSL_BIN rand -base64 12 | tr -dc 'A-Za-z0-9' | head -c 16)"
  CREDS["$u"]="$p"
  echo "$u:$p" >> "$USER_CREDS_FILE"
done
info "Saved per-session credentials: $USER_CREDS_FILE"

# ---------- Optional: create expiry timer for auto-cleanup ----------
if [[ -n "$SESSION_TTL_HOURS" ]]; then
  # create one-shot systemd timer that calls cleanup script after X hours
  TTL_SEC=$(( SESSION_TTL_HOURS * 3600 ))
  EXPIRE_SERVICE="/etc/systemd/system/${SUB}-expire.service"
  EXPIRE_TIMER="/etc/systemd/system/${SUB}-expire.timer"

  cat > "$EXPIRE_SERVICE" <<EOF
[Unit]
Description=Auto-cleanup ephemeral RDP session ${SUB}

[Service]
Type=oneshot
ExecStart=/usr/local/bin/cleanup-rdp-session.sh ${SUB}
EOF

  cat > "$EXPIRE_TIMER" <<EOF
[Unit]
Description=Auto-cleanup timer for ${SUB}

[Timer]
OnActiveSec=${TTL_SEC}
Unit=${SUB}-expire.service

[Install]
WantedBy=timers.target
EOF

  chmod 644 "$EXPIRE_SERVICE" "$EXPIRE_TIMER"
  systemctl daemon-reload
  systemctl enable --now "${SUB}-expire.timer"
  info "Auto-expire timer created: ${SUB}-expire.timer (after ${SESSION_TTL_HOURS} hours)"
fi

# ---------- Final output ----------
cat <<EOF

==============================================
✅ Ephemeral RDP session created
----------------------------------------------
Session name: ${SUB}
FQDN: ${FQDN}
Systemd service: ${SESSION_SERVICE}
Cloudflared config: ${SESSION_CONFIG}
Tunnel credentials: ${CRED_JSON_DIR}/${UUID}.json
User creds: ${USER_CREDS_FILE}
Log: ${LOG_FILE}
EOF

for u in "${USERS[@]}"; do
  echo "  - ${u} : ${CREDS[$u]}"
done

echo
echo "To connect from a client:"
echo "  cloudflared access rdp --hostname ${FQDN} --url localhost:3389"
echo "  then open your RDP client to localhost:3389"
echo
echo "To cleanup now (recommended when finished):"
echo "  sudo /usr/local/bin/cleanup-rdp-session.sh ${SUB}"
echo "=============================================="
exit 0

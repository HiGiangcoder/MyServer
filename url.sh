#!/bin/bash
# In URL công khai hiện tại và kiểm tra API
set -uo pipefail

NGROK_CONF="$HOME/.config/ngrok/ngrok.yml"
CF_LOG="$HOME/.cloudflared/quick.log"

DOMAIN="$(awk '/^[[:space:]]*domain:[[:space:]]*/ {print $2; exit}' "$NGROK_CONF" 2>/dev/null)"

if [[ -n "${DOMAIN:-}" ]]; then
    URL="https://${DOMAIN}"
    SOURCE="ngrok"
else
    URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | tail -1)"
    SOURCE="cloudflared quick tunnel"
fi

if [[ -z "${URL:-}" ]]; then
    echo "Chưa có URL công khai."
    echo "Kiểm tra: systemctl --user status ngrok-myserver"
    exit 1
fi

echo "Nguồn       : $SOURCE"
echo "URL công khai: $URL"
echo -n "Kiểm tra API : "
curl -s --max-time 25 "$URL/api/hello" || echo "không phản hồi"
echo

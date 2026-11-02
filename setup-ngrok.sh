#!/bin/bash
# Cấu hình ngrok static domain và bật service.
# Dùng:
#   ~/myserver/setup-ngrok.sh <AUTHTOKEN> <DOMAIN>
# Ví dụ domain: gianglab.ngrok-free.app
set -euo pipefail

TOKEN="${1:-}"
DOMAIN="${2:-}"

if [[ -z "$TOKEN" || -z "$DOMAIN" ]]; then
    echo "Dùng: $0 <AUTHTOKEN> <DOMAIN>"
    exit 1
fi

mkdir -p "$HOME/.config/ngrok"
umask 077

cat > "$HOME/.config/ngrok/ngrok.yml" <<EOF
version: "3"
agent:
  authtoken: ${TOKEN}
tunnels:
  myserver:
    proto: http
    addr: 80
    domain: ${DOMAIN}
    host_header: gianglab.duckdns.org
EOF

chmod 600 "$HOME/.config/ngrok/ngrok.yml"
echo "Đã ghi ~/.config/ngrok/ngrok.yml"

systemctl --user daemon-reload
systemctl --user enable --now ngrok-myserver.service
sleep 6
systemctl --user --no-pager status ngrok-myserver.service | head -8

echo
echo "URL công khai: https://${DOMAIN}"
echo -n "Kiểm tra API : "
curl -s --max-time 25 "https://${DOMAIN}/api/hello" || echo "chưa phản hồi, xem log:"
echo

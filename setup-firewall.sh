#!/usr/bin/env bash
# Chạy 1 lần bằng sudo:
#   sudo /home/brolyn-ntg/myserver/setup-firewall.sh
set -euo pipefail

echo "==> Mở cổng 80/443 trên UFW"
ufw allow 'Nginx Full'
ufw reload
ufw status verbose

echo "==> Cho phép service người dùng chạy khi chưa đăng nhập (boot)"
loginctl enable-linger brolyn-ntg
loginctl show-user brolyn-ntg -p Linger

echo "==> Kiểm tra nginx"
nginx -t
systemctl reload nginx

echo "XONG."

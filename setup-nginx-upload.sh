#!/usr/bin/env bash
# Cho phép upload tệp lớn qua nginx (mặc định chỉ 1 MB).
# Chạy 1 lần bằng sudo:
#   sudo /home/brolyn-ntg/myserver/setup-nginx-upload.sh
set -euo pipefail

CONF=/etc/nginx/sites-available/myserver

if [[ ! -f "$CONF" ]]; then
    echo "Không tìm thấy $CONF"
    exit 1
fi

python3 - "$CONF" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()

lines = [
    "client_max_body_size 21g;",
    "client_body_timeout 600s;",
    "proxy_request_buffering off;",
    "proxy_read_timeout 7200s;",
    "proxy_send_timeout 7200s;",
]

# Xóa các dòng cũ (nếu đã cấu hình trước đó)
for key in ["client_max_body_size", "client_body_timeout",
            "proxy_request_buffering", "proxy_read_timeout",
            "proxy_send_timeout"]:
    text = re.sub(rf"^[ \t]*{key}[^\n]*\n", "", text, flags=re.MULTILINE)

# Chèn ngay sau server_name
match = re.search(r"^(\s*server_name[^;]*;\s*)$", text, flags=re.MULTILINE)
if not match:
    print("Không tìm thấy dòng server_name trong", path)
    sys.exit(1)

insert = "\n" + "\n".join("    " + line for line in lines) + "\n"
text = text[: match.end()] + insert + text[match.end():]

open(path, "w", encoding="utf-8").write(text)
print("Đã cập nhật", path)
PY

nginx -t
systemctl reload nginx

echo "XONG. Giới hạn upload: 21 GB (khớp MAX_CONTENT_LENGTH của Flask)."

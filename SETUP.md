# Hướng dẫn dựng MyServer từ đầu

Tài liệu này dựng lại **toàn bộ** hệ thống từ máy trắng: Flask → Gunicorn →
Nginx → đưa ra Internet bằng ngrok (hoặc DuckDNS + port forwarding).

- Hệ điều hành dùng trong ví dụ: Linux Mint / Ubuntu (apt, systemd).
- Username: `brolyn-ntg` — thay bằng username của bạn nếu khác.
- Thư mục dự án: `~/myserver`.
- Cuối mỗi phần đều có lệnh kiểm tra.

## Mục lục

1. [Kiến trúc](#1-kiến-trúc)
2. [Cài gói hệ thống](#2-cài-gói-hệ-thống)
3. [Tạo ứng dụng Flask](#3-tạo-ứng-dụng-flask)
4. [Chạy nền bằng Gunicorn + systemd](#4-chạy-nền-bằng-gunicorn--systemd)
5. [Nginx reverse proxy](#5-nginx-reverse-proxy)
6. [DuckDNS (tùy chọn)](#6-duckdns-tùy-chọn)
7. [Đưa ra Internet](#7-đưa-ra-internet)
8. [Firewall](#8-firewall)
9. [Kiểm tra toàn bộ](#9-kiểm-tra-toàn-bộ)
10. [Xử lý sự cố](#10-xử-lý-sự-cố)
11. [Bảo mật](#11-bảo-mật)

---

## 1. Kiến trúc

```
Cách A (không cần mở port):
Internet → ngrok/Cloudflare → nginx :80 → gunicorn 127.0.0.1:8000 → Flask

Cách B (cần port forwarding):
Internet → router (NAT 80) → nginx :80 → gunicorn 127.0.0.1:8000 → Flask
```

Nguyên tắc:

- Gunicorn **chỉ** nghe `127.0.0.1:8000` (không lộ trực tiếp ra ngoài).
- Nginx nghe `:80`, làm reverse proxy và là điểm vào duy nhất.
- Tunnel (ngrok/Cloudflare) nối Internet vào nginx, nên không cần mở port router.
- systemd user service giữ app và tunnel luôn chạy, tự restart khi crash.

---

## 2. Cài gói hệ thống

```bash
sudo apt update
sudo apt install -y nginx python3-venv python3-pip curl
sudo systemctl enable --now nginx
```

Kiểm tra:

```bash
systemctl is-active nginx      # active
nginx -v
python3 --version
```

---

## 3. Tạo ứng dụng Flask

### 3.1. Tạo thư mục và môi trường ảo

```bash
mkdir -p ~/myserver/templates
cd ~/myserver
python3 -m venv venv
source venv/bin/activate
pip install flask gunicorn
```

### 3.2. Mã nguồn

Chép các file sau từ repo vào `~/myserver` (hoặc clone repo):

```
app.py                    # Flask + SQLite: toàn bộ route và API
templates/base.html       # layout chung
templates/login.html
templates/register.html
templates/chat.html
templates/settings.html
templates/admin_files.html
static/style.css
static/app.js
setup-nginx-upload.sh     # bật upload lớn qua nginx (chạy bằng sudo)
```

Ứng dụng tự tạo `data/app.db` và `data/secret_key` ở lần chạy đầu tiên,
không cần migration thủ công. Khi khởi động, app tự thêm các cột/bảng còn
thiếu (`users.settings`, `users.is_admin`, `attachments`, `server_settings`)
và tự gán admin cho username `admin` (hoặc user đầu tiên) nếu chưa có ai.

Các route chính:

| Route | Mô tả |
|---|---|
| `GET /` | Trang chat (yêu cầu đăng nhập) |
| `GET/POST /register` | Đăng ký tài khoản (user đầu tiên thành admin) |
| `GET/POST /login` | Đăng nhập |
| `POST /logout` | Đăng xuất (có CSRF) |
| `GET /api/chat?since=<id>` | Tin phòng chung mới hơn `id` |
| `POST /api/chat` | Gửi tin phòng chung |
| `GET /api/dm/<username>?since=<id>` | Tin riêng với một người |
| `POST /api/dm/<username>` | Gửi tin riêng |
| `POST /api/upload` | Gửi tệp/thư mục kèm tin nhắn (multipart) |
| `GET /files/<id>` | Tải/xem tệp đính kèm (kiểm tra quyền) |
| `GET /api/users` | Danh sách thành viên + trạng thái online |
| `POST /api/heartbeat` | Cập nhật "đang online" |
| `GET /settings` | Cài đặt tài khoản + chat (+ admin) |
| `POST /settings/profile` | Đổi tên hiển thị / username |
| `POST /settings/password` | Đổi mật khẩu |
| `POST /settings/prefs` | Lưu giao diện/cài đặt chat |
| `POST /settings/delete` | Xóa tài khoản + toàn bộ tin nhắn |
| `POST /settings/admin` | Lưu cấu hình server (chỉ admin) |
| `GET /admin/files` | Quản lý tệp tạm (chỉ admin) |
| `POST /admin/files/delete` | Xóa tệp đã chọn (chỉ admin) |
| `POST /admin/cleanup` | Dọn tệp hết hạn ngay (chỉ admin) |
| `POST /admin/messages/purge` | Xóa tin nhắn cũ/toàn bộ (chỉ admin) |
| `GET /api/hello`, `/api/info` | API kiểm tra cũ |

> Mọi API `POST` bắt buộc header `X-CSRF-Token` (giá trị lấy từ
> `window.APP.csrf` trong trang chat). Thiếu header trả `403`.

### 3.3. Cấu trúc thư mục

```
~/myserver/
├── app.py
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   ├── chat.html
│   ├── settings.html
│   └── admin_files.html
├── static/
│   ├── style.css
│   └── app.js
└── data/                 # tự tạo, đã gitignore
    ├── app.db
    ├── secret_key
    └── uploads/          # tệp gửi lên, tự xóa theo hạn
```

### 3.4. Chạy thử

```bash
cd ~/myserver
source venv/bin/activate
python app.py
```

Mở terminal khác:

```bash
curl http://127.0.0.1:8000/api/hello
curl -I http://127.0.0.1:8000/
```

Sau khi thử xong, **Ctrl+C** để tắt. (Nếu chạy tiếp sẽ chiếm port 8000 và
Gunicorn ở bước sau báo `Address already in use`.)

---

## 4. Chạy nền bằng Gunicorn + systemd

### 4.1. File service

Tạo `~/.config/systemd/user/myserver.service`:

```ini
[Unit]
Description=My Flask server (gunicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/brolyn-ntg/myserver
Environment=PATH=/home/brolyn-ntg/myserver/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/brolyn-ntg/myserver/venv/bin/gunicorn \
    --workers 3 \
    --bind 127.0.0.1:8000 \
    --timeout 14400 \
    --graceful-timeout 60 \
    --access-logfile - \
    --error-logfile - \
    app:app
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

> Thay `/home/brolyn-ntg` bằng `$HOME` thực tế của bạn.

### 4.2. Bật service

```bash
systemctl --user daemon-reload
systemctl --user enable --now myserver.service
systemctl --user status myserver.service
```

Nếu đang còn tiến trình `python app.py`, tắt nó trước:

```bash
pkill -f 'python3 app.py'
```

### 4.3. Cho phép chạy khi chưa đăng nhập (quan trọng)

Mặc định service user chỉ chạy khi bạn đăng nhập. Bật linger để chạy từ lúc boot:

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger    # Linger=yes
```

### 4.4. Kiểm tra

```bash
ss -tlnp | grep 8000
curl -s http://127.0.0.1:8000/api/hello
```

---

## 5. Nginx reverse proxy

### 5.1. File site

Tạo `/etc/nginx/sites-available/myserver`:

```nginx
server {
    listen 80;
    listen [::]:80;

    server_name gianglab.duckdns.org;

    # Cho phép gửi tệp lớn (mặc định nginx chỉ 1 MB)
    client_max_body_size 21g;
    client_body_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:8000;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Không đệm request body để upload lớn chuyển tiếp ngay
        proxy_request_buffering off;
        proxy_read_timeout 7200s;
        proxy_send_timeout 7200s;
    }
}
```

Nếu site đã tồn tại, chỉ cần chạy script có sẵn (tự chèn các dòng trên):

```bash
sudo ~/myserver/setup-nginx-upload.sh
```

Giới hạn mặc định của app: **2 GB/tệp**, admin có thể nâng tối đa **20 GB/tệp**.
Request tối đa 21 GB (đủ cho 1 tệp 20 GB + overhead multipart). Trong lúc nhận
file, multipart tạm cũng được ghi vào cùng thư mục upload, nên cần khoảng
**2 lần dung lượng tệp** đang upload. Hãy trỏ thư mục lưu tệp sang ổ lớn nếu
định gửi file cực lớn.

> ngrok Free giới hạn 1 GB data transfer/tháng, nên không phù hợp để gửi
> file hàng chục GB. Dùng port forwarding trực tiếp hoặc dịch vụ khác.

### 5.2. Kích hoạt

```bash
sudo ln -s /etc/nginx/sites-available/myserver /etc/nginx/sites-enabled/myserver
sudo nginx -t
sudo systemctl reload nginx
```

(`ln -s` chỉ chạy một lần. Nếu báo `File exists` nghĩa là đã có link.)

### 5.3. Kiểm tra

Vì `server_name` là domain, test bằng Host header:

```bash
curl -H "Host: gianglab.duckdns.org" http://127.0.0.1/api/hello
curl -I -H "Host: gianglab.duckdns.org" http://127.0.0.1/
```

Kết quả mong đợi: `200` và JSON.

> Truy cập `http://192.168.1.9` (IP LAN) sẽ ra trang mặc định của nginx vì
> `server_name` không khớp. Muốn IP LAN cũng vào app:
> ```bash
> sudo rm /etc/nginx/sites-enabled/default
> sudo systemctl reload nginx
> ```

---

## 6. DuckDNS (tùy chọn)

Chỉ cần nếu bạn đi theo **Cách B — port forwarding**. Nếu dùng ngrok/Cloudflare
thì bỏ qua phần này.

### 6.1. Tạo tên miền

1. Đăng nhập https://www.duckdns.org (Google/GitHub).
2. Tạo subdomain, ví dụ `gianglab` → `gianglab.duckdns.org`.
3. Copy token ở đầu trang.

### 6.2. Lưu token và script

```bash
mkdir -p ~/duckdns
printf '%s\n' 'TOKEN_CUA_BAN' > ~/duckdns/duck.token
chmod 600 ~/duckdns/duck.token
```

Tạo `~/duckdns/duck.sh`:

```bash
#!/bin/bash
# Cập nhật IP công khai hiện tại lên DuckDNS.
# Token đọc từ file riêng (chmod 600), không ghi trong script.
set -uo pipefail

DOMAIN="gianglab"
TOKEN_FILE="$HOME/duckdns/duck.token"
LOG="$HOME/duckdns/duck.log"

if [[ ! -r "$TOKEN_FILE" ]]; then
    echo "$(date '+%F %T') ERROR: không đọc được $TOKEN_FILE" >> "$LOG"
    exit 1
fi

TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"

# ip= rỗng => DuckDNS tự lấy IP công khai của kết nối
RESPONSE="$(curl -fsS --max-time 20 \
    "https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}&ip=" \
    2>&1)" || RESPONSE="curl-error"

echo "$(date '+%F %T') $RESPONSE" >> "$LOG"

# Giữ log gọn (500 dòng gần nhất)
if [[ -f "$LOG" ]] && (( $(wc -l < "$LOG") > 500 )); then
    tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
```

```bash
chmod 700 ~/duckdns/duck.sh
~/duckdns/duck.sh
cat ~/duckdns/duck.log      # phải thấy OK
```

### 6.3. Cron cập nhật mỗi 5 phút

```bash
( crontab -l 2>/dev/null | grep -v 'duckdns/duck.sh'
  echo '*/5 * * * * /home/brolyn-ntg/duckdns/duck.sh >/dev/null 2>&1' ) | crontab -
crontab -l
```

Kiểm tra DNS:

```bash
dig +short gianglab.duckdns.org
curl -s https://api.ipify.org; echo
```

Hai IP phải giống nhau.

---

## 7. Đưa ra Internet

### Cách A1 — ngrok static domain (khuyến nghị, không mở port)

**a) Cài ngrok**

```bash
mkdir -p ~/.local/bin
cd /tmp
curl -fsSL -o ngrok.tgz https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar -xzf ngrok.tgz
install -m 755 ngrok ~/.local/bin/ngrok
~/.local/bin/ngrok version
```

**b) Tạo tài khoản và lấy thông tin**

1. Đăng ký: https://dashboard.ngrok.com/signup
2. Authtoken: https://dashboard.ngrok.com/get-started/your-authtoken
3. Static domain: https://dashboard.ngrok.com/domains → **New Domain**
   (free plan được 1 domain, dạng `ten-ngau-nhien.ngrok-free.dev`)

**c) File cấu hình `~/.config/ngrok/ngrok.yml`**

```yaml
version: "3"
agent:
  authtoken: <AUTHTOKEN>
tunnels:
  myserver:
    proto: http
    addr: 80
    domain: <DOMAIN>.ngrok-free.dev
    host_header: gianglab.duckdns.org
```

> `authtoken` **phải** nằm dưới `agent:`. Để ở cấp cao nhất sẽ báo
> `field authtoken not found in type config.v3yamlConfig`.
>
> `addr: 80` để đi qua nginx; `host_header` giúp nginx khớp `server_name`.

```bash
chmod 600 ~/.config/ngrok/ngrok.yml
```

Hoặc dùng script tự động (tạo ở mục 7.2).

**d) Service `~/.config/systemd/user/ngrok-myserver.service`**

```ini
[Unit]
Description=ngrok tunnel -> local nginx (static domain)
After=network-online.target myserver.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/home/brolyn-ntg/.local/bin/ngrok start --all --config /home/brolyn-ntg/.config/ngrok/ngrok.yml --log stdout --log-format logfmt
Restart=always
RestartSec=5
StandardOutput=append:/home/brolyn-ntg/.config/ngrok/ngrok.log
StandardError=append:/home/brolyn-ntg/.config/ngrok/ngrok.log

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now ngrok-myserver.service
systemctl --user status ngrok-myserver.service
curl https://<DOMAIN>.ngrok-free.dev/api/hello
```

### Cách A2 — Cloudflare Quick Tunnel (không cần tài khoản)

```bash
curl -fsSL -o /tmp/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x /tmp/cloudflared
install -m 755 /tmp/cloudflared ~/.local/bin/cloudflared
~/.local/bin/cloudflared --version
```

Chạy thử:

```bash
~/.local/bin/cloudflared tunnel --no-autoupdate \
  --url http://127.0.0.1:80 --http-host-header gianglab.duckdns.org
```

Log sẽ in ra URL `https://<ngau-nhien>.trycloudflare.com`.
URL này **đổi mỗi lần chạy**, chỉ phù hợp demo/test.

### Cách B — Port forwarding + DuckDNS

1. Vào router (`http://192.168.1.1` hoặc `https://192.168.1.1`).
2. **Port Forwarding**: `TCP 80 → 192.168.1.9:80` (IP máy chủ, không phải IP khác).
3. Nên đặt **DHCP reservation** cho máy chủ để IP không đổi.
4. Kiểm tra **WAN IP** trên router:
   - Bằng IP công khai (`curl -s https://api.ipify.org`) → NAT bình thường.
   - Dạng `100.64.x.x` hoặc khác → **CGNAT**, port forwarding vô tác dụng;
     dùng ngrok/Cloudflare.
5. Nếu ISP chặn port 80: forward cổng ngoài khác, ví dụ
   `TCP 8080 → 192.168.1.9:80`, rồi test `http://gianglab.duckdns.org:8080`.

> Test từ **điện thoại dùng 4G** (tắt WiFi). `curl` domain trên chính máy chủ
> thường treo do router không hỗ trợ NAT loopback — không phải lỗi cấu hình.

### 7.2. Script tự động cấu hình ngrok

Tạo `~/myserver/setup-ngrok.sh`:

```bash
#!/bin/bash
# Dùng: ~/myserver/setup-ngrok.sh <AUTHTOKEN> <DOMAIN>
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
```

```bash
chmod 755 ~/myserver/setup-ngrok.sh
~/myserver/setup-ngrok.sh 'AUTHTOKEN' 'ten.ngrok-free.dev'
```

### 7.3. Script xem URL công khai

Tạo `~/myserver/url.sh`:

```bash
#!/bin/bash
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
    exit 1
fi

echo "Nguồn        : $SOURCE"
echo "URL công khai: $URL"
echo -n "Kiểm tra API : "
curl -s --max-time 25 "$URL/api/hello" || echo "không phản hồi"
echo
```

```bash
chmod 755 ~/myserver/url.sh
~/myserver/url.sh
```

---

## 8. Firewall

Nếu dùng tunnel thì **không cần mở port**. Nếu dùng port forwarding:

```bash
sudo ufw allow 22/tcp        # SSH trước, tránh tự khóa mình
sudo ufw allow 'Nginx Full'  # 80 + 443
sudo ufw enable
sudo ufw status verbose
```

Kiểm tra trạng thái:

```bash
systemctl is-active ufw
sudo ufw status verbose
```

> `Status: inactive` nghĩa là UFW không lọc gì cả — không phải nguyên nhân
> gây timeout, nhưng cũng không bảo vệ gì.

---

## 9. Kiểm tra toàn bộ

Chạy lần lượt, tất cả phải đạt:

```bash
# 1. Gunicorn
systemctl --user is-active myserver            # active
curl -s http://127.0.0.1:8000/api/hello        # JSON

# 2. Nginx
sudo nginx -t                                  # successful
curl -s -H "Host: gianglab.duckdns.org" http://127.0.0.1/api/hello

# 3. Tunnel
systemctl --user is-active ngrok-myserver      # active
~/myserver/url.sh                              # JSON qua HTTPS

# 4. Tự chạy khi boot
loginctl show-user "$USER" -p Linger           # Linger=yes
systemctl --user is-enabled myserver ngrok-myserver   # enabled

# 5. Từ ngoài Internet
#    Mở URL trên điện thoại dùng 4G (tắt WiFi)
```

Sau khi reboot, kiểm tra lại mục 1–4 mà không đăng nhập thủ công.

---

## 10. Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `Address already in use` khi `python app.py` | Gunicorn đang giữ port 8000. Không cần chạy `app.py`; dùng `systemctl --user status myserver` |
| `502 Bad Gateway` | Gunicorn chết: `systemctl --user restart myserver`, xem `journalctl --user -u myserver -n 50` |
| Nginx trả trang mặc định | `Host` không khớp `server_name`. Test bằng `-H "Host: gianglab.duckdns.org"` hoặc thêm `host_header` cho tunnel |
| `curl` domain treo trên chính máy chủ | Router không hỗ trợ NAT loopback. Test bằng 4G |
| Từ Internet timeout | Kiểm tra theo thứ tự: UFW (`ufw status`) → port forwarding đúng IP `.9` → WAN IP có phải CGNAT → ISP chặn port 80 |
| WAN IP khác IP công khai | CGNAT. Dùng ngrok/Cloudflare Tunnel |
| `field authtoken not found` | Trong `ngrok.yml`, `authtoken` phải nằm dưới `agent:` |
| ngrok hiện "You are about to visit…" | Giới hạn free. Bấm **Visit Site**, hoặc dùng domain riêng + Cloudflare Named Tunnel |
| Service user tắt sau khi logout | Chưa bật linger: `sudo loginctl enable-linger "$USER"` |
| DuckDNS không cập nhật | Chạy tay `~/duckdns/duck.sh`; xem `~/duckdns/duck.log`; kiểm tra `crontab -l` và `systemctl is-active cron` |
| `ln: File exists` | Link sites-enabled đã tồn tại, bỏ qua |
| Upload báo `413 Request Entity Too Large` | Nginx chưa nâng giới hạn: chạy `sudo ~/myserver/setup-nginx-upload.sh` rồi thử lại |
| Upload lớn bị `502` / timeout | Kiểm tra gunicorn có `--timeout 14400` và nginx có `proxy_read_timeout 7200s`, `proxy_request_buffering off` |
| Tệp biến mất sau vài ngày | Đúng cơ chế tệp tạm: xem hạn trong tin nhắn hoặc `/admin/files`; admin đổi thời gian lưu ở `/settings#admin` |
| Không thấy mục Quản trị server | Tài khoản không phải admin. Kiểm tra `sqlite3 data/app.db "SELECT username,is_admin FROM users;"` |
| Admin không xóa được tệp / tin nhắn | Hết phiên CSRF: tải lại trang rồi thao tác lại |

---

## 11. Bảo mật

- **Không** đưa authtoken ngrok / token DuckDNS vào chat, ảnh chụp, git.
  Nếu đã lộ: tạo token mới trên dashboard và cập nhật lại file cấu hình.
- `chmod 600` cho `~/.config/ngrok/ngrok.yml`, `~/duckdns/duck.token`.
- App có đăng nhập nội bộ, nhưng **ai có URL cũng có thể tự đăng ký**. Nếu chỉ
  muốn nhóm kín: thêm HTTP Basic Auth trong nginx (`auth_basic` + `htpasswd`),
  hoặc chặn route `/register`.
- Chỉ tài khoản `is_admin = 1` mới vào được `/admin/files` và lưu cấu hình
  server; các route này vẫn kiểm tra CSRF và quyền ở server.
- Tệp tải lên nằm trong `data/uploads/`; không commit thư mục `data/`.
- Không bật `debug=True` khi đã ra Internet.
- Nếu mở SSH ra ngoài: dùng key, tắt `PasswordAuthentication`.
- Cập nhật hệ thống định kỳ: `sudo apt update && sudo apt upgrade`.

---

## Phụ lục — Cấu trúc file sau khi hoàn tất

```
~/myserver/
├── app.py
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   ├── chat.html
│   ├── settings.html
│   └── admin_files.html
├── static/
│   ├── style.css
│   └── app.js
├── data/                           (tự tạo, gitignore)
│   ├── app.db
│   ├── secret_key                  (chmod 600)
│   └── uploads/                    (tệp tạm, tự xóa)
├── venv/
├── setup-nginx-upload.sh
├── setup-ngrok.sh
├── setup-firewall.sh
├── url.sh
├── README.md
└── SETUP.md

~/.config/systemd/user/
├── myserver.service
└── ngrok-myserver.service

~/.config/ngrok/ngrok.yml          (chmod 600)

~/duckdns/                          (chỉ khi dùng DuckDNS)
├── duck.sh
├── duck.token                      (chmod 600)
└── duck.log

/etc/nginx/sites-available/myserver
/etc/nginx/sites-enabled/myserver -> ../sites-available/myserver
```

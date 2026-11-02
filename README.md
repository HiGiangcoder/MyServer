# MyServer — Flask + Nginx + DuckDNS

> Muốn dựng lại toàn bộ từ máy trắng (kèm nội dung tất cả file cấu hình):
> xem [`SETUP.md`](SETUP.md). File này chỉ tóm tắt trạng thái và cách vận hành.

## Kiến trúc

```
Cách 1 (đang dùng, không cần router):
Internet → ngrok (static domain) → nginx :80 → gunicorn 127.0.0.1:8000 → Flask

Cách 2 (dự phòng, cần port forwarding):
Internet → router (NAT 80) → nginx :80 → gunicorn → Flask
```

Lấy URL công khai hiện tại:

```bash
~/myserver/url.sh
```

## Thành phần đã cấu hình

| Thành phần | Vị trí |
|---|---|
| Flask app | `~/myserver/app.py` |
| Gunicorn (3 worker) | systemd user unit `myserver.service` |
| Nginx reverse proxy | `/etc/nginx/sites-available/myserver` |
| Cập nhật DuckDNS | `~/duckdns/duck.sh` + cron `*/5 * * * *` |
| Token DuckDNS | `~/duckdns/duck.token` (chmod 600) |
| Log DuckDNS | `~/duckdns/duck.log` |

## Lệnh quản trị

```bash
# Flask/gunicorn
systemctl --user status myserver
systemctl --user restart myserver
journalctl --user -u myserver -f

# Nginx
sudo nginx -t && sudo systemctl reload nginx
sudo tail -f /var/log/nginx/access.log

# DuckDNS
~/duckdns/duck.sh && tail -n 5 ~/duckdns/duck.log

# Test nội bộ
curl -H "Host: gianglab.duckdns.org" http://127.0.0.1/api/hello
```

## ngrok static domain (đang bật)

- URL: `https://relish-shown-overbuilt.ngrok-free.dev`
- Service: `ngrok-myserver.service` (systemd user, `Restart=always`, `enabled`)
- Config: `~/.config/ngrok/ngrok.yml` (chmod 600, chứa authtoken)
- Log: `~/.config/ngrok/ngrok.log`
- `addr: 80` + `host_header: gianglab.duckdns.org` → đi qua nginx như bình thường.

```bash
systemctl --user status ngrok-myserver
journalctl --user -u ngrok-myserver -f
~/myserver/url.sh
```

**Hạn chế của ngrok free:** trình duyệt sẽ thấy trang cảnh báo
"You are about to visit..." một lần, bấm **Visit Site** để vào. Client/API
(curl, fetch) không bị ảnh hưởng. Hết hạn mức free thì nâng cấp hoặc
chuyển sang Cloudflare Named Tunnel.

Cloudflare Quick Tunnel vẫn được cài nhưng đã tắt (`cloudflared-quick.service`)
để tránh trùng đường truy cập.

## Việc cần làm để truy cập từ Internet

1. **Mở firewall** (cần sudo):
   ```bash
   sudo ~/myserver/setup-firewall.sh
   ```
2. **Port forwarding trên router** (`http://192.168.1.1`):
   - TCP 80 → `192.168.1.9:80`
   - (tuỳ chọn) TCP 443 → `192.168.1.9:443`
3. Kiểm tra **WAN IP** trên trang status của router:
   - Nếu = `14.191.233.9` → NAT bình thường.
   - Nếu là `100.64.x.x` hoặc IP khác → nhà mạng dùng **CGNAT**, port forwarding
     sẽ không có tác dụng. Giải pháp: Cloudflare Tunnel / Tailscale Funnel /
     xin ISP cấp IP công khai.
4. Nếu ISP chặn inbound port 80, thử forward cổng ngoài khác:
   `TCP 8080 → 192.168.1.9:80`, rồi test `http://gianglab.duckdns.org:8080`.

## HTTPS (sau khi port 80 đã truy cập được từ Internet)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d gianglab.duckdns.org
```

## Bảo mật

- Token DuckDNS đã bị lộ trong chat → vào https://www.duckdns.org đổi token,
  sau đó cập nhật:
  ```bash
  printf '%s\n' 'TOKEN_MOI' > ~/duckdns/duck.token && chmod 600 ~/duckdns/duck.token
  ```
- Không commit `duck.token` lên git.
- Cân nhắc đổi SSH sang key và tắt mật khẩu nếu mở SSH ra Internet.

## Ghi chú

- Nginx `default` site vẫn bật nên truy cập bằng IP LAN (`http://192.168.1.9`)
  sẽ ra trang mặc định của nginx. Dùng domain là đúng route.
- Repo apt của Cursor đang lỗi GPG (`NO_PUBKEY 42A1772E62E492D6`). Sửa bằng:
  ```bash
  sudo install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://downloads.cursor.com/keys/anysphere.asc \
    | sudo gpg --dearmor -o /etc/apt/keyrings/cursor.gpg
  ```
  (hoặc tạm thời comment dòng cursor trong `/etc/apt/sources.list.d/`).

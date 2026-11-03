import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("MYSERVER_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "app.db")
SECRET_PATH = os.path.join(DATA_DIR, "secret_key")

os.makedirs(DATA_DIR, exist_ok=True)


def load_secret_key():
    """Giữ SECRET_KEY cố định giữa các worker và các lần restart."""
    try:
        with open(SECRET_PATH, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass

    key = secrets.token_hex(32)
    fd = os.open(SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=2 * 1024 * 1024 * 1024,
)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")
MAX_MESSAGE_LEN = 2000
ONLINE_SECONDS = 60

DEFAULT_PREFS = {
    "theme": "dark",
    "font_size": "md",
    "enter_to_send": True,
    "sound": False,
    "show_avatars": True,
}

PREF_CHOICES = {
    "theme": {"dark", "light"},
    "font_size": {"sm", "md", "lg"},
}

DEFAULT_CONFIG = {
    "upload_dir": os.path.join(DATA_DIR, "uploads"),
    "retention_hours": 72,
    "max_file_mb": 100,
    "message_retention_days": 0,
    "auto_delete": True,
}

MAX_FILES_PER_MESSAGE = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    settings      TEXT NOT NULL DEFAULT '{}',
    is_admin      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    uploader_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    relative_path TEXT,
    stored_name   TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    size          INTEGER NOT NULL,
    mime          TEXT,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS server_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(recipient_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id, id);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_expires ON attachments(expires_at);
"""


def init_db():
    with sqlite3.connect(DB_PATH, timeout=15) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)

        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "settings" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN settings TEXT NOT NULL DEFAULT '{}'"
            )
        if "is_admin" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )

        # Nếu chưa có admin: ưu tiên username "admin", nếu không lấy user đầu tiên
        has_admin = db.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1"
        ).fetchone()[0]
        if not has_admin:
            admin = db.execute(
                "SELECT id FROM users WHERE username = 'admin' COLLATE NOCASE"
            ).fetchone()
            if admin is None:
                admin = db.execute(
                    "SELECT id FROM users ORDER BY id LIMIT 1"
                ).fetchone()
            if admin is not None:
                db.execute(
                    "UPDATE users SET is_admin = 1 WHERE id = ?", (admin[0],)
                )

    os.makedirs(DEFAULT_CONFIG["upload_dir"], exist_ok=True)


init_db()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def query(sql, args=(), one=False):
    rows = get_db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query(
        "SELECT id, username, display_name, created_at, settings, is_admin "
        "FROM users WHERE id = ?",
        (user_id,),
        one=True,
    )


def normalize_prefs(raw):
    """Trả về dict prefs hợp lệ, thiếu/sai thì dùng mặc định."""
    prefs = dict(DEFAULT_PREFS)
    if not isinstance(raw, dict):
        return prefs

    for key, allowed in PREF_CHOICES.items():
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            prefs[key] = value

    for key in ("enter_to_send", "sound", "show_avatars"):
        if key in raw and isinstance(raw[key], bool):
            prefs[key] = raw[key]

    return prefs


def get_prefs(user):
    try:
        raw = json.loads(user["settings"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    return normalize_prefs(raw)


@app.context_processor
def inject_prefs():
    user = current_user()
    prefs = get_prefs(user) if user else dict(DEFAULT_PREFS)
    return {"ui_prefs": prefs}


def admin_required(view):
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if wants_json():
                return jsonify({"error": "Chưa đăng nhập"}), 401
            return redirect(url_for("login"))

        if not current_user()["is_admin"]:
            if wants_json():
                return jsonify({"error": "Chỉ admin được phép"}), 403
            abort(403)

        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    return wrapped


# --------------------------------------------------------------------------
# Cấu hình server (admin)
# --------------------------------------------------------------------------


def get_config():
    row = query(
        "SELECT value FROM server_settings WHERE key = 'config'", one=True
    )
    raw = {}
    if row:
        try:
            loaded = json.loads(row["value"])
            if isinstance(loaded, dict):
                raw = loaded
        except (TypeError, ValueError):
            raw = {}

    cfg = dict(DEFAULT_CONFIG)

    upload_dir = raw.get("upload_dir")
    if isinstance(upload_dir, str) and upload_dir.strip():
        cfg["upload_dir"] = upload_dir.strip()

    try:
        cfg["retention_hours"] = max(
            1, min(24 * 365, int(raw.get("retention_hours", cfg["retention_hours"])))
        )
    except (TypeError, ValueError):
        pass

    try:
        cfg["max_file_mb"] = max(
            1, min(1024, int(raw.get("max_file_mb", cfg["max_file_mb"])))
        )
    except (TypeError, ValueError):
        pass

    try:
        cfg["message_retention_days"] = max(
            0,
            min(
                3650,
                int(raw.get("message_retention_days", cfg["message_retention_days"])),
            ),
        )
    except (TypeError, ValueError):
        pass

    if isinstance(raw.get("auto_delete"), bool):
        cfg["auto_delete"] = raw["auto_delete"]

    return cfg


def normalize_config(data):
    cfg = dict(DEFAULT_CONFIG)

    upload_dir = str(data.get("upload_dir") or "").strip()
    if not upload_dir:
        upload_dir = DEFAULT_CONFIG["upload_dir"]
    if not os.path.isabs(upload_dir):
        upload_dir = os.path.join(BASE_DIR, upload_dir)
    cfg["upload_dir"] = os.path.normpath(upload_dir)

    def as_int(key, low, high, fallback):
        try:
            return max(low, min(high, int(data.get(key, fallback))))
        except (TypeError, ValueError):
            return fallback

    cfg["retention_hours"] = as_int("retention_hours", 1, 24 * 365, 72)
    cfg["max_file_mb"] = as_int("max_file_mb", 1, 1024, 100)
    cfg["message_retention_days"] = as_int(
        "message_retention_days", 0, 3650, 0
    )
    cfg["auto_delete"] = bool(data.get("auto_delete"))
    return cfg


def save_config(cfg):
    execute(
        "INSERT INTO server_settings (key, value) VALUES ('config', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(cfg),),
    )


def ensure_upload_dir(cfg):
    path = cfg["upload_dir"]
    os.makedirs(path, exist_ok=True)
    if not os.access(path, os.W_OK):
        raise OSError(f"Thư mục không ghi được: {path}")
    return path


# --------------------------------------------------------------------------
# Tệp đính kèm + dọn dẹp
# --------------------------------------------------------------------------


def safe_remove(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def serialize_attachment(row):
    mime = row["mime"] or ""
    return {
        "id": row["id"],
        "name": row["relative_path"] or row["original_name"],
        "size": row["size"],
        "mime": mime,
        "is_image": mime.startswith("image/"),
        "expires_at": row["expires_at"],
        "url": url_for("download_file", attachment_id=row["id"]),
    }


def with_attachments(messages):
    ids = [m["id"] for m in messages]
    if not ids:
        return messages

    placeholders = ",".join("?" for _ in ids)
    rows = query(
        f"SELECT * FROM attachments WHERE message_id IN ({placeholders}) ORDER BY id",
        ids,
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(
            serialize_attachment(row)
        )

    for message in messages:
        message["attachments"] = grouped.get(message["id"], [])
    return messages


def delete_messages_where(where_sql, args=()):
    total = 0
    while True:
        rows = query(
            f"SELECT id FROM messages WHERE {where_sql} ORDER BY id LIMIT 5000",
            args,
        )
        ids = [row["id"] for row in rows]
        if not ids:
            return total

        placeholders = ",".join("?" for _ in ids)
        files = query(
            f"SELECT stored_path FROM attachments WHERE message_id IN ({placeholders})",
            ids,
        )
        for file_row in files:
            safe_remove(file_row["stored_path"])

        execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        total += len(ids)

        if len(ids) < 5000:
            return total


def cleanup_expired(force=False):
    cfg = get_config()
    removed = 0

    if cfg["auto_delete"] or force:
        now = now_iso()
        expired = query(
            "SELECT id, stored_path FROM attachments WHERE expires_at <= ?", (now,)
        )
        for row in expired:
            safe_remove(row["stored_path"])
        if expired:
            execute("DELETE FROM attachments WHERE expires_at <= ?", (now,))
            removed += len(expired)

    # Tin nhắn không còn nội dung và không còn tệp (chỉ xóa tin cũ hơn 1 giờ
    # để không đụng vào upload đang được ghi dở)
    empty_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds")
    execute(
        "DELETE FROM messages WHERE body = '' AND created_at < ? "
        "AND id NOT IN (SELECT message_id FROM attachments)",
        (empty_cutoff,),
    )

    if cfg["message_retention_days"] > 0:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=cfg["message_retention_days"])
        ).isoformat(timespec="seconds")
        removed += delete_messages_where("created_at < ?", (cutoff,))

    return removed


_cleanup_started = False


def start_cleanup_thread():
    global _cleanup_started
    if os.environ.get("MYSERVER_DISABLE_CLEANUP") == "1":
        return
    if _cleanup_started:
        return
    _cleanup_started = True

    def loop():
        time.sleep(15)
        while True:
            try:
                with app.app_context():
                    cleanup_expired()
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="cleanup").start()


def wants_json():
    return request.path.startswith("/api/")


def login_required(view):
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if wants_json():
                return jsonify({"error": "Chưa đăng nhập"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    return wrapped


def valid_csrf():
    token = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or ""
    )
    expected = session.get("csrf", "")
    return bool(token) and bool(expected) and secrets.compare_digest(token, expected)


def ensure_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def touch_user(user_id):
    execute("UPDATE users SET last_seen = ? WHERE id = ?", (now_iso(), user_id))


def serialize_message(row, me_id):
    return {
        "id": row["id"],
        "body": row["body"],
        "created_at": row["created_at"],
        "username": row["username"],
        "display_name": row["display_name"],
        "mine": row["sender_id"] == me_id,
    }


def fetch_room_messages(me_id, since):
    rows = query(
        """
        SELECT * FROM (
            SELECT m.id, m.body, m.created_at, m.sender_id,
                   u.username, u.display_name
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.recipient_id IS NULL AND m.id > ?
            ORDER BY m.id DESC
            LIMIT 200
        )
        ORDER BY id
        """,
        (since,),
    )
    return with_attachments([serialize_message(r, me_id) for r in rows])


def fetch_dm_messages(me_id, peer_id, since):
    rows = query(
        """
        SELECT * FROM (
            SELECT m.id, m.body, m.created_at, m.sender_id,
                   u.username, u.display_name
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.recipient_id IS NOT NULL
              AND ((m.sender_id = ? AND m.recipient_id = ?)
                OR (m.sender_id = ? AND m.recipient_id = ?))
              AND m.id > ?
            ORDER BY m.id DESC
            LIMIT 200
        )
        ORDER BY id
        """,
        (me_id, peer_id, peer_id, me_id, since),
    )
    return with_attachments([serialize_message(r, me_id) for r in rows])


def parse_since():
    try:
        return max(0, int(request.args.get("since", 0)))
    except (TypeError, ValueError):
        return 0


def read_json_body():
    data = request.get_json(silent=True) or {}
    body = data.get("body", "")
    if not isinstance(body, str):
        return None
    body = body.strip()
    if not body or len(body) > MAX_MESSAGE_LEN:
        return None
    return body


# --------------------------------------------------------------------------
# Trang
# --------------------------------------------------------------------------


@app.route("/")
@login_required
def index():
    me = current_user()
    cfg = get_config()
    return render_template(
        "chat.html",
        me=me,
        csrf=ensure_csrf(),
        upload_cfg={
            "max_file_mb": cfg["max_file_mb"],
            "retention_hours": cfg["retention_hours"],
        },
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("index"))

    error = None
    values = {"username": "", "display_name": ""}

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        display_name = (request.form.get("display_name") or "").strip()
        password = request.form.get("password") or ""
        values = {"username": username, "display_name": display_name}

        if not USERNAME_RE.match(username):
            error = "Tên đăng nhập 3–20 ký tự, chỉ gồm chữ, số và dấu gạch dưới."
        elif not (1 <= len(display_name) <= 40):
            error = "Tên hiển thị phải từ 1 đến 40 ký tự."
        elif len(password) < 6:
            error = "Mật khẩu phải có ít nhất 6 ký tự."
        elif query("SELECT 1 FROM users WHERE username = ?", (username,), one=True):
            error = "Tên đăng nhập đã tồn tại."
        else:
            execute(
                """
                INSERT INTO users
                    (username, display_name, password_hash, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    username,
                    display_name,
                    generate_password_hash(password),
                    now_iso(),
                    now_iso(),
                ),
            )
            session.clear()
            user = query(
                "SELECT id FROM users WHERE username = ?", (username,), one=True
            )
            # Người dùng đầu tiên trên server mới trở thành admin
            if query(
                "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1", one=True
            )["n"] == 0:
                execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
            session["user_id"] = user["id"]
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"))

    return render_template("register.html", error=error, values=values)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))

    error = None
    username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = query(
            """
            SELECT id, password_hash FROM users
            WHERE username = ? COLLATE NOCASE
            """,
            (username,),
            one=True,
        )

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["csrf"] = secrets.token_urlsafe(32)
            touch_user(user["id"])
            return redirect(url_for("index"))

        error = "Sai tên đăng nhập hoặc mật khẩu."

    return render_template("login.html", error=error, username=username)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    if not valid_csrf():
        return jsonify({"error": "CSRF token không hợp lệ"}), 403
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Cài đặt tài khoản / giao diện
# --------------------------------------------------------------------------


@app.get("/settings")
@login_required
def settings_page():
    me = current_user()
    return render_template(
        "settings.html",
        me=me,
        prefs=get_prefs(me),
        csrf=ensure_csrf(),
        cfg=get_config() if me["is_admin"] else None,
    )


@app.post("/settings/profile")
@login_required
def settings_profile():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("settings_page"))

    me = current_user()
    username = (request.form.get("username") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()

    if not USERNAME_RE.match(username):
        flash("Tên đăng nhập 3–20 ký tự, chỉ gồm chữ, số và dấu gạch dưới.", "error")
    elif not (1 <= len(display_name) <= 40):
        flash("Tên hiển thị phải từ 1 đến 40 ký tự.", "error")
    elif query(
        "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE AND id <> ?",
        (username, me["id"]),
        one=True,
    ):
        flash("Tên đăng nhập này đã có người dùng.", "error")
    else:
        execute(
            "UPDATE users SET username = ?, display_name = ? WHERE id = ?",
            (username, display_name, me["id"]),
        )
        flash("Đã cập nhật hồ sơ.", "ok")

    return redirect(url_for("settings_page"))


@app.post("/settings/password")
@login_required
def settings_password():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("settings_page"))

    me = current_user()
    row = query(
        "SELECT password_hash FROM users WHERE id = ?", (me["id"],), one=True
    )
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not check_password_hash(row["password_hash"], current_password):
        flash("Mật khẩu hiện tại không đúng.", "error")
    elif len(new_password) < 6:
        flash("Mật khẩu mới phải có ít nhất 6 ký tự.", "error")
    elif new_password != confirm_password:
        flash("Hai lần nhập mật khẩu mới không khớp.", "error")
    elif new_password == current_password:
        flash("Mật khẩu mới phải khác mật khẩu hiện tại.", "error")
    else:
        execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), me["id"]),
        )
        # Giữ đăng nhập nhưng xoay CSRF token
        session.clear()
        session["user_id"] = me["id"]
        session["csrf"] = secrets.token_urlsafe(32)
        flash("Đã đổi mật khẩu.", "ok")

    return redirect(url_for("settings_page"))


@app.post("/settings/delete")
@login_required
def settings_delete():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("settings_page"))

    me = current_user()
    row = query(
        "SELECT password_hash FROM users WHERE id = ?", (me["id"],), one=True
    )
    password = request.form.get("password") or ""
    confirm_text = (request.form.get("confirm_text") or "").strip().upper()

    if not check_password_hash(row["password_hash"], password):
        flash("Mật khẩu không đúng, tài khoản chưa bị xóa.", "error")
        return redirect(url_for("settings_page"))

    if confirm_text != "XOA":
        flash('Gõ chính xác "XOA" để xác nhận xóa tài khoản.', "error")
        return redirect(url_for("settings_page"))

    # Xóa tệp trên ổ đĩa trước khi CASCADE xóa tin nhắn + attachment rows
    files = query(
        """
        SELECT stored_path FROM attachments
        WHERE uploader_id = ?
           OR message_id IN (
                SELECT id FROM messages
                WHERE sender_id = ? OR recipient_id = ?
           )
        """,
        (me["id"], me["id"], me["id"]),
    )
    for file_row in files:
        safe_remove(file_row["stored_path"])

    # FOREIGN KEY ON DELETE CASCADE xóa luôn tin nhắn của người này
    execute("DELETE FROM users WHERE id = ?", (me["id"],))
    session.clear()
    return redirect(url_for("register", deleted=1))


@app.post("/settings/prefs")
@login_required
def settings_prefs():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("settings_page"))

    me = current_user()
    prefs = normalize_prefs(
        {
            "theme": request.form.get("theme", DEFAULT_PREFS["theme"]),
            "font_size": request.form.get("font_size", DEFAULT_PREFS["font_size"]),
            "enter_to_send": "enter_to_send" in request.form,
            "sound": "sound" in request.form,
            "show_avatars": "show_avatars" in request.form,
        }
    )
    execute(
        "UPDATE users SET settings = ? WHERE id = ?",
        (json.dumps(prefs), me["id"]),
    )
    flash("Đã lưu cài đặt chat.", "ok")
    return redirect(url_for("settings_page") + "#chat")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@app.get("/api/me")
@login_required
def api_me():
    me = current_user()
    return jsonify(
        {
            "id": me["id"],
            "username": me["username"],
            "display_name": me["display_name"],
        }
    )


@app.get("/api/users")
@login_required
def api_users():
    me = current_user()
    cutoff = datetime.now(timezone.utc).timestamp() - ONLINE_SECONDS
    rows = query(
        """
        SELECT id, username, display_name, last_seen
        FROM users
        ORDER BY display_name COLLATE NOCASE
        """
    )

    users = []
    for row in rows:
        try:
            last_seen = datetime.fromisoformat(row["last_seen"]).timestamp()
        except ValueError:
            last_seen = 0
        users.append(
            {
                "id": row["id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "online": row["id"] == me["id"] or last_seen >= cutoff,
                "is_me": row["id"] == me["id"],
            }
        )

    return jsonify({"users": users})


@app.post("/api/heartbeat")
@login_required
def api_heartbeat():
    if not valid_csrf():
        return jsonify({"error": "CSRF token không hợp lệ"}), 403
    touch_user(current_user()["id"])
    return jsonify({"ok": True})


@app.get("/api/chat")
@login_required
def api_chat_get():
    me = current_user()
    return jsonify({"messages": fetch_room_messages(me["id"], parse_since())})


@app.post("/api/chat")
@login_required
def api_chat_post():
    if not valid_csrf():
        return jsonify({"error": "CSRF token không hợp lệ"}), 403

    body = read_json_body()
    if body is None:
        return jsonify({"error": "Tin nhắn rỗng hoặc quá dài"}), 400

    me = current_user()
    touch_user(me["id"])
    cur = execute(
        "INSERT INTO messages (sender_id, recipient_id, body, created_at) "
        "VALUES (?, NULL, ?, ?)",
        (me["id"], body, now_iso()),
    )
    row = query(
        """
        SELECT m.id, m.body, m.created_at, m.sender_id,
               u.username, u.display_name
        FROM messages m JOIN users u ON u.id = m.sender_id
        WHERE m.id = ?
        """,
        (cur.lastrowid,),
        one=True,
    )
    message = with_attachments([serialize_message(row, me["id"])])[0]
    return jsonify({"message": message}), 201


@app.get("/api/dm/<username>")
@login_required
def api_dm_get(username):
    me = current_user()
    peer = query(
        "SELECT id, username, display_name FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
        one=True,
    )
    if peer is None:
        return jsonify({"error": "Không tìm thấy người dùng"}), 404

    return jsonify(
        {
            "peer": {
                "username": peer["username"],
                "display_name": peer["display_name"],
            },
            "messages": fetch_dm_messages(me["id"], peer["id"], parse_since()),
        }
    )


@app.post("/api/dm/<username>")
@login_required
def api_dm_post(username):
    if not valid_csrf():
        return jsonify({"error": "CSRF token không hợp lệ"}), 403

    body = read_json_body()
    if body is None:
        return jsonify({"error": "Tin nhắn rỗng hoặc quá dài"}), 400

    me = current_user()
    peer = query(
        "SELECT id, username FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
        one=True,
    )
    if peer is None:
        return jsonify({"error": "Không tìm thấy người dùng"}), 404
    if peer["id"] == me["id"]:
        return jsonify({"error": "Không thể tự nhắn tin cho chính mình"}), 400

    touch_user(me["id"])
    cur = execute(
        "INSERT INTO messages (sender_id, recipient_id, body, created_at) "
        "VALUES (?, ?, ?, ?)",
        (me["id"], peer["id"], body, now_iso()),
    )
    row = query(
        """
        SELECT m.id, m.body, m.created_at, m.sender_id,
               u.username, u.display_name
        FROM messages m JOIN users u ON u.id = m.sender_id
        WHERE m.id = ?
        """,
        (cur.lastrowid,),
        one=True,
    )
    message = with_attachments([serialize_message(row, me["id"])])[0]
    return jsonify({"message": message}), 201


# --------------------------------------------------------------------------
# Tệp đính kèm
# --------------------------------------------------------------------------


def clean_relative_path(raw):
    raw = (raw or "").replace("\\", "/")
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if part in ("", ".", ".."):
            continue
        parts.append(part[:120])
    return "/".join(parts)[:250]


@app.post("/api/upload")
@login_required
def api_upload():
    if not valid_csrf():
        return jsonify({"error": "CSRF token không hợp lệ"}), 403

    me = current_user()
    touch_user(me["id"])

    target = request.form.get("target", "room")
    body = (request.form.get("body") or "").strip()
    if len(body) > MAX_MESSAGE_LEN:
        return jsonify({"error": "Nội dung tin nhắn quá dài"}), 400

    recipient_id = None
    if target == "dm":
        username = (request.form.get("username") or "").strip()
        peer = query(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
            one=True,
        )
        if peer is None:
            return jsonify({"error": "Không tìm thấy người dùng"}), 404
        if peer["id"] == me["id"]:
            return jsonify({"error": "Không thể tự nhắn tin cho chính mình"}), 400
        recipient_id = peer["id"]

    files = [f for f in request.files.getlist("files") if f and f.filename]
    if len(files) > MAX_FILES_PER_MESSAGE:
        return jsonify(
            {"error": f"Tối đa {MAX_FILES_PER_MESSAGE} tệp mỗi tin nhắn"}
        ), 400
    if not files and not body:
        return jsonify({"error": "Chưa có nội dung hoặc tệp"}), 400

    cfg = get_config()
    try:
        upload_dir = ensure_upload_dir(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    message_id = execute(
        "INSERT INTO messages (sender_id, recipient_id, body, created_at) "
        "VALUES (?, ?, ?, ?)",
        (me["id"], recipient_id, body, now_iso()),
    ).lastrowid

    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=cfg["retention_hours"])
    ).isoformat(timespec="seconds")
    max_bytes = cfg["max_file_mb"] * 1024 * 1024
    saved_paths = []

    try:
        for storage in files:
            rel = clean_relative_path(storage.filename)
            original = (rel.split("/")[-1] or "file")[:180]
            ext = os.path.splitext(original)[1][:16]
            stored_name = uuid.uuid4().hex + ext
            dest = os.path.join(upload_dir, stored_name)
            saved_paths.append(dest)

            size = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = storage.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(
                            f"Tệp “{original}” vượt quá {cfg['max_file_mb']} MB"
                        )
                    out.write(chunk)

            execute(
                """
                INSERT INTO attachments
                    (message_id, uploader_id, original_name, relative_path,
                     stored_name, stored_path, size, mime, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    me["id"],
                    original,
                    rel,
                    stored_name,
                    dest,
                    size,
                    storage.mimetype or "application/octet-stream",
                    now_iso(),
                    expires_at,
                ),
            )
    except ValueError as exc:
        for path in saved_paths:
            safe_remove(path)
        execute("DELETE FROM messages WHERE id = ?", (message_id,))
        return jsonify({"error": str(exc)}), 413
    except OSError:
        for path in saved_paths:
            safe_remove(path)
        execute("DELETE FROM messages WHERE id = ?", (message_id,))
        return jsonify({"error": "Không ghi được tệp lên server"}), 500

    row = query(
        """
        SELECT m.id, m.body, m.created_at, m.sender_id,
               u.username, u.display_name
        FROM messages m JOIN users u ON u.id = m.sender_id
        WHERE m.id = ?
        """,
        (message_id,),
        one=True,
    )
    message = with_attachments([serialize_message(row, me["id"])])[0]
    return jsonify({"message": message}), 201


@app.get("/files/<int:attachment_id>")
@login_required
def download_file(attachment_id):
    row = query(
        """
        SELECT a.*, m.sender_id AS message_sender, m.recipient_id
        FROM attachments a
        JOIN messages m ON m.id = a.message_id
        WHERE a.id = ?
        """,
        (attachment_id,),
        one=True,
    )
    if row is None:
        abort(404)

    me = current_user()
    if (
        row["recipient_id"] is not None
        and not me["is_admin"]
        and me["id"] not in (row["message_sender"], row["recipient_id"])
    ):
        abort(403)

    if not os.path.isfile(row["stored_path"]):
        abort(404)

    mime = row["mime"] or ""
    # SVG có thể chứa script khi mở trực tiếp -> luôn tải xuống, không inline
    inline = mime.startswith("image/") and mime != "image/svg+xml"
    response = send_file(
        row["stored_path"],
        mimetype=mime or None,
        as_attachment=not inline,
        download_name=row["original_name"],
        conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# --------------------------------------------------------------------------
# Quản trị (admin)
# --------------------------------------------------------------------------


@app.post("/settings/admin")
@admin_required
def settings_admin():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("settings_page") + "#admin")

    cfg = normalize_config(request.form)
    try:
        ensure_upload_dir(cfg)
    except OSError as exc:
        flash(f"Không dùng được thư mục lưu tệp: {exc}", "error")
        return redirect(url_for("settings_page") + "#admin")

    save_config(cfg)
    flash("Đã lưu cấu hình server.", "ok")
    return redirect(url_for("settings_page") + "#admin")


@app.get("/admin/files")
@admin_required
def admin_files():
    sort = request.args.get("sort", "size")
    order = {
        "size": "a.size DESC",
        "date": "a.created_at DESC",
        "name": "a.original_name COLLATE NOCASE ASC",
        "expires": "a.expires_at ASC",
    }.get(sort, "a.size DESC")

    files = query(
        f"""
        SELECT a.*, u.username, u.display_name
        FROM attachments a
        JOIN users u ON u.id = a.uploader_id
        ORDER BY {order}
        """
    )
    total = sum(row["size"] for row in files)

    return render_template(
        "admin_files.html",
        me=current_user(),
        files=files,
        total=total,
        sort=sort,
        cfg=get_config(),
        csrf=ensure_csrf(),
    )


@app.post("/admin/files/delete")
@admin_required
def admin_files_delete():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("admin_files"))

    ids = []
    for raw in request.form.getlist("ids"):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    if not ids:
        flash("Chưa chọn tệp nào.", "error")
        return redirect(url_for("admin_files"))

    placeholders = ",".join("?" for _ in ids)
    rows = query(
        f"SELECT stored_path FROM attachments WHERE id IN ({placeholders})", ids
    )
    for row in rows:
        safe_remove(row["stored_path"])

    execute(f"DELETE FROM attachments WHERE id IN ({placeholders})", ids)
    execute(
        "DELETE FROM messages WHERE body = '' "
        "AND id NOT IN (SELECT message_id FROM attachments)"
    )

    flash(f"Đã xóa {len(rows)} tệp.", "ok")
    return redirect(url_for("admin_files"))


@app.post("/admin/cleanup")
@admin_required
def admin_cleanup():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("admin_files"))

    removed = cleanup_expired(force=True)
    flash(f"Đã dọn {removed} mục hết hạn.", "ok")
    return redirect(url_for("admin_files"))


@app.post("/admin/messages/purge")
@admin_required
def admin_messages_purge():
    if not valid_csrf():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("settings_page") + "#admin")

    mode = request.form.get("mode")

    if mode == "all":
        removed = delete_messages_where("1 = 1")
        flash(f"Đã xóa toàn bộ {removed} tin nhắn.", "ok")
    else:
        days = get_config()["message_retention_days"]
        if days <= 0:
            flash(
                "Chưa đặt số ngày lưu tin nhắn (đang là 0 = giữ mãi).", "error"
            )
            return redirect(url_for("settings_page") + "#admin")

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat(timespec="seconds")
        removed = delete_messages_where("created_at < ?", (cutoff,))
        flash(f"Đã xóa {removed} tin nhắn cũ hơn {days} ngày.", "ok")

    return redirect(url_for("settings_page") + "#admin")


# --------------------------------------------------------------------------
# API cũ (giữ để không phá vỡ kiểm tra trước đó)
# --------------------------------------------------------------------------


@app.route("/api/hello")
def hello():
    return jsonify(
        {
            "message": "Hello from my personal server!",
            "backend": "Flask",
            "status": "online",
        }
    )


@app.route("/api/info")
def info():
    return jsonify({"server": "My Linux Mint PC", "api_version": "1.0"})


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": "Dữ liệu gửi lên quá lớn"}), 413


start_cleanup_thread()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)

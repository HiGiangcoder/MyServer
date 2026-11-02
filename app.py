import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
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
    MAX_CONTENT_LENGTH=64 * 1024,
)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")
MAX_MESSAGE_LEN = 2000
ONLINE_SECONDS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(recipient_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id, id);
"""


def init_db():
    with sqlite3.connect(DB_PATH, timeout=15) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)


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
        "SELECT id, username, display_name, created_at FROM users WHERE id = ?",
        (user_id,),
        one=True,
    )


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
    return [serialize_message(r, me_id) for r in rows]


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
    return [serialize_message(r, me_id) for r in rows]


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
    return render_template("chat.html", me=me, csrf=ensure_csrf())


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
    return jsonify({"message": serialize_message(row, me["id"])}), 201


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
    return jsonify({"message": serialize_message(row, me["id"])}), 201


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)

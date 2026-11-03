import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

BASE = tempfile.mkdtemp(prefix="myserver-test-")
os.environ["MYSERVER_DATA_DIR"] = BASE
os.environ["MYSERVER_DISABLE_CLEANUP"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as mod  # noqa: E402

mod.app.config.update(TESTING=True)


def csrf(client):
    with client.session_transaction() as sess:
        if "csrf" not in sess:
            sess["csrf"] = "testcsrf"
        return sess["csrf"]


def register(client, username, display="Test User"):
    return client.post(
        "/register",
        data={
            "username": username,
            "display_name": display,
            "password": "secret123",
        },
        follow_redirects=False,
    )


def login(client, username, password="secret123"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def upload(client, name, data, target="room", username=None, body="", mime=None):
    payload = {
        "target": target,
        "body": body,
        "csrf_token": csrf(client),
        "files": (io.BytesIO(data), name) if mime is None else (io.BytesIO(data), name, mime),
    }
    if username:
        payload["username"] = username
    return client.post("/api/upload", data=payload, content_type="multipart/form-data")


failures = []


def check(cond, label, extra=""):
    if cond:
        print(f"  ok  {label}")
    else:
        failures.append(label)
        print(f" FAIL {label} {extra}")


try:
    admin = mod.app.test_client()
    bob = mod.app.test_client()
    eve = mod.app.test_client()

    register(admin, "admin", "Quản trị")
    register(bob, "bob", "Bob")
    register(eve, "eve", "Eve")

    with mod.app.app_context():
        rows = mod.query("SELECT username, is_admin FROM users ORDER BY id")
        admin_row = [r for r in rows if r["username"] == "admin"][0]
        bob_row = [r for r in rows if r["username"] == "bob"][0]
        check(admin_row["is_admin"] == 1, "first registered user is admin")
        check(bob_row["is_admin"] == 0, "second registered user is not admin")

    # ---- Admin vs non-admin access ----
    login(admin, "admin")
    login(bob, "bob")
    login(eve, "eve")

    r = bob.get("/admin/files")
    check(r.status_code == 403, "non-admin cannot open file manager", r.status_code)
    r = bob.post(
        "/settings/admin",
        data={"csrf_token": csrf(bob), "upload_dir": "/tmp/x"},
        follow_redirects=False,
    )
    check(r.status_code == 403, "non-admin cannot save server config", r.status_code)

    r = admin.get("/admin/files")
    check(r.status_code == 200, "admin can open file manager", r.status_code)
    r = admin.get("/")
    check(r.status_code == 200 and b"window.APP" in r.data, "admin chat page renders", r.status_code)
    r = bob.get("/")
    check(r.status_code == 200 and b'"is_admin": false' in r.data, "non-admin chat page renders", r.status_code)
    r = admin.get("/settings")
    check(r.status_code == 200 and b'id="admin"' in r.data, "admin settings has admin card")
    r = bob.get("/settings")
    check(r.status_code == 200 and b'id="admin"' not in r.data, "user settings hides admin card")

    # ---- Upload single file ----
    r = upload(admin, "myfolder/sub/hello.txt", b"hello world", body="xin chao")
    check(r.status_code == 201, "upload returns 201", r.status_code)
    payload = r.get_json() or {}
    msg = payload.get("message") or {}
    atts = msg.get("attachments") or []
    check(len(atts) == 1, "one attachment serialized", atts)
    if atts:
        att = atts[0]
        check(att["name"] == "myfolder/sub/hello.txt", "relative folder path kept", att["name"])
        check(att["size"] == 11, "attachment size correct", att["size"])
        check(bool(att["expires_at"]), "attachment has expires_at", att["expires_at"])
        check(att["url"].startswith("/files/"), "attachment has download url", att["url"])
        att_id = att["id"]
        with mod.app.app_context():
            row = mod.query("SELECT stored_path FROM attachments WHERE id = ?", (att_id,), one=True)
            check(row and os.path.isfile(row["stored_path"]), "file exists on disk")

    # Download permissions on a room message
    r = admin.get(f"/files/{att_id}")
    check(r.status_code == 200 and r.data == b"hello world", "admin downloads room file", r.status_code)
    r = bob.get(f"/files/{att_id}")
    check(r.status_code == 200, "any user can download room file", r.status_code)

    # ---- DM attachment permissions ----
    r = upload(admin, "secret.txt", b"dm secret", target="dm", username="bob")
    check(r.status_code == 201, "DM upload ok", r.status_code)
    dm_att = r.get_json()["message"]["attachments"][0]
    r = eve.get(f"/files/{dm_att['id']}")
    check(r.status_code == 403, "third user cannot download DM file", r.status_code)
    r = bob.get(f"/files/{dm_att['id']}")
    check(r.status_code == 200, "DM recipient can download", r.status_code)
    r = admin.get(f"/files/{dm_att['id']}")
    check(r.status_code == 200, "admin can download DM file", r.status_code)

    # ---- Config save + validation ----
    newdir = os.path.join(BASE, "custom-uploads")
    r = admin.post(
        "/settings/admin",
        data={
            "csrf_token": csrf(admin),
            "upload_dir": newdir,
            "retention_hours": "5",
            "max_file_mb": "1",
            "message_retention_days": "3",
            "auto_delete": "on",
        },
        follow_redirects=False,
    )
    check(r.status_code == 302, "admin config saves", r.status_code)
    with mod.app.app_context():
        cfg = mod.get_config()
        check(cfg["retention_hours"] == 5, "retention_hours saved", cfg)
        check(cfg["max_file_mb"] == 1, "max_file_mb saved", cfg)
        check(cfg["message_retention_days"] == 3, "message_retention_days saved", cfg)
        check(cfg["auto_delete"] is True, "auto_delete saved", cfg)
        check(cfg["upload_dir"] == newdir, "upload_dir saved", cfg)
        check(os.path.isdir(newdir), "upload dir created")

    r = upload(admin, "after-config.txt", b"x" * 100)
    check(r.status_code == 201, "upload uses new config dir", r.status_code)
    with mod.app.app_context():
        p = mod.query("SELECT stored_path FROM attachments ORDER BY id DESC LIMIT 1", one=True)["stored_path"]
        check(os.path.dirname(p) == newdir, "new file stored in configured dir", p)

    # ---- Per-file size limit ----
    r = upload(admin, "toobig.bin", b"x" * (2 * 1024 * 1024))
    check(r.status_code == 413, "oversized file rejected with 413", r.status_code)
    with mod.app.app_context():
        check(
            mod.query("SELECT COUNT(*) AS n FROM attachments WHERE original_name='toobig.bin'", one=True)["n"] == 0,
            "rejected upload leaves no attachment row",
        )
        check(
            mod.query("SELECT COUNT(*) AS n FROM messages WHERE body='' AND id NOT IN (SELECT message_id FROM attachments)", one=True)["n"] == 0,
            "rejected upload leaves no empty message",
        )

    # ---- Admin file manager sorting / manual delete ----
    r = admin.get("/admin/files?sort=size")
    check(r.status_code == 200 and "Nặng nhất".encode() in r.data, "file manager renders")
    r = admin.get("/admin/files?sort=expires")
    check(r.status_code == 200, "file manager sort=expires")

    with mod.app.app_context():
        victim = mod.query("SELECT id, stored_path FROM attachments ORDER BY id LIMIT 1", one=True)
    r = admin.post(
        "/admin/files/delete",
        data={"csrf_token": csrf(admin), "ids": [str(victim["id"])]},
        follow_redirects=False,
    )
    check(r.status_code == 302, "manual delete redirects", r.status_code)
    with mod.app.app_context():
        check(mod.query("SELECT 1 FROM attachments WHERE id = ?", (victim["id"],), one=True) is None,
              "attachment row deleted")
        check(not os.path.isfile(victim["stored_path"]), "manual delete removes disk file")

    # non-admin cannot delete
    r = bob.post("/admin/files/delete", data={"csrf_token": csrf(bob), "ids": ["1"]})
    check(r.status_code == 403, "non-admin cannot delete files", r.status_code)

    # ---- Cleanup expired ----
    with mod.app.app_context():
        row = mod.query("SELECT id, stored_path FROM attachments LIMIT 1", one=True)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        mod.execute("UPDATE attachments SET expires_at = ? WHERE id = ?", (past, row["id"]))
        removed = mod.cleanup_expired(force=True)
        check(removed >= 1, "cleanup reports removed entries", removed)
        check(not os.path.isfile(row["stored_path"]), "cleanup removes expired disk file")
        check(mod.query("SELECT 1 FROM attachments WHERE id = ?", (row["id"],), one=True) is None,
              "cleanup deletes expired attachment row")

    # ---- SVG không được serve inline ----
    r = upload(admin, "x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", mime="image/svg+xml")
    check(r.status_code == 201, "svg upload ok", r.status_code)
    svg_att = r.get_json()["message"]["attachments"][0]
    r = admin.get(f"/files/{svg_att['id']}")
    check(r.status_code == 200, "svg downloadable", r.status_code)
    check("attachment" in (r.headers.get("Content-Disposition") or ""), "svg forced as attachment")

    r = upload(admin, "pic.png", b"\x89PNG\r\n\x1a\nfake", mime="image/png")
    png_att = r.get_json()["message"]["attachments"][0]
    r = admin.get(f"/files/{png_att['id']}")
    check(r.status_code == 200 and "attachment" not in (r.headers.get("Content-Disposition") or ""),
          "raster image served inline")
    check(r.headers.get("X-Content-Type-Options") == "nosniff", "nosniff header present")

    # ---- Xóa tài khoản xóa tệp trên đĩa ----
    r = upload(eve, "eve-secret.bin", b"eve data")
    check(r.status_code == 201, "eve upload ok", r.status_code)
    eve_att = r.get_json()["message"]["attachments"][0]
    with mod.app.app_context():
        eve_path = mod.query("SELECT stored_path FROM attachments WHERE id = ?", (eve_att["id"],), one=True)["stored_path"]
    r = eve.post(
        "/settings/delete",
        data={"csrf_token": csrf(eve), "password": "secret123", "confirm_text": "XOA"},
        follow_redirects=False,
    )
    check(r.status_code == 302, "delete account redirects", r.status_code)
    check(not os.path.isfile(eve_path), "delete account removes uploaded file from disk")
    with mod.app.app_context():
        check(mod.query("SELECT 1 FROM users WHERE username='eve'", one=True) is None, "delete account removes user")
        check(mod.query("SELECT 1 FROM attachments WHERE id = ?", (eve_att["id"],), one=True) is None,
              "delete account cascades attachment row")

    # ---- Message purge all (also removes attachments) ----
    with mod.app.app_context():
        paths = [r["stored_path"] for r in mod.query("SELECT stored_path FROM attachments")]
        before_msgs = mod.query("SELECT COUNT(*) AS n FROM messages", one=True)["n"]
    r = admin.post(
        "/admin/messages/purge",
        data={"csrf_token": csrf(admin), "mode": "all"},
        follow_redirects=False,
    )
    check(r.status_code == 302, "purge all redirects", r.status_code)
    with mod.app.app_context():
        after_msgs = mod.query("SELECT COUNT(*) AS n FROM messages", one=True)["n"]
        after_atts = mod.query("SELECT COUNT(*) AS n FROM attachments", one=True)["n"]
        check(after_msgs == 0, "purge all deletes messages", after_msgs)
        check(after_atts == 0, "purge all deletes attachment rows", after_atts)
        check(all(not os.path.isfile(p) for p in paths), "purge all removes disk files")
        check(before_msgs > 0, "had messages before purge")

    # ---- Purge old only ----
    with mod.app.app_context():
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
        mod.execute(
            "INSERT INTO messages (sender_id, recipient_id, body, created_at) VALUES (1, NULL, 'old', ?)",
            (old,),
        )
        mod.execute(
            "INSERT INTO messages (sender_id, recipient_id, body, created_at) VALUES (1, NULL, 'new', ?)",
            (mod.now_iso(),),
        )
    r = admin.post(
        "/admin/messages/purge",
        data={"csrf_token": csrf(admin), "mode": "old"},
        follow_redirects=False,
    )
    check(r.status_code == 302, "purge old redirects", r.status_code)
    with mod.app.app_context():
        bodies = [r["body"] for r in mod.query("SELECT body FROM messages")]
        check("old" not in bodies and "new" in bodies, "purge old keeps recent messages", bodies)

    # ---- CSRF required ----
    r = admin.post(
        "/api/upload",
        data={"target": "room", "files": (io.BytesIO(b"x"), "x.txt")},
        content_type="multipart/form-data",
    )
    check(r.status_code == 403, "upload without CSRF rejected", r.status_code)

    # ---- 404 for unknown file ----
    r = admin.get("/files/999999")
    check(r.status_code == 404, "unknown file 404", r.status_code)

finally:
    shutil.rmtree(BASE, ignore_errors=True)

print()
if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("ALL TESTS PASSED")

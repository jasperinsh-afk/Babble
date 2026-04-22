import os
import time
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "replace-with-your-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 最大上传 5MB

DB_PATH = "babble.db"

UPLOAD_FOLDER = os.path.join("static", "uploads", "images")
AVATAR_FOLDER = os.path.join("static", "uploads", "avatars")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# ========= 工具 =========
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def allowed_image_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def allowed_avatar_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_AVATAR_EXTENSIONS


def random_anonymous_name():
    return f"匿名用户{int(time.time() * 1000) % 1000000}"


def get_current_user():
    username = session.get("username")
    if not username:
        return None

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = c.fetchone()
    finally:
        conn.close()
    return user


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        avatar_path TEXT DEFAULT ''
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        user_id INTEGER,
        content TEXT,
        image_path TEXT DEFAULT '',
        is_premium INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        user_id INTEGER,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        UNIQUE(message_id, username)
    )
    """)

    conn.commit()
    conn.close()


def ensure_db_columns():
    conn = get_conn()
    c = conn.cursor()

    def get_columns(table):
        c.execute(f"PRAGMA table_info({table})")
        rows = c.fetchall()
        return [row["name"] for row in rows]

    # users 表兼容
    user_cols = get_columns("users")
    if "avatar_path" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT DEFAULT ''")

    # messages 表兼容
    msg_cols = get_columns("messages")
    if "user_id" not in msg_cols:
        c.execute("ALTER TABLE messages ADD COLUMN user_id INTEGER")
    if "content" not in msg_cols:
        c.execute("ALTER TABLE messages ADD COLUMN content TEXT")
    if "image_path" not in msg_cols:
        c.execute("ALTER TABLE messages ADD COLUMN image_path TEXT DEFAULT ''")
    if "is_premium" not in msg_cols:
        c.execute("ALTER TABLE messages ADD COLUMN is_premium INTEGER DEFAULT 0")
    if "created_at" not in msg_cols:
        c.execute("ALTER TABLE messages ADD COLUMN created_at TEXT DEFAULT ''")

    # replies 表兼容
    reply_cols = get_columns("replies")
    if "user_id" not in reply_cols:
        c.execute("ALTER TABLE replies ADD COLUMN user_id INTEGER")
    if "content" not in reply_cols:
        c.execute("ALTER TABLE replies ADD COLUMN content TEXT DEFAULT ''")
    if "created_at" not in reply_cols:
        c.execute("ALTER TABLE replies ADD COLUMN created_at TEXT DEFAULT ''")

    # likes 表只确保存在即可，结构不随意改，避免唯一索引问题
    conn.commit()
    conn.close()


# ========= 页面 =========
@app.route("/")
@app.route("/message")
def message_page():
    return render_template("message.html")


# ========= 登录态 =========
@app.route("/me")
def me():
    username = session.get("username")

    if not username:
        return jsonify({
            "logged_in": False,
            "username": None,
            "avatar_path": ""
        })

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT username, avatar_path FROM users WHERE username = ?", (username,))
        user = c.fetchone()
    finally:
        conn.close()

    if not user:
        session.clear()
        return jsonify({
            "logged_in": False,
            "username": None,
            "avatar_path": ""
        })

    return jsonify({
        "logged_in": True,
        "username": user["username"],
        "avatar_path": user["avatar_path"] or ""
    })


# ========= 注册 / 登录 / 退出 =========
@app.route("/register", methods=["POST"])
def register():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()

    if len(username) < 2 or len(username) > 30:
        return jsonify({"status": "error", "message": "用户名长度需 2-30 个字符"})
    if len(password) < 2 or len(password) > 100:
        return jsonify({"status": "error", "message": "密码长度不合法"})

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        if c.fetchone():
            return jsonify({"status": "error", "message": "用户名已存在"})

        c.execute(
            "INSERT INTO users (username, password, avatar_path) VALUES (?, ?, ?)",
            (username, password, "")
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"status": "ok"})


@app.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
        user = c.fetchone()
    finally:
        conn.close()

    if not user:
        return jsonify({"status": "error", "message": "用户名或密码错误"})

    session["username"] = user["username"]
    return jsonify({"status": "ok"})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.route("/change_username", methods=["POST"])
def change_username():
    user = get_current_user()
    if not user:
        return jsonify({"status": "error", "message": "请先登录"}), 401

    new_username = (request.form.get("new_username") or "").strip()
    if len(new_username) < 2 or len(new_username) > 30:
        return jsonify({"status": "error", "message": "用户名长度需 2-30 个字符"})

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("SELECT id FROM users WHERE username = ?", (new_username,))
        existed = c.fetchone()
        if existed:
            return jsonify({"status": "error", "message": "新用户名已存在"})

        old_username = user["username"]

        c.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user["id"]))
        c.execute("UPDATE messages SET username = ? WHERE user_id = ?", (new_username, user["id"]))
        c.execute("UPDATE replies SET username = ? WHERE user_id = ?", (new_username, user["id"]))
        c.execute("UPDATE likes SET username = ? WHERE username = ?", (new_username, old_username))

        conn.commit()
    finally:
        conn.close()

    session["username"] = new_username
    return jsonify({"status": "ok"})


@app.route("/delete_account", methods=["POST"])
def delete_account():
    user = get_current_user()
    if not user:
        return jsonify({"status": "error", "message": "请先登录"}), 401

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("DELETE FROM likes WHERE username = ?", (user["username"],))
        c.execute("DELETE FROM replies WHERE user_id = ?", (user["id"],))
        c.execute("DELETE FROM messages WHERE user_id = ?", (user["id"],))
        c.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        conn.commit()
    finally:
        conn.close()

    session.clear()
    return jsonify({"status": "ok"})


# ========= 头像上传 =========
@app.route("/upload_avatar", methods=["POST"])
def upload_avatar():
    user = get_current_user()
    if not user:
        return jsonify({
            "status": "error",
            "message": "请先登录"
        }), 401

    if "avatar" not in request.files:
        return jsonify({
            "status": "error",
            "message": "未选择头像文件"
        }), 400

    file = request.files["avatar"]

    if file.filename == "":
        return jsonify({
            "status": "error",
            "message": "未选择头像文件"
        }), 400

    if not allowed_avatar_file(file.filename):
        return jsonify({
            "status": "error",
            "message": "仅支持 png、jpg、jpeg、gif、webp 格式"
        }), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    safe_username = secure_filename(user["username"])
    filename = f"{safe_username}_{int(time.time())}.{ext}"
    save_path = os.path.join(AVATAR_FOLDER, filename)
    file.save(save_path)

    avatar_path = "/" + save_path.replace("\\", "/")

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "UPDATE users SET avatar_path = ? WHERE id = ?",
            (avatar_path, user["id"])
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "status": "ok",
        "message": "头像上传成功",
        "avatar_path": avatar_path
    })


# ========= 发帖 =========
@app.route("/upload", methods=["POST"])
def upload():
    content = (request.form.get("content") or "").strip()
    member_code = (request.form.get("member_code") or "").strip().upper()

    if not content:
        return jsonify({"status": "error", "message": "内容不能为空"})

    user = get_current_user()

    if user:
        username = user["username"]
        user_id = user["id"]
    else:
        username = random_anonymous_name()
        user_id = None

    is_premium = 1 if member_code else 0

    image_path = ""
    if "image" in request.files:
        image = request.files["image"]
        if image and image.filename:
            if not allowed_image_file(image.filename):
                return jsonify({"status": "error", "message": "图片格式不支持"})
            ext = image.filename.rsplit(".", 1)[1].lower()
            filename = f"msg_{int(time.time())}_{secure_filename(username)}.{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, filename)
            image.save(save_path)
            image_path = "/" + save_path.replace("\\", "/")

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO messages (username, user_id, content, image_path, is_premium, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, user_id, content, image_path, is_premium, now_str()))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"status": "ok"})


# ========= 回复 =========
@app.route("/reply", methods=["POST"])
def reply():
    message_id = (request.form.get("message_id") or "").strip()
    reply_content = (request.form.get("reply_content") or "").strip()

    if not message_id.isdigit():
        return jsonify({"status": "error", "message": "参数错误"})
    if not reply_content:
        return jsonify({"status": "error", "message": "回复内容不能为空"})

    user = get_current_user()
    if user:
        username = user["username"]
        user_id = user["id"]
    else:
        username = random_anonymous_name()
        user_id = None

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO replies (message_id, username, user_id, content, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (int(message_id), username, user_id, reply_content, now_str()))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"status": "ok"})


# ========= 点赞 =========
@app.route("/toggle_like", methods=["POST"])
def toggle_like():
    user = get_current_user()
    if not user:
        return jsonify({"status": "error", "message": "请先登录"}), 401

    message_id = (request.form.get("message_id") or "").strip()
    if not message_id.isdigit():
        return jsonify({"status": "error", "message": "参数错误"})

    message_id = int(message_id)

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute(
            "SELECT id FROM likes WHERE message_id = ? AND username = ?",
            (message_id, user["username"])
        )
        existed = c.fetchone()

        if existed:
            c.execute(
                "DELETE FROM likes WHERE message_id = ? AND username = ?",
                (message_id, user["username"])
            )
            liked = False
        else:
            c.execute(
                "INSERT OR IGNORE INTO likes (message_id, username) VALUES (?, ?)",
                (message_id, user["username"])
            )
            liked = True

        conn.commit()

        c.execute("SELECT COUNT(*) AS cnt FROM likes WHERE message_id = ?", (message_id,))
        like_count = c.fetchone()["cnt"]
    finally:
        conn.close()

    return jsonify({
        "status": "ok",
        "liked": liked,
        "like_count": like_count
    })


# ========= 获取留言 =========
@app.route("/messages")
def messages():
    current_user = get_current_user()
    current_username = current_user["username"] if current_user else None

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("""
            SELECT
                m.id,
                m.username,
                m.user_id,
                m.content,
                m.image_path,
                m.is_premium,
                m.created_at,
                u.avatar_path,
                (SELECT COUNT(*) FROM likes l WHERE l.message_id = m.id) AS like_count
            FROM messages m
            LEFT JOIN users u ON m.user_id = u.id
            ORDER BY m.id DESC
        """)
        message_rows = c.fetchall()

        result = []
        for m in message_rows:
            c.execute("""
                SELECT
                    r.id,
                    r.username,
                    r.user_id,
                    r.content,
                    r.created_at,
                    u.avatar_path
                FROM replies r
                LEFT JOIN users u ON r.user_id = u.id
                WHERE r.message_id = ?
                ORDER BY r.id ASC
            """, (m["id"],))
            reply_rows = c.fetchall()

            liked_by_me = False
            if current_username:
                c.execute(
                    "SELECT 1 FROM likes WHERE message_id = ? AND username = ?",
                    (m["id"], current_username)
                )
                liked_by_me = c.fetchone() is not None

            result.append({
                "id": m["id"],
                "username": m["username"],
                "content": m["content"] or "",
                "image_path": m["image_path"] or "",
                "is_premium": m["is_premium"],
                "date": m["created_at"] or "",
                "avatar_path": m["avatar_path"] or "",
                "like_count": m["like_count"] or 0,
                "liked_by_me": liked_by_me,
                "replies": [
                    {
                        "id": r["id"],
                        "username": r["username"],
                        "content": r["content"] or "",
                        "date": r["created_at"] or "",
                        "avatar_path": r["avatar_path"] or ""
                    }
                    for r in reply_rows
                ]
            })
    finally:
        conn.close()

    return jsonify({
        "status": "ok",
        "messages": result
    })


# ========= 上传过大处理 =========
@app.errorhandler(413)
def too_large(e):
    return jsonify({
        "status": "error",
        "message": "上传文件过大"
    }), 413


# ========= 启动时初始化 =========
init_db()
ensure_db_columns()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

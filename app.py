import os
import time
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "replace-with-your-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 最大上传 5MB

# ========= 路径配置 =========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "babble.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "images")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# ========= 工具 =========
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def allowed_image_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def random_anonymous_name():
    return f"匿名用户{int(time.time() * 1000) % 1000000}"


def to_static_url(abs_path):
    rel = os.path.relpath(abs_path, BASE_DIR).replace("\\", "/")
    if not rel.startswith("/"):
        rel = "/" + rel
    return rel


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


# ========= 数据库初始化 =========
def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
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

    def table_exists(table_name):
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return c.fetchone() is not None

    def get_columns(table_name):
        c.execute(f"PRAGMA table_info({table_name})")
        return [row["name"] for row in c.fetchall()]

    try:
        if table_exists("messages"):
            msg_cols = get_columns("messages")

            if "username" not in msg_cols:
                c.execute("ALTER TABLE messages ADD COLUMN username TEXT DEFAULT '匿名用户'")
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

        if table_exists("replies"):
            reply_cols = get_columns("replies")

            if "message_id" not in reply_cols:
                c.execute("ALTER TABLE replies ADD COLUMN message_id INTEGER DEFAULT 0")
            if "username" not in reply_cols:
                c.execute("ALTER TABLE replies ADD COLUMN username TEXT DEFAULT '匿名用户'")
            if "user_id" not in reply_cols:
                c.execute("ALTER TABLE replies ADD COLUMN user_id INTEGER")
            if "content" not in reply_cols:
                c.execute("ALTER TABLE replies ADD COLUMN content TEXT DEFAULT ''")
            if "created_at" not in reply_cols:
                c.execute("ALTER TABLE replies ADD COLUMN created_at TEXT DEFAULT ''")

        if table_exists("likes"):
            like_cols = get_columns("likes")

            if "message_id" not in like_cols:
                c.execute("ALTER TABLE likes ADD COLUMN message_id INTEGER DEFAULT 0")
            if "username" not in like_cols:
                c.execute("ALTER TABLE likes ADD COLUMN username TEXT DEFAULT ''")

        conn.commit()
    finally:
        conn.close()


# ========= 页面 =========
@app.route("/")
@app.route("/message")
def message_page():
    return render_template("message.html")


# ========= 调试接口 =========
@app.route("/debug_db")
def debug_db():
    conn = get_conn()
    c = conn.cursor()

    info = {
        "db_path": DB_PATH,
        "base_dir": BASE_DIR,
        "db_exists": os.path.exists(DB_PATH),
        "tables": [],
        "counts": {},
        "columns": {}
    }

    try:
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row["name"] for row in c.fetchall()]
        info["tables"] = tables

        for table in tables:
            try:
                c.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
                info["counts"][table] = c.fetchone()["cnt"]
            except Exception as e:
                info["counts"][table] = f"error: {e}"

            try:
                c.execute(f"PRAGMA table_info({table})")
                info["columns"][table] = [row["name"] for row in c.fetchall()]
            except Exception as e:
                info["columns"][table] = f"error: {e}"
    finally:
        conn.close()

    return jsonify(info)


# ========= 登录态 =========
@app.route("/me")
def me():
    username = session.get("username")

    if not username:
        return jsonify({
            "logged_in": False,
            "username": None
        })

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("SELECT username FROM users WHERE username = ?", (username,))
        user = c.fetchone()
    finally:
        conn.close()

    if not user:
        session.clear()
        return jsonify({
            "logged_in": False,
            "username": None
        })

    return jsonify({
        "logged_in": True,
        "username": user["username"]
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
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, password)
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
        c.execute(
            "SELECT * FROM users WHERE username = ? AND password = ?",
            (username, password)
        )
        user = c.fetchone()
    finally:
        conn.close()

    if not user:
        return jsonify({"status": "error", "message": "用户名或密码错误"})

    session["username"] = user["username"]

    return jsonify({
        "status": "ok",
        "username": user["username"]
    })


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
            safe_username = secure_filename(username) or "anonymous"
            filename = f"msg_{int(time.time())}_{safe_username}.{ext}"

            save_path = os.path.join(UPLOAD_FOLDER, filename)
            image.save(save_path)
            image_path = to_static_url(save_path)

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("""
            INSERT INTO messages (
                username,
                user_id,
                content,
                image_path,
                is_premium,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            username,
            user_id,
            content,
            image_path,
            is_premium,
            now_str()
        ))
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

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("SELECT id FROM messages WHERE id = ?", (int(message_id),))
        msg = c.fetchone()
        if not msg:
            return jsonify({"status": "error", "message": "留言不存在"})

        user = get_current_user()

        if user:
            username = user["username"]
            user_id = user["id"]
        else:
            username = random_anonymous_name()
            user_id = None

        c.execute("""
            INSERT INTO replies (
                message_id,
                username,
                user_id,
                content,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            int(message_id),
            username,
            user_id,
            reply_content,
            now_str()
        ))

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
        c.execute("SELECT id FROM messages WHERE id = ?", (message_id,))
        msg = c.fetchone()
        if not msg:
            return jsonify({"status": "error", "message": "留言不存在"})

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
                (
                    SELECT COUNT(*)
                    FROM likes l
                    WHERE l.message_id = m.id
                ) AS like_count
            FROM messages m
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
                    r.created_at
                FROM replies r
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
                "username": m["username"] or "匿名用户",
                "content": m["content"] or "",
                "image_path": m["image_path"] or "",
                "is_premium": m["is_premium"] or 0,
                "date": m["created_at"] or "",
                "like_count": m["like_count"] or 0,
                "liked_by_me": liked_by_me,
                "replies": [
                    {
                        "id": r["id"],
                        "username": r["username"] or "匿名用户",
                        "content": r["content"] or "",
                        "date": r["created_at"] or ""
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


# ========= 错误处理 =========
@app.errorhandler(413)
def too_large(e):
    return jsonify({
        "status": "error",
        "message": "上传文件过大，最大支持 5MB"
    }), 413


@app.errorhandler(500)
def internal_error(e):
    return jsonify({
        "status": "error",
        "message": "服务器内部错误，请查看 Flask 控制台日志"
    }), 500


# ========= 启动初始化 =========
init_db()
ensure_db_columns()

if __name__ == "__main__":
    print("====================================")
    print("BABBLE Flask app starting...")
    print("BASE_DIR:", BASE_DIR)
    print("DB_PATH:", DB_PATH)
    print("DB_EXISTS:", os.path.exists(DB_PATH))
    print("UPLOAD_FOLDER:", UPLOAD_FOLDER)
    print("====================================")

    app.run(host="0.0.0.0", port=5000, debug=True)

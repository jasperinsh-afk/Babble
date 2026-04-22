import os
import time
import traceback
from datetime import datetime

import pymysql
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "replace-with-your-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 最大上传 5MB

# ========= 路径配置 =========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "images")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# ========= Railway MySQL 配置 =========
MYSQL_HOST = os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST")
MYSQL_PORT_RAW = os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT")
MYSQL_USER = os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER")
MYSQL_PASSWORD = (
    os.environ.get("MYSQLPASSWORD")
    or os.environ.get("MYSQL_PASSWORD")
    or os.environ.get("MYSQL_ROOT_PASSWORD")
)
MYSQL_DATABASE = os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE")

try:
    MYSQL_PORT = int(MYSQL_PORT_RAW) if MYSQL_PORT_RAW else 3306
except ValueError:
    MYSQL_PORT = 3306


# ========= 工具 =========
def validate_mysql_env():
    problems = []

    if not MYSQL_HOST:
        problems.append("缺少 MYSQLHOST / MYSQL_HOST")
    if not MYSQL_USER:
        problems.append("缺少 MYSQLUSER / MYSQL_USER")
    if not MYSQL_DATABASE:
        problems.append("缺少 MYSQLDATABASE / MYSQL_DATABASE")

    if MYSQL_HOST and MYSQL_HOST.strip().lower() in {"localhost", "127.0.0.1"}:
        problems.append("检测到非法数据库主机 localhost/127.0.0.1，已拒绝连接（请绑定 Railway MySQL 变量）")

    if problems:
        raise RuntimeError(" | ".join(problems))


def get_conn():
    validate_mysql_env()

    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD or "",
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
        read_timeout=10,
        write_timeout=10
    )


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
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM users WHERE username = %s", (username,))
            return c.fetchone()
    finally:
        conn.close()


# ========= 数据库初始化 =========
def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(30) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                user_id INT NULL,
                content TEXT,
                image_path VARCHAR(500) DEFAULT '',
                is_premium TINYINT DEFAULT 0,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_messages_id (id),
                INDEX idx_messages_user_id (user_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id INT NOT NULL,
                username VARCHAR(64) NOT NULL,
                user_id INT NULL,
                content TEXT NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_replies_message_id (message_id),
                INDEX idx_replies_user_id (user_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id INT NOT NULL,
                username VARCHAR(64) NOT NULL,
                UNIQUE KEY uniq_message_username (message_id, username),
                INDEX idx_likes_message_id (message_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

        conn.commit()
    finally:
        conn.close()


def column_exists(cursor, table_name, column_name):
    cursor.execute("""
        SELECT COUNT(*) AS cnt
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
    """, (MYSQL_DATABASE, table_name, column_name))
    row = cursor.fetchone()
    return bool(row and row["cnt"] > 0)


def ensure_db_columns():
    conn = get_conn()
    try:
        with conn.cursor() as c:
            if not column_exists(c, "messages", "username"):
                c.execute("ALTER TABLE messages ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT '匿名用户'")
            if not column_exists(c, "messages", "user_id"):
                c.execute("ALTER TABLE messages ADD COLUMN user_id INT NULL")
            if not column_exists(c, "messages", "content"):
                c.execute("ALTER TABLE messages ADD COLUMN content TEXT")
            if not column_exists(c, "messages", "image_path"):
                c.execute("ALTER TABLE messages ADD COLUMN image_path VARCHAR(500) DEFAULT ''")
            if not column_exists(c, "messages", "is_premium"):
                c.execute("ALTER TABLE messages ADD COLUMN is_premium TINYINT DEFAULT 0")
            if not column_exists(c, "messages", "created_at"):
                c.execute("ALTER TABLE messages ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''")

            if not column_exists(c, "replies", "message_id"):
                c.execute("ALTER TABLE replies ADD COLUMN message_id INT NOT NULL DEFAULT 0")
            if not column_exists(c, "replies", "username"):
                c.execute("ALTER TABLE replies ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT '匿名用户'")
            if not column_exists(c, "replies", "user_id"):
                c.execute("ALTER TABLE replies ADD COLUMN user_id INT NULL")
            if not column_exists(c, "replies", "content"):
                c.execute("ALTER TABLE replies ADD COLUMN content TEXT")
            if not column_exists(c, "replies", "created_at"):
                c.execute("ALTER TABLE replies ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''")

            if not column_exists(c, "likes", "message_id"):
                c.execute("ALTER TABLE likes ADD COLUMN message_id INT NOT NULL DEFAULT 0")
            if not column_exists(c, "likes", "username"):
                c.execute("ALTER TABLE likes ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT ''")

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
    info = {
        "db_type": "mysql",
        "mysql_host": MYSQL_HOST,
        "mysql_port": MYSQL_PORT,
        "mysql_user": MYSQL_USER,
        "mysql_database": MYSQL_DATABASE,
        "connection": "unknown",
        "tables": [],
        "counts": {},
        "columns": {}
    }

    conn = None
    try:
        validate_mysql_env()
        conn = get_conn()
        info["connection"] = "ok"

        with conn.cursor() as c:
            c.execute("""
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
            """, (MYSQL_DATABASE,))
            tables = [row["TABLE_NAME"] for row in c.fetchall()]
            info["tables"] = tables

            for table in tables:
                try:
                    c.execute(f"SELECT COUNT(*) AS cnt FROM `{table}`")
                    info["counts"][table] = c.fetchone()["cnt"]
                except Exception as e:
                    info["counts"][table] = f"error: {repr(e)}"

                try:
                    c.execute("""
                        SELECT COLUMN_NAME
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = %s
                          AND TABLE_NAME = %s
                        ORDER BY ORDINAL_POSITION
                    """, (MYSQL_DATABASE, table))
                    info["columns"][table] = [row["COLUMN_NAME"] for row in c.fetchall()]
                except Exception as e:
                    info["columns"][table] = f"error: {repr(e)}"

    except Exception as e:
        info["connection"] = f"error: {repr(e)}"
    finally:
        if conn:
            conn.close()

    return jsonify(info)


# ========= 登录态 =========
@app.route("/me")
def me():
    try:
        username = session.get("username")

        if not username:
            return jsonify({
                "logged_in": False,
                "username": None
            })

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT username FROM users WHERE username = %s", (username,))
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
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "logged_in": False,
            "username": None,
            "error": repr(e)
        }), 500


# ========= 注册 / 登录 / 退出 =========
@app.route("/register", methods=["POST"])
def register():
    try:
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if len(username) < 2 or len(username) > 30:
            return jsonify({"status": "error", "message": "用户名长度需 2-30 个字符"})

        if len(password) < 2 or len(password) > 100:
            return jsonify({"status": "error", "message": "密码长度不合法"})

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT id FROM users WHERE username = %s", (username,))
                if c.fetchone():
                    return jsonify({"status": "error", "message": "用户名已存在"})

                c.execute(
                    "INSERT INTO users (username, password) VALUES (%s, %s)",
                    (username, password)
                )
            conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "ok"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "注册失败", "detail": repr(e)}), 500


@app.route("/login", methods=["POST"])
def login():
    try:
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT * FROM users WHERE username = %s AND password = %s",
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
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "登录失败", "detail": repr(e)}), 500


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.route("/change_username", methods=["POST"])
def change_username():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "error", "message": "请先登录"}), 401

        new_username = (request.form.get("new_username") or "").strip()

        if len(new_username) < 2 or len(new_username) > 30:
            return jsonify({"status": "error", "message": "用户名长度需 2-30 个字符"})

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT id FROM users WHERE username = %s", (new_username,))
                existed = c.fetchone()
                if existed:
                    return jsonify({"status": "error", "message": "新用户名已存在"})

                old_username = user["username"]

                c.execute("UPDATE users SET username = %s WHERE id = %s", (new_username, user["id"]))
                c.execute("UPDATE messages SET username = %s WHERE user_id = %s", (new_username, user["id"]))
                c.execute("UPDATE replies SET username = %s WHERE user_id = %s", (new_username, user["id"]))
                c.execute("UPDATE likes SET username = %s WHERE username = %s", (new_username, old_username))

            conn.commit()
        finally:
            conn.close()

        session["username"] = new_username

        return jsonify({"status": "ok"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "修改用户名失败", "detail": repr(e)}), 500


@app.route("/delete_account", methods=["POST"])
def delete_account():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "error", "message": "请先登录"}), 401

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("DELETE FROM likes WHERE username = %s", (user["username"],))
                c.execute("DELETE FROM replies WHERE user_id = %s", (user["id"],))
                c.execute("DELETE FROM messages WHERE user_id = %s", (user["id"],))
                c.execute("DELETE FROM users WHERE id = %s", (user["id"],))
            conn.commit()
        finally:
            conn.close()

        session.clear()
        return jsonify({"status": "ok"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "注销失败", "detail": repr(e)}), 500


# ========= 发帖 =========
@app.route("/upload", methods=["POST"])
def upload():
    try:
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
        try:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO messages (
                        username,
                        user_id,
                        content,
                        image_path,
                        is_premium,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
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
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "发帖失败", "detail": repr(e)}), 500


# ========= 回复 =========
@app.route("/reply", methods=["POST"])
def reply():
    try:
        message_id = (request.form.get("message_id") or "").strip()
        reply_content = (request.form.get("reply_content") or "").strip()

        if not message_id.isdigit():
            return jsonify({"status": "error", "message": "参数错误"})

        if not reply_content:
            return jsonify({"status": "error", "message": "回复内容不能为空"})

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT id FROM messages WHERE id = %s", (int(message_id),))
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
                    VALUES (%s, %s, %s, %s, %s)
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
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "回复失败", "detail": repr(e)}), 500


# ========= 点赞 =========
@app.route("/toggle_like", methods=["POST"])
def toggle_like():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "error", "message": "请先登录"}), 401

        message_id = (request.form.get("message_id") or "").strip()

        if not message_id.isdigit():
            return jsonify({"status": "error", "message": "参数错误"})

        message_id = int(message_id)

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT id FROM messages WHERE id = %s", (message_id,))
                msg = c.fetchone()
                if not msg:
                    return jsonify({"status": "error", "message": "留言不存在"})

                c.execute(
                    "SELECT id FROM likes WHERE message_id = %s AND username = %s",
                    (message_id, user["username"])
                )
                existed = c.fetchone()

                if existed:
                    c.execute(
                        "DELETE FROM likes WHERE message_id = %s AND username = %s",
                        (message_id, user["username"])
                    )
                    liked = False
                else:
                    c.execute(
                        "INSERT IGNORE INTO likes (message_id, username) VALUES (%s, %s)",
                        (message_id, user["username"])
                    )
                    liked = True

                conn.commit()

                c.execute("SELECT COUNT(*) AS cnt FROM likes WHERE message_id = %s", (message_id,))
                like_count = c.fetchone()["cnt"]
        finally:
            conn.close()

        return jsonify({
            "status": "ok",
            "liked": liked,
            "like_count": like_count
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "点赞失败", "detail": repr(e)}), 500


# ========= 获取留言 =========
@app.route("/messages")
def messages():
    try:
        current_user = get_current_user()
        current_username = current_user["username"] if current_user else None

        conn = get_conn()
        try:
            with conn.cursor() as c:
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
                        WHERE r.message_id = %s
                        ORDER BY r.id ASC
                    """, (m["id"],))

                    reply_rows = c.fetchall()

                    liked_by_me = False
                    if current_username:
                        c.execute(
                            "SELECT 1 FROM likes WHERE message_id = %s AND username = %s",
                            (m["id"], current_username)
                        )
                        liked_by_me = c.fetchone() is not None

                    result.append({
                        "id": m["id"],
                        "username": m.get("username") or "匿名用户",
                        "content": m.get("content") or "",
                        "image_path": m.get("image_path") or "",
                        "is_premium": m.get("is_premium") or 0,
                        "date": m.get("created_at") or "",
                        "like_count": m.get("like_count") or 0,
                        "liked_by_me": liked_by_me,
                        "replies": [
                            {
                                "id": r["id"],
                                "username": r.get("username") or "匿名用户",
                                "content": r.get("content") or "",
                                "date": r.get("created_at") or ""
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
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "messages 接口异常",
            "detail": repr(e),
            "mysql_host": MYSQL_HOST,
            "mysql_port": MYSQL_PORT,
            "mysql_user": MYSQL_USER,
            "mysql_database": MYSQL_DATABASE
        }), 500


# ========= 错误处理 =========
@app.errorhandler(413)
def too_large(e):
    return jsonify({
        "status": "error",
        "message": "上传文件过大，最大支持 5MB"
    }), 413


@app.errorhandler(500)
def internal_error(e):
    traceback.print_exc()
    return jsonify({
        "status": "error",
        "message": "服务器内部错误",
        "detail": repr(e)
    }), 500


# ========= 启动初始化 =========
print("====================================")
print("BABBLE Flask app starting...")
print("DB_TYPE: MySQL")
print("MYSQL_HOST:", MYSQL_HOST)
print("MYSQL_PORT:", MYSQL_PORT)
print("MYSQL_USER:", MYSQL_USER)
print("MYSQL_DATABASE:", MYSQL_DATABASE)
print("UPLOAD_FOLDER:", UPLOAD_FOLDER)
print("====================================")

try:
    validate_mysql_env()
    init_db()
    ensure_db_columns()
    print("DB INIT OK")
except Exception:
    print("========== DB INIT ERROR ==========")
    traceback.print_exc()
    print("===================================")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

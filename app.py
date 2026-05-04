import os
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse, unquote

import pymysql
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "replace-with-your-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


# ========= 路径配置 =========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "images")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# ========= 数据库配置解析 =========
def parse_mysql_uri(uri):
    if not uri:
        return {}

    parsed = urlparse(uri)
    scheme = (parsed.scheme or "").lower()

    if not scheme.startswith("mysql"):
        return {}

    database = parsed.path.lstrip("/") if parsed.path else None

    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else "",
        "database": unquote(database) if database else None,
        "source_uri": uri
    }


def load_mysql_config():
    config = {
        "host": os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST"),
        "port": os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT"),
        "user": os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER"),
        "password": (
            os.environ.get("MYSQLPASSWORD")
            or os.environ.get("MYSQL_PASSWORD")
            or os.environ.get("MYSQL_ROOT_PASSWORD")
        ),
        "database": os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE"),
        "source": "MYSQLHOST"
    }

    has_direct_config = bool(config["host"] and config["user"] and config["database"])
    if has_direct_config:
        try:
            config["port"] = int(config["port"] or 3306)
        except ValueError:
            config["port"] = 3306
        return config

    for key in ["SQLALCHEMY_DATABASE_URI", "DATABASE_URL", "MYSQL_URL"]:
        uri = os.environ.get(key)
        parsed = parse_mysql_uri(uri)
        if parsed.get("host") and parsed.get("user") and parsed.get("database"):
            return {
                "host": parsed.get("host"),
                "port": parsed.get("port") or 3306,
                "user": parsed.get("user"),
                "password": parsed.get("password") or "",
                "database": parsed.get("database"),
                "source": key
            }

    try:
        config["port"] = int(config["port"] or 3306)
    except ValueError:
        config["port"] = 3306

    return config


MYSQL_CONFIG = load_mysql_config()

MYSQL_HOST = MYSQL_CONFIG.get("host")
MYSQL_PORT = MYSQL_CONFIG.get("port") or 3306
MYSQL_USER = MYSQL_CONFIG.get("user")
MYSQL_PASSWORD = MYSQL_CONFIG.get("password") or ""
MYSQL_DATABASE = MYSQL_CONFIG.get("database")
MYSQL_SOURCE = MYSQL_CONFIG.get("source")


# ========= 工具函数 =========
def validate_mysql_env():
    problems = []

    if not MYSQL_HOST:
        problems.append("缺少 MYSQLHOST / MYSQL_HOST，且没有可解析的 SQLALCHEMY_DATABASE_URI / DATABASE_URL / MYSQL_URL")
    if not MYSQL_USER:
        problems.append("缺少 MYSQLUSER / MYSQL_USER，且连接串里没有用户名")
    if not MYSQL_DATABASE:
        problems.append("缺少 MYSQLDATABASE / MYSQL_DATABASE，且连接串里没有数据库名")

    if MYSQL_HOST:
        normalized_host = MYSQL_HOST.strip().lower()
        if normalized_host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            problems.append(f"检测到非法数据库主机 {MYSQL_HOST}，Railway MySQL 不应使用 localhost")

    if problems:
        raise RuntimeError(" | ".join(problems))


def get_conn():
    validate_mysql_env()
    return pymysql.connect(
        host=MYSQL_HOST,
        port=int(MYSQL_PORT),
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


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""


def table_exists_with_cursor(cursor, table_name):
    cursor.execute("""
        SELECT COUNT(*) AS cnt
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
    """, (MYSQL_DATABASE, table_name))
    row = cursor.fetchone()
    return bool(row and row["cnt"] > 0)


def table_exists(table_name):
    conn = get_conn()
    try:
        with conn.cursor() as c:
            return table_exists_with_cursor(c, table_name)
    finally:
        conn.close()


def detect_table_mode():
    conn = get_conn()
    try:
        with conn.cursor() as c:
            has_old = (
                table_exists_with_cursor(c, "message")
                and table_exists_with_cursor(c, "reply")
                and table_exists_with_cursor(c, "user")
            )
            has_new = (
                table_exists_with_cursor(c, "messages")
                and table_exists_with_cursor(c, "replies")
                and table_exists_with_cursor(c, "users")
            )

            if has_old:
                return "old"
            if has_new:
                return "new"
            return "old"
    finally:
        conn.close()


def current_tables():
    mode = detect_table_mode()
    if mode == "old":
        return {
            "mode": "old",
            "message_table": "message",
            "reply_table": "reply",
            "user_table": "user"
        }
    return {
        "mode": "new",
        "message_table": "messages",
        "reply_table": "replies",
        "user_table": "users"
    }


def get_current_user():
    username = session.get("username")
    if not username:
        return None

    tables = current_tables()
    user_table = tables["user_table"]

    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(f"SELECT * FROM `{user_table}` WHERE username = %s", (username,))
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


def ensure_db_columns():
    conn = get_conn()
    try:
        with conn.cursor() as c:
            if table_exists_with_cursor(c, "messages"):
                if not column_exists(c, "messages", "username"):
                    c.execute("ALTER TABLE messages ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT '匿名用户'")
                if not column_exists(c, "messages", "user_id"):
                    c.execute("ALTER TABLE messages ADD COLUMN user_id INT NULL")
                if not column_exists(c, "messages", "content"):
                    c.execute("ALTER TABLE messages ADD COLUMN content TEXT")
                if not column_exists(c, "messages", "image_path"):
                    c.execute("ALTER TABLE messages ADD COLUMN image_path VARCHAR(500) DEFAULT ''")
                if not column_exists(c, "messages", "created_at"):
                    c.execute("ALTER TABLE messages ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''")

            if table_exists_with_cursor(c, "replies"):
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

            if table_exists_with_cursor(c, "likes"):
                if not column_exists(c, "likes", "message_id"):
                    c.execute("ALTER TABLE likes ADD COLUMN message_id INT NOT NULL DEFAULT 0")
                if not column_exists(c, "likes", "username"):
                    c.execute("ALTER TABLE likes ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT ''")

            if table_exists_with_cursor(c, "users"):
                if not column_exists(c, "users", "inviter"):
                    c.execute("ALTER TABLE users ADD COLUMN inviter VARCHAR(64) NOT NULL DEFAULT ''")
                if not column_exists(c, "users", "created_at"):
                    c.execute("ALTER TABLE users ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''")

            if table_exists_with_cursor(c, "user"):
                if not column_exists(c, "user", "inviter"):
                    c.execute("ALTER TABLE user ADD COLUMN inviter VARCHAR(64) NOT NULL DEFAULT ''")
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


# ========= 页面路由 =========
@app.route("/")
@app.route("/message")
def message_page():
    return render_template("message.html")


# ========= 登录态 =========
@app.route("/me")
def me():
    try:
        username = session.get("username")
        if not username:
            return jsonify({"logged_in": False, "username": None})

        tables = current_tables()
        user_table = tables["user_table"]

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(f"SELECT id, username FROM `{user_table}` WHERE username = %s", (username,))
                user = c.fetchone()
                if not user:
                    session.clear()
                    return jsonify({"logged_in": False, "username": None})
        finally:
            conn.close()

        return jsonify({
            "logged_in": True,
            "username": user["username"],
            "mode": tables["mode"]
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
        tables = current_tables()
        mode = tables["mode"]
        user_table = tables["user_table"]

        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        inviter = (request.form.get("inviter") or "").strip()

        if len(username) < 2 or len(username) > 30:
            return jsonify({"status": "error", "message": "用户名长度需 2-30 个字符"})
        if len(password) < 2 or len(password) > 100:
            return jsonify({"status": "error", "message": "密码长度不合法"})
        if inviter and len(inviter) > 30:
            return jsonify({"status": "error", "message": "邀请人用户名过长"})
        if inviter and inviter == username:
            return jsonify({"status": "error", "message": "邀请人不能是自己"})

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(f"SELECT id FROM `{user_table}` WHERE username = %s", (username,))
                if c.fetchone():
                    return jsonify({"status": "error", "message": "用户名已存在"})

                inviter_to_save = ""
                if inviter and column_exists(c, user_table, "inviter"):
                    c.execute(f"SELECT id FROM `{user_table}` WHERE username = %s", (inviter,))
                    if c.fetchone():
                        inviter_to_save = inviter

                if mode == "old":
                    if column_exists(c, user_table, "inviter"):
                        c.execute(f"""
                            INSERT INTO `{user_table}` (username, password_hash, date, register_ip, inviter)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (username, password, now_str(), client_ip(), inviter_to_save))
                    else:
                        c.execute(f"""
                            INSERT INTO `{user_table}` (username, password_hash, date, register_ip)
                            VALUES (%s, %s, %s, %s)
                        """, (username, password, now_str(), client_ip()))
                else:
                    has_inviter = column_exists(c, user_table, "inviter")
                    has_created_at = column_exists(c, user_table, "created_at")

                    if has_inviter and has_created_at:
                        c.execute(f"""
                            INSERT INTO `{user_table}` (username, password, inviter, created_at)
                            VALUES (%s, %s, %s, %s)
                        """, (username, password, inviter_to_save, now_str()))
                    elif has_inviter:
                        c.execute(f"""
                            INSERT INTO `{user_table}` (username, password, inviter)
                            VALUES (%s, %s, %s)
                        """, (username, password, inviter_to_save))
                    elif has_created_at:
                        c.execute(f"""
                            INSERT INTO `{user_table}` (username, password, created_at)
                            VALUES (%s, %s, %s)
                        """, (username, password, now_str()))
                    else:
                        c.execute(f"""
                            INSERT INTO `{user_table}` (username, password)
                            VALUES (%s, %s)
                        """, (username, password))
            conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "ok", "mode": mode})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "注册失败", "detail": repr(e)}), 500


@app.route("/login", methods=["POST"])
def login():
    try:
        tables = current_tables()
        mode = tables["mode"]
        user_table = tables["user_table"]

        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        conn = get_conn()
        try:
            with conn.cursor() as c:
                if mode == "old":
                    c.execute(
                        f"SELECT * FROM `{user_table}` WHERE username = %s AND password_hash = %s",
                        (username, password)
                    )
                else:
                    c.execute(
                        f"SELECT * FROM `{user_table}` WHERE username = %s AND password = %s",
                        (username, password)
                    )
                user = c.fetchone()
                if not user:
                    return jsonify({"status": "error", "message": "用户名或密码错误"})
        finally:
            conn.close()

        session["username"] = user["username"]
        return jsonify({"status": "ok", "username": user["username"], "mode": mode})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "登录失败", "detail": repr(e)}), 500


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "ok"})


# ========= 发帖 =========
@app.route("/upload", methods=["POST"])
def upload():
    try:
        tables = current_tables()
        mode = tables["mode"]
        message_table = tables["message_table"]

        content = (
            request.form.get("content")
            or request.form.get("message")
            or request.form.get("text")
            or request.form.get("body")
            or ""
        ).strip()

        if not content:
            return jsonify({"status": "error", "message": "内容不能为空"}), 400

        user = get_current_user()
        if user:
            username = user["username"]
            user_id = user["id"]
        else:
            username = random_anonymous_name()
            user_id = None

        image_path = ""
        image = request.files.get("image") or request.files.get("file") or request.files.get("photo")
        has_image = bool(image and image.filename)

        if has_image:
            if not allowed_image_file(image.filename):
                return jsonify({"status": "error", "message": "图片格式不支持"}), 400

            ext = image.filename.rsplit(".", 1)[1].lower()
            safe_username = secure_filename(username) or "anonymous"
            filename = f"msg_{int(time.time())}_{safe_username}.{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, filename)
            image.save(save_path)
            image_path = to_static_url(save_path)

        conn = get_conn()
        try:
            with conn.cursor() as c:
                if mode == "old":
                    c.execute(f"""
                        INSERT INTO `{message_table}` (
                            ip, content, date, username, image_path, user_id
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        client_ip(),
                        content,
                        now_str(),
                        username,
                        image_path,
                        user_id
                    ))
                else:
                    c.execute(f"""
                        INSERT INTO `{message_table}` (
                            username, user_id, content, image_path, created_at
                        ) VALUES (%s, %s, %s, %s, %s)
                    """, (
                        username,
                        user_id,
                        content,
                        image_path,
                        now_str()
                    ))
            conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "ok", "mode": mode})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "发帖失败", "detail": repr(e)}), 500


# ========= 回复 =========
@app.route("/reply", methods=["POST"])
def reply():
    try:
        tables = current_tables()
        mode = tables["mode"]
        message_table = tables["message_table"]
        reply_table = tables["reply_table"]

        message_id = (request.form.get("message_id") or "").strip()
        reply_content = (request.form.get("reply_content") or "").strip()

        if not message_id.isdigit():
            return jsonify({"status": "error", "message": "参数错误"})
        if not reply_content:
            return jsonify({"status": "error", "message": "回复内容不能为空"})

        message_id = int(message_id)

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(f"SELECT id FROM `{message_table}` WHERE id = %s", (message_id,))
                if not c.fetchone():
                    return jsonify({"status": "error", "message": "留言不存在"})

                user = get_current_user()
                if user:
                    username = user["username"]
                    user_id = user["id"]
                else:
                    username = random_anonymous_name()
                    user_id = None

                if mode == "old":
                    c.execute(f"""
                        INSERT INTO `{reply_table}` (
                            ip, content, date, message_id, username, user_id
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        client_ip(),
                        reply_content,
                        now_str(),
                        message_id,
                        username,
                        user_id
                    ))
                else:
                    c.execute(f"""
                        INSERT INTO `{reply_table}` (
                            message_id, username, user_id, content, created_at
                        ) VALUES (%s, %s, %s, %s, %s)
                    """, (
                        message_id,
                        username,
                        user_id,
                        reply_content,
                        now_str()
                    ))
            conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "ok", "mode": mode})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "回复失败", "detail": repr(e)}), 500


# ========= 点赞（修复路由匹配小程序调用） =========
@app.route("/like", methods=["POST"])
def toggle_like():
    try:
        tables = current_tables()
        mode = tables["mode"]

        if mode == "old":
            return jsonify({"status": "error", "message": "旧表模式暂不支持点赞"}), 400

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
                if not c.fetchone():
                    return jsonify({"status": "error", "message": "留言不存在"})

                c.execute("SELECT id FROM likes WHERE message_id = %s AND username = %s", (message_id, user["username"]))
                existed = c.fetchone()

                if existed:
                    c.execute("DELETE FROM likes WHERE message_id = %s AND username = %s", (message_id, user["username"]))
                    liked = False
                else:
                    c.execute("INSERT IGNORE INTO likes (message_id, username) VALUES (%s, %s)", (message_id, user["username"]))
                    liked = True

                c.execute("SELECT COUNT(*) AS cnt FROM likes WHERE message_id = %s", (message_id,))
                like_count = c.fetchone()["cnt"]
        finally:
            conn.close()

        return jsonify({"status": "ok", "liked": liked, "like_count": like_count})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "点赞失败", "detail": repr(e)}), 500


# ========= 获取留言 =========
@app.route("/messages")
def messages():
    try:
        tables = current_tables()
        mode = tables["mode"]
        message_table = tables["message_table"]
        reply_table = tables["reply_table"]

        current_user = get_current_user()
        current_username = current_user["username"] if current_user else None

        conn = get_conn()
        try:
            with conn.cursor() as c:
                if mode == "old":
                    c.execute(f"""
                        SELECT
                            m.id, m.username, m.user_id, m.content, m.image_path,
                            m.date AS created_at, 0 AS like_count
                        FROM `{message_table}` m
                        ORDER BY m.id DESC
                    """)
                else:
                    c.execute(f"""
                        SELECT
                            m.id, m.username, m.user_id, m.content, m.image_path, m.created_at,
                            (SELECT COUNT(*) FROM likes l WHERE l.message_id = m.id) AS like_count
                        FROM `{message_table}` m
                        ORDER BY m.id DESC
                    """)

                message_rows = c.fetchall()
                result = []

                for m in message_rows:
                    if mode == "old":
                        c.execute(f"""
                            SELECT r.id, r.username, r.user_id, r.content, r.date AS created_at
                            FROM `{reply_table}` r
                            WHERE r.message_id = %s
                            ORDER BY r.id ASC
                        """, (m["id"],))
                        reply_rows = c.fetchall()
                        liked_by_me = False
                    else:
                        c.execute(f"""
                            SELECT r.id, r.username, r.user_id, r.content, r.created_at
                            FROM `{reply_table}` r
                            WHERE r.message_id = %s
                            ORDER BY r.id ASC
                        """, (m["id"],))
                        reply_rows = c.fetchall()

                        liked_by_me = False
                        if current_username:
                            c.execute("SELECT 1 FROM likes WHERE message_id = %s AND username = %s", (m["id"], current_username))
                            liked_by_me = c.fetchone() is not None

                    result.append({
                        "id": m["id"],
                        "username": m.get("username") or "匿名用户",
                        "content": m.get("content") or "",
                        "image_path": m.get("image_path") or "",
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

        return jsonify({"status": "ok", "mode": mode, "messages": result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "messages 接口异常",
            "detail": repr(e),
            "config_source": MYSQL_SOURCE,
            "mysql_host": MYSQL_HOST,
            "mysql_port": MYSQL_PORT,
            "mysql_user": MYSQL_USER,
            "mysql_database": MYSQL_DATABASE
        }), 500


# ========= 错误处理 =========
@app.errorhandler(413)
def too_large(e):
    return jsonify({"status": "error", "message": "上传文件过大，最大支持 5MB"}), 413


@app.errorhandler(500)
def internal_error(e):
    traceback.print_exc()
    return jsonify({"status": "error", "message": "服务器内部错误", "detail": repr(e)}), 500


# ========= 启动初始化 =========
print("====================================")
print("BABBLE Flask app starting...")
print("DB_TYPE: MySQL")
print("CONFIG_SOURCE:", MYSQL_SOURCE)
print("MYSQL_HOST:", MYSQL_HOST)
print("MYSQL_PORT:", MYSQL_PORT)
print("MYSQL_USER:", MYSQL_USER)
print("MYSQL_DATABASE:", MYSQL_DATABASE)
print("HAS_PASSWORD:", bool(MYSQL_PASSWORD))
print("UPLOAD_FOLDER:", UPLOAD_FOLDER)
print("====================================")

try:
    validate_mysql_env()
    init_db()
    ensure_db_columns()
    print("DB INIT OK")
    try:
        print("DETECTED_TABLE_MODE:", detect_table_mode())
    except Exception:
        traceback.print_exc()
except Exception:
    print("========== DB INIT ERROR ==========")
    traceback.print_exc()
    print("===================================")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

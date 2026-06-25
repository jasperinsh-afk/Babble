import os
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse, unquote

import pymysql
from flask import Flask, render_template, request, jsonify
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


# ========= 工具函数 =========
def validate_mysql_env():
    problems = []

    if not MYSQL_HOST:
        problems.append("缺少 MYSQLHOST / MYSQL_HOST，且没有可解析的数据库连接串")
    if not MYSQL_USER:
        problems.append("缺少 MYSQLUSER / MYSQL_USER")
    if not MYSQL_DATABASE:
        problems.append("缺少 MYSQLDATABASE / MYSQL_DATABASE")

    if MYSQL_HOST:
        normalized_host = MYSQL_HOST.strip().lower()
        if normalized_host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            problems.append(f"非法数据库主机 {MYSQL_HOST}")

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


# ========= 数据库初始化 =========
def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                content TEXT,
                image_path VARCHAR(500) DEFAULT '',
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_messages_id (id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id INT NOT NULL,
                username VARCHAR(64) NOT NULL,
                content TEXT NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_replies_message_id (message_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id INT NOT NULL,
                client_ip VARCHAR(128) NOT NULL,
                UNIQUE KEY uniq_msg_ip (message_id, client_ip),
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
                if not column_exists(c, "replies", "content"):
                    c.execute("ALTER TABLE replies ADD COLUMN content TEXT")
                if not column_exists(c, "replies", "created_at"):
                    c.execute("ALTER TABLE replies ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''")

            if table_exists_with_cursor(c, "likes"):
                if not column_exists(c, "likes", "message_id"):
                    c.execute("ALTER TABLE likes ADD COLUMN message_id INT NOT NULL DEFAULT 0")
                if not column_exists(c, "likes", "client_ip"):
                    c.execute("ALTER TABLE likes ADD COLUMN client_ip VARCHAR(128) NOT NULL")
        conn.commit()
    finally:
        conn.close()


# ========= 页面路由 =========
@app.route("/")
@app.route("/message")
def message_page():
    return render_template("message.html")


# ========= 发帖接口（所有用户均可上传图片，无会员、账号限制） =========
@app.route("/upload", methods=["POST"])
def upload():
    try:
        content = (
            request.form.get("content")
            or request.form.get("message")
            or request.form.get("text")
            or ""
        ).strip()

        username = random_anonymous_name()
        image_path = ""
        image = request.files.get("image") or request.files.get("file")
        has_image = bool(image and image.filename)

        # 允许只发图 或 只发文字
        if not content and not has_image:
            return jsonify({"status": "error", "message": "请输入文字内容或上传图片"}), 400

        # 处理图片上传，所有用户无权限限制
        if has_image:
            if not allowed_image_file(image.filename):
                return jsonify({"status": "error", "message": "图片格式仅支持 png、jpg、jpeg、gif、webp"}), 400

            ext = image.filename.rsplit(".", 1)[1].lower()
            safe_name = secure_filename(username)
            filename = f"msg_{int(time.time())}_{safe_name}.{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, filename)
            image.save(save_path)
            image_path = to_static_url(save_path)

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO messages (username, content, image_path, created_at)
                    VALUES (%s, %s, %s, %s)
                """, (username, content, image_path, now_str()))
            conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "ok"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "发帖失败", "detail": repr(e)}), 500


# ========= 回复接口 =========
@app.route("/reply", methods=["POST"])
def reply():
    try:
        message_id = (request.form.get("message_id") or "").strip()
        reply_content = (request.form.get("reply_content") or "").strip()

        if not message_id.isdigit():
            return jsonify({"status": "error", "message": "参数错误"})
        if not reply_content:
            return jsonify({"status": "error", "message": "回复内容不能为空"})

        message_id = int(message_id)
        username = random_anonymous_name()

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT id FROM messages WHERE id = %s", (message_id,))
                if not c.fetchone():
                    return jsonify({"status": "error", "message": "留言不存在"})

                c.execute("""
                    INSERT INTO replies (message_id, username, content, created_at)
                    VALUES (%s, %s, %s, %s)
                """, (message_id, username, reply_content, now_str()))
            conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "ok"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "回复失败", "detail": repr(e)}), 500


# ========= 点赞接口（按IP限制，无需登录） =========
@app.route("/toggle_like", methods=["POST"])
def toggle_like():
    try:
        message_id = (request.form.get("message_id") or "").strip()
        if not message_id.isdigit():
            return jsonify({"status": "error", "message": "参数错误"})
        message_id = int(message_id)
        ip = client_ip()

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT id FROM messages WHERE id = %s", (message_id,))
                if not c.fetchone():
                    return jsonify({"status": "error", "message": "留言不存在"})

                c.execute("SELECT id FROM likes WHERE message_id = %s AND client_ip = %s", (message_id, ip))
                existed = c.fetchone()

                if existed:
                    c.execute("DELETE FROM likes WHERE message_id = %s AND client_ip = %s", (message_id, ip))
                    liked = False
                else:
                    c.execute("INSERT IGNORE INTO likes (message_id, client_ip) VALUES (%s, %s)", (message_id, ip))
                    liked = True

                c.execute("SELECT COUNT(*) AS cnt FROM likes WHERE message_id = %s", (message_id,))
                like_count = c.fetchone()["cnt"]
            conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "ok", "liked": liked, "like_count": like_count})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "点赞失败", "detail": repr(e)}), 500


# ========= 获取所有留言 + 回复 + 点赞 =========
@app.route("/messages")
def messages():
    try:
        current_ip = client_ip()
        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT
                        m.id, m.username, m.content, m.image_path, m.created_at,
                        (SELECT COUNT(*) FROM likes l WHERE l.message_id = m.id) AS like_count
                    FROM messages m
                    ORDER BY m.id DESC
                """)
                message_rows = c.fetchall()
                result = []

                for m in message_rows:
                    c.execute("""
                        SELECT r.id, r.username, r.content, r.created_at
                        FROM replies r
                        WHERE r.message_id = %s
                        ORDER BY r.id ASC
                    """, (m["id"],))
                    reply_rows = c.fetchall()

                    c.execute("SELECT 1 FROM likes WHERE message_id = %s AND client_ip = %s", (m["id"], current_ip))
                    liked_by_me = c.fetchone() is not None

                    result.append({
                        "id": m["id"],
                        "username": m["username"],
                        "content": m["content"] or "",
                        "image_path": m["image_path"] or "",
                        "date": m["created_at"],
                        "like_count": m["like_count"],
                        "liked_by_me": liked_by_me,
                        "replies": [
                            {
                                "id": r["id"],
                                "username": r["username"],
                                "content": r["content"],
                                "date": r["created_at"]
                            } for r in reply_rows
                        ]
                    })
        finally:
            conn.close()

        return jsonify({"status": "ok", "messages": result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "获取留言失败", "detail": repr(e)}), 500


# ========= 全局错误捕获 =========
@app.errorhandler(413)
def too_large(e):
    return jsonify({"status": "error", "message": "上传文件过大，最大支持 5MB"}), 413


@app.errorhandler(500)
def internal_error(e):
    traceback.print_exc()
    return jsonify({"status": "error", "message": "服务器内部错误", "detail": repr(e)}), 500


# ========= 程序初始化 =========
print("====================================")
print("BABBLE 匿名留言板启动（已移除账号/会员/积分功能）")
print("MYSQL_HOST:", MYSQL_HOST)
print("MYSQL_PORT:", MYSQL_PORT)
print("MYSQL_USER:", MYSQL_USER)
print("MYSQL_DATABASE:", MYSQL_DATABASE)
print("UPLOAD_FOLDER:", UPLOAD_FOLDER)
print("所有用户均可上传图片，无权限限制")
print("====================================")

try:
    validate_mysql_env()
    init_db()
    ensure_db_columns()
    print("数据库初始化完成")
except Exception:
    print("========== 数据库初始化异常 ==========")
    traceback.print_exc()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

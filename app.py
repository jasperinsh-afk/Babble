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
        return {"mode": "old", "message_table": "message", "reply_table": "reply", "user_table": "user"}
    return {"mode": "new", "message_table": "messages", "reply_table": "replies", "user_table": "users"}


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


# ========= 页面路由 =========
@app.route("/")
@app.route("/message")
def message_page():
    return render_template("message.html")


# ========= 特殊码验证接口 =========
@app.route("/redeem_member_code", methods=["POST"])
def redeem_member_code():
    try:
        code = (request.form.get("code") or "").strip()
        VALID_SPECIAL_CODE = "114PZ514"  # ✅ 你的特殊码

        if code == VALID_SPECIAL_CODE:
            session["is_member"] = True
            return jsonify({"status": "ok", "is_member": True, "upgraded": True})
        else:
            return jsonify({"status": "fail", "message": "无效的特殊码"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "验证异常", "detail": repr(e)}), 500


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

        return jsonify({"logged_in": True, "username": user["username"], "mode": tables["mode"]})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"logged_in": False, "username": None, "error": repr(e)}), 500


# ========= 其他已有路由（注册、登录、发帖、回复、点赞等）保持不变 =========
# 你原来的代码从这里开始一直到底部保持原样即可


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
    print("DB INIT OK")
    print("DETECTED_TABLE_MODE:", detect_table_mode())
except Exception:
    print("========== DB INIT ERROR ==========")
    traceback.print_exc()
    print("===================================")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

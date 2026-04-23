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

# ========= 积分配置 =========
POINTS_DAILY_CHECKIN = 5
POINTS_POST_TEXT = 10
POINTS_POST_IMAGE = 15
POINTS_REPLY = 5
POINTS_THEME_DAILY = 5
POINTS_MEMBER_THRESHOLD = 100
CHEAT_MEMBER_CODE = "114PZ514"


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


# ========= 工具 =========
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


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


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
    """
    old:  message / reply / user
    new:  messages / replies / users
    """
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


# ========= 积分工具 =========
def ensure_points_row(cursor, user_id):
    cursor.execute("""
        INSERT INTO user_points (user_id, points, is_member, last_checkin_date, last_theme_reward_date, created_at, updated_at)
        VALUES (%s, 0, 0, '', '', %s, %s)
        ON DUPLICATE KEY UPDATE updated_at = VALUES(updated_at)
    """, (user_id, now_str(), now_str()))


def get_points_row(cursor, user_id):
    ensure_points_row(cursor, user_id)
    cursor.execute("""
        SELECT user_id, points, is_member, last_checkin_date, last_theme_reward_date, updated_at
        FROM user_points
        WHERE user_id = %s
    """, (user_id,))
    return cursor.fetchone()


def normalize_member_by_points(cursor, user_id):
    row = get_points_row(cursor, user_id)
    points = int(row.get("points") or 0)
    is_member = int(row.get("is_member") or 0)
    should_member = 1 if points >= POINTS_MEMBER_THRESHOLD else is_member
    if should_member != is_member:
        cursor.execute("UPDATE user_points SET is_member = %s, updated_at = %s WHERE user_id = %s", (should_member, now_str(), user_id))
    return should_member


def add_points(cursor, user_id, delta):
    if delta <= 0:
        return get_points_row(cursor, user_id)
    ensure_points_row(cursor, user_id)
    cursor.execute("""
        UPDATE user_points
        SET points = points + %s,
            updated_at = %s
        WHERE user_id = %s
    """, (delta, now_str(), user_id))
    normalize_member_by_points(cursor, user_id)
    return get_points_row(cursor, user_id)


def points_payload_from_row(row):
    points = int(row.get("points") or 0)
    is_member = bool(int(row.get("is_member") or 0))
    return {
        "points": points,
        "is_member": is_member,
        "today_signed": (row.get("last_checkin_date") == today_str()),
        "today_theme_rewarded": (row.get("last_theme_reward_date") == today_str())
    }


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

            # 新增：积分表（自动创建，不用手动）
            c.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id INT PRIMARY KEY,
                points INT NOT NULL DEFAULT 0,
                is_member TINYINT NOT NULL DEFAULT 0,
                last_checkin_date VARCHAR(16) NOT NULL DEFAULT '',
                last_theme_reward_date VARCHAR(16) NOT NULL DEFAULT '',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_user_points_is_member (is_member)
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
                if not column_exists(c, "messages", "is_premium"):
                    c.execute("ALTER TABLE messages ADD COLUMN is_premium TINYINT DEFAULT 0")
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

            # user_points 补字段（防止半升级环境）
            if table_exists_with_cursor(c, "user_points"):
                if not column_exists(c, "user_points", "user_id"):
                    c.execute("ALTER TABLE user_points ADD COLUMN user_id INT PRIMARY KEY")
                if not column_exists(c, "user_points", "points"):
                    c.execute("ALTER TABLE user_points ADD COLUMN points INT NOT NULL DEFAULT 0")
                if not column_exists(c, "user_points", "is_member"):
                    c.execute("ALTER TABLE user_points ADD COLUMN is_member TINYINT NOT NULL DEFAULT 0")
                if not column_exists(c, "user_points", "last_checkin_date"):
                    c.execute("ALTER TABLE user_points ADD COLUMN last_checkin_date VARCHAR(16) NOT NULL DEFAULT ''")
                if not column_exists(c, "user_points", "last_theme_reward_date"):
                    c.execute("ALTER TABLE user_points ADD COLUMN last_theme_reward_date VARCHAR(16) NOT NULL DEFAULT ''")
                if not column_exists(c, "user_points", "created_at"):
                    c.execute("ALTER TABLE user_points ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''")
                if not column_exists(c, "user_points", "updated_at"):
                    c.execute("ALTER TABLE user_points ADD COLUMN updated_at VARCHAR(32) NOT NULL DEFAULT ''")

        conn.commit()
    finally:
        conn.close()


# ========= 页面 =========
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

                ensure_points_row(c, user["id"])
                row = get_points_row(c, user["id"])
                conn.commit()
        finally:
            conn.close()

        payload = points_payload_from_row(row)
        return jsonify({
            "logged_in": True,
            "username": user["username"],
            "mode": tables["mode"],
            "points": payload["points"],
            "is_member": payload["is_member"]
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "logged_in": False,
            "username": None,
            "error": repr(e)
        }), 500


# ========= 积分接口 =========
@app.route("/points_status")
def points_status():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "error", "message": "请先登录"}), 401

        conn = get_conn()
        try:
            with conn.cursor() as c:
                row = get_points_row(c, user["id"])
                normalize_member_by_points(c, user["id"])
                row = get_points_row(c, user["id"])
            conn.commit()
        finally:
            conn.close()

        payload = points_payload_from_row(row)
        return jsonify({"status": "ok", **payload})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "获取积分失败", "detail": repr(e)}), 500


@app.route("/daily_checkin", methods=["POST"])
def daily_checkin():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "error", "message": "请先登录"}), 401

        today = today_str()
        rewarded = False

        conn = get_conn()
        try:
            with conn.cursor() as c:
                row = get_points_row(c, user["id"])
                if row.get("last_checkin_date") != today:
                    add_points(c, user["id"], POINTS_DAILY_CHECKIN)
                    c.execute("UPDATE user_points SET last_checkin_date = %s, updated_at = %s WHERE user_id = %s",
                              (today, now_str(), user["id"]))
                    rewarded = True

                row = get_points_row(c, user["id"])
            conn.commit()
        finally:
            conn.close()

        payload = points_payload_from_row(row)
        return jsonify({"status": "ok", "rewarded": rewarded, "delta": POINTS_DAILY_CHECKIN if rewarded else 0, **payload})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "打卡失败", "detail": repr(e)}), 500


@app.route("/theme_reward", methods=["POST"])
def theme_reward():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "ok", "rewarded": False, "delta": 0, "points": 0, "is_member": False, "today_signed": False, "today_theme_rewarded": False})

        today = today_str()
        rewarded = False

        conn = get_conn()
        try:
            with conn.cursor() as c:
                row = get_points_row(c, user["id"])
                if row.get("last_theme_reward_date") != today:
                    add_points(c, user["id"], POINTS_THEME_DAILY)
                    c.execute("UPDATE user_points SET last_theme_reward_date = %s, updated_at = %s WHERE user_id = %s",
                              (today, now_str(), user["id"]))
                    rewarded = True

                row = get_points_row(c, user["id"])
            conn.commit()
        finally:
            conn.close()

        payload = points_payload_from_row(row)
        return jsonify({"status": "ok", "rewarded": rewarded, "delta": POINTS_THEME_DAILY if rewarded else 0, **payload})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "主题奖励失败", "detail": repr(e)}), 500


@app.route("/redeem_member_code", methods=["POST"])
def redeem_member_code():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "error", "message": "请先登录"}), 401

        code = (request.form.get("code") or "").strip().upper()
        if not code:
            return jsonify({"status": "error", "message": "请输入会员码"}), 400

        if code != CHEAT_MEMBER_CODE:
            return jsonify({"status": "error", "message": "会员码无效"}), 400

        conn = get_conn()
        try:
            with conn.cursor() as c:
                ensure_points_row(c, user["id"])
                c.execute("""
                    UPDATE user_points
                    SET is_member = 1,
                        points = CASE WHEN points < %s THEN %s ELSE points END,
                        updated_at = %s
                    WHERE user_id = %s
                """, (POINTS_MEMBER_THRESHOLD, POINTS_MEMBER_THRESHOLD, now_str(), user["id"]))
                row = get_points_row(c, user["id"])
            conn.commit()
        finally:
            conn.close()

        payload = points_payload_from_row(row)
        return jsonify({"status": "ok", "upgraded": True, **payload})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "兑换失败", "detail": repr(e)}), 500


# ========= 注册 / 登录 / 退出 =========
@app.route("/register", methods=["POST"])
def register():
    try:
        tables = current_tables()
        mode = tables["mode"]
        user_table = tables["user_table"]

        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if len(username) < 2 or len(username) > 30:
            return jsonify({"status": "error", "message": "用户名长度需 2-30 个字符"})

        if len(password) < 2 or len(password) > 100:
            return jsonify({"status": "error", "message": "密码长度不合法"})

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(f"SELECT id FROM `{user_table}` WHERE username = %s", (username,))
                if c.fetchone():
                    return jsonify({"status": "error", "message": "用户名已存在"})

                if mode == "old":
                    c.execute(f"""
                        INSERT INTO `{user_table}` (username, password_hash, date, register_ip)
                        VALUES (%s, %s, %s, %s)
                    """, (username, password, now_str(), client_ip()))
                else:
                    c.execute(f"""
                        INSERT INTO `{user_table}` (username, password)
                        VALUES (%s, %s)
                    """, (username, password))

                # 新用户积分记录
                c.execute(f"SELECT id FROM `{user_table}` WHERE username = %s", (username,))
                u = c.fetchone()
                if u:
                    ensure_points_row(c, u["id"])

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

                ensure_points_row(c, user["id"])
            conn.commit()
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


@app.route("/change_username", methods=["POST"])
def change_username():
    try:
        user = get_current_user()
        if not user:
            return jsonify({"status": "error", "message": "请先登录"}), 401

        tables = current_tables()
        user_table = tables["user_table"]
        message_table = tables["message_table"]
        reply_table = tables["reply_table"]

        new_username = (request.form.get("new_username") or "").strip()
        if len(new_username) < 2 or len(new_username) > 30:
            return jsonify({"status": "error", "message": "用户名长度需 2-30 个字符"})

        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(f"SELECT id FROM `{user_table}` WHERE username = %s", (new_username,))
                if c.fetchone():
                    return jsonify({"status": "error", "message": "新用户名已存在"})

                old_username = user["username"]

                c.execute(f"UPDATE `{user_table}` SET username = %s WHERE id = %s", (new_username, user["id"]))
                c.execute(f"UPDATE `{message_table}` SET username = %s WHERE user_id = %s", (new_username, user["id"]))
                c.execute(f"UPDATE `{reply_table}` SET username = %s WHERE user_id = %s", (new_username, user["id"]))

                if table_exists("likes"):
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

        tables = current_tables()
        user_table = tables["user_table"]
        message_table = tables["message_table"]
        reply_table = tables["reply_table"]

        conn = get_conn()
        try:
            with conn.cursor() as c:
                if table_exists("likes"):
                    c.execute("DELETE FROM likes WHERE username = %s", (user["username"],))
                c.execute(f"DELETE FROM `{reply_table}` WHERE user_id = %s", (user["id"],))
                c.execute(f"DELETE FROM `{message_table}` WHERE user_id = %s", (user["id"],))
                c.execute(f"DELETE FROM `{user_table}` WHERE id = %s", (user["id"],))
                if table_exists("user_points"):
                    c.execute("DELETE FROM user_points WHERE user_id = %s", (user["id"],))
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

        member_code = (
            request.form.get("member_code")
            or request.form.get("vip_code")
            or ""
        ).strip().upper()

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

            # 有图：登录用户可“会员码”或“积分会员”二选一；匿名只能靠会员码
            can_image = False
            if member_code == CHEAT_MEMBER_CODE:
                can_image = True
            elif user_id:
                conn_tmp = get_conn()
                try:
                    with conn_tmp.cursor() as ctmp:
                        row = get_points_row(ctmp, user_id)
                        normalize_member_by_points(ctmp, user_id)
                        row = get_points_row(ctmp, user_id)
                        can_image = bool(int(row.get("is_member") or 0))
                    conn_tmp.commit()
                finally:
                    conn_tmp.close()
            if not can_image and not member_code:
                return jsonify({"status": "error", "message": "上传图片需要会员权限"}), 403

            ext = image.filename.rsplit(".", 1)[1].lower()
            safe_username = secure_filename(username) or "anonymous"
            filename = f"msg_{int(time.time())}_{safe_username}.{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, filename)
            image.save(save_path)
            image_path = to_static_url(save_path)

        # premium 逻辑：有会员码 / 积分会员 / 有图均可视为 premium 帖
        is_premium = 0
        if member_code:
            is_premium = 1
        elif user_id:
            conn_tmp2 = get_conn()
            try:
                with conn_tmp2.cursor() as ctmp2:
                    row2 = get_points_row(ctmp2, user_id)
                    normalize_member_by_points(ctmp2, user_id)
                    row2 = get_points_row(ctmp2, user_id)
                    if int(row2.get("is_member") or 0) == 1:
                        is_premium = 1
                conn_tmp2.commit()
            finally:
                conn_tmp2.close()

        conn = get_conn()
        try:
            with conn.cursor() as c:
                if mode == "old":
                    c.execute(f"""
                        INSERT INTO `{message_table}` (
                            ip, content, date, is_premium, username, image_path, user_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        client_ip(),
                        content,
                        now_str(),
                        is_premium,
                        username,
                        image_path,
                        user_id
                    ))
                else:
                    c.execute(f"""
                        INSERT INTO `{message_table}` (
                            username, user_id, content, image_path, is_premium, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        username,
                        user_id,
                        content,
                        image_path,
                        is_premium,
                        now_str()
                    ))

                # 积分：仅登录用户加分
                if user_id:
                    add_points(c, user_id, POINTS_POST_IMAGE if has_image else POINTS_POST_TEXT)

                    # 作弊码可直升会员
                    if member_code == CHEAT_MEMBER_CODE:
                        c.execute("""
                            UPDATE user_points
                            SET is_member = 1,
                                points = CASE WHEN points < %s THEN %s ELSE points END,
                                updated_at = %s
                            WHERE user_id = %s
                        """, (POINTS_MEMBER_THRESHOLD, POINTS_MEMBER_THRESHOLD, now_str(), user_id))

                points_row = get_points_row(c, user_id) if user_id else None

            conn.commit()
        finally:
            conn.close()

        resp = {"status": "ok", "mode": mode}
        if points_row:
            resp.update(points_payload_from_row(points_row))
        return jsonify(resp)

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

                if mode == "old":
                    c.execute(f"""
                        INSERT INTO `{reply_table}` (
                            ip, content, date, message_id, is_premium, username, user_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        client_ip(),
                        reply_content,
                        now_str(),
                        message_id,
                        0,
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

                if user_id:
                    add_points(c, user_id, POINTS_REPLY)
                    points_row = get_points_row(c, user_id)
                else:
                    points_row = None

            conn.commit()
        finally:
            conn.close()

        resp = {"status": "ok", "mode": mode}
        if points_row:
            resp.update(points_payload_from_row(points_row))
        return jsonify(resp)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "回复失败", "detail": repr(e)}), 500


# ========= 点赞 =========
@app.route("/toggle_like", methods=["POST"])
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
                msg = c.fetchone()
                if not msg:
                    return jsonify({"status": "error", "message": "留言不存在"})

                c.execute("SELECT id FROM likes WHERE message_id = %s AND username = %s", (message_id, user["username"]))
                existed = c.fetchone()

                if existed:
                    c.execute("DELETE FROM likes WHERE message_id = %s AND username = %s", (message_id, user["username"]))
                    liked = False
                else:
                    c.execute("INSERT IGNORE INTO likes (message_id, username) VALUES (%s, %s)", (message_id, user["username"]))
                    liked = True

                conn.commit()

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
                            m.id, m.username, m.user_id, m.content, m.image_path, m.is_premium,
                            m.date AS created_at, 0 AS like_count
                        FROM `{message_table}` m
                        ORDER BY m.id DESC
                    """)
                else:
                    c.execute(f"""
                        SELECT
                            m.id, m.username, m.user_id, m.content, m.image_path, m.is_premium, m.created_at,
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

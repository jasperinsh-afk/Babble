import os
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse, unquote
import pymysql
from flask import Flask, render_template, request, jsonify, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "replace-with-your-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==== 活动配置 ====
EVENT_START = datetime(2026, 5, 1)
EVENT_END = datetime(2026, 5, 5)
EVENT_REQUIRED_POSTS_PER_DAY = 2
EVENT_REQUIRED_INVITES = 3

def is_event_period():
    now = datetime.now()
    return EVENT_START.date() <= now.date() <= EVENT_END.date()

# ================== MySQL配置解析 ==================
def parse_mysql_uri(uri):
    if not uri:
        return {}
    parsed = urlparse(uri)
    database = parsed.path.lstrip("/") if parsed.path else None
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else "",
        "database": unquote(database) if database else None,
    }

# 优先使用 Railway 公共URL
def load_mysql_config():
    public_url = os.environ.get("MYSQL_PUBLIC_URL") or os.environ.get("MYSQL_URL")
    if public_url:
        parsed = parse_mysql_uri(public_url)
        return parsed
    # fallback 读取分开变量
    return {
        "host": os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST"),
        "port": int(os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT") or 3306),
        "user": os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER"),
        "password": (
            os.environ.get("MYSQLPASSWORD")
            or os.environ.get("MYSQL_PASSWORD")
            or os.environ.get("MYSQL_ROOT_PASSWORD")
        ),
        "database": os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE"),
    }

MYSQL_CONFIG = load_mysql_config()
MYSQL_HOST = MYSQL_CONFIG.get("host")
MYSQL_PORT = int(MYSQL_CONFIG.get("port") or 3306)
MYSQL_USER = MYSQL_CONFIG.get("user")
MYSQL_PASSWORD = MYSQL_CONFIG.get("password") or ""
MYSQL_DATABASE = MYSQL_CONFIG.get("database")

# ========== 数据库函数 ==========
def get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=int(MYSQL_PORT),
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    conn = get_conn()
    with conn.cursor() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(30) UNIQUE,
            password VARCHAR(255) NOT NULL,
            inviter_username VARCHAR(30) DEFAULT NULL
        ) CHARACTER SET utf8mb4
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NULL,
            username VARCHAR(64),
            content TEXT,
            created_at VARCHAR(32) NOT NULL
        ) CHARACTER SET utf8mb4
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id INT PRIMARY KEY,
            points INT DEFAULT 0,
            is_member TINYINT DEFAULT 0,
            updated_at VARCHAR(32) DEFAULT ''
        ) CHARACTER SET utf8mb4
        """)
    conn.commit()
    conn.close()

# ========== 路由 ==========
@app.route("/")
def index():
    return render_template("message.html")

@app.route("/me")
def me():
    username = session.get("username")
    if not username:
        return jsonify({"logged_in": False})

    conn = get_conn()
    with conn.cursor() as c:
        c.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = c.fetchone()
    conn.close()

    if not user:
        return jsonify({"logged_in": False})

    is_member = False
    # 活动期间判定逻辑
    if is_event_period():
        conn = get_conn()
        with conn.cursor() as c:
            # 每天发帖数
            c.execute("SELECT COUNT(*) AS cnt FROM messages WHERE user_id=%s AND DATE(created_at)=CURDATE()", (user["id"],))
            post_count = c.fetchone()["cnt"]
            # 被邀请人数
            c.execute("SELECT COUNT(*) AS cnt FROM users WHERE inviter_username=%s", (username,))
            invite_count = c.fetchone()["cnt"]
        conn.close()
        is_member = post_count >= EVENT_REQUIRED_POSTS_PER_DAY or invite_count >= EVENT_REQUIRED_INVITES
    else:
        conn = get_conn()
        with conn.cursor() as c:
            c.execute("SELECT is_member FROM user_points WHERE user_id=%s", (user["id"],))
            row = c.fetchone()
            if row:
                is_member = bool(row["is_member"])
        conn.close()

    return jsonify({"logged_in": True, "username": username, "is_member": is_member})

@app.route("/register", methods=["POST"])
def register():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    inviter = (request.form.get("inviter") or "").strip()
    if len(username) < 2 or len(password) < 2:
        return jsonify({"status": "error", "message": "用户名或密码无效"})

    conn = get_conn()
    with conn.cursor() as c:
        c.execute("SELECT id FROM users WHERE username=%s", (username,))
        if c.fetchone():
            return jsonify({"status": "error", "message": "用户名已存在"})
        c.execute("INSERT INTO users(username,password,inviter_username) VALUES(%s,%s,%s)",
                  (username, password, inviter or None))
        c.execute("SELECT id FROM users WHERE username=%s", (username,))
        user = c.fetchone()
        if user:
            c.execute("INSERT IGNORE INTO user_points(user_id,updated_at) VALUES(%s,%s)", (user["id"], now_str()))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

@app.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    conn = get_conn()
    with conn.cursor() as c:
        c.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = c.fetchone()
    conn.close()

    if not user:
        return jsonify({"status": "error", "message": "用户名或密码错误"})
    session["username"] = username
    return jsonify({"status": "ok", "username": username})

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "ok"})

@app.route("/upload", methods=["POST"])
def upload():
    username = session.get("username")
    content = (request.form.get("content") or "").strip()
    if not content:
        return jsonify({"status": "error", "message": "内容不能为空"})

    user_id = None
    if username:
        conn = get_conn()
        with conn.cursor() as c:
            c.execute("SELECT id FROM users WHERE username=%s", (username,))
            u = c.fetchone()
            if u:
                user_id = u["id"]
        conn.close()

    conn = get_conn()
    with conn.cursor() as c:
        c.execute("INSERT INTO messages(user_id, username, content, created_at) VALUES (%s,%s,%s,%s)",
                  (user_id, username or "匿名", content, now_str()))
    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "activity_mode": is_event_period()})

@app.route("/messages")
def messages():
    conn = get_conn()
    with conn.cursor() as c:
        c.execute("SELECT * FROM messages ORDER BY id DESC")
        rows = c.fetchall()
    conn.close()
    return jsonify({"status": "ok", "messages": rows})

# 初始化数据库
try:
    init_db()
    print("✅ DB INIT OK")
except Exception as e:
    traceback.print_exc()
    print("❌ DB INIT FAILED:", e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

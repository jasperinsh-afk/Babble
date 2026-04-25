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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "images")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# ==== 原积分配置保持不动 ====
POINTS_DAILY_CHECKIN = 5
POINTS_POST_TEXT = 10
POINTS_POST_IMAGE = 15
POINTS_REPLY = 5
POINTS_THEME_DAILY = 5
POINTS_MEMBER_THRESHOLD = 100
CHEAT_MEMBER_CODE = "114PZ514"

# ==== 五一活动配置 ====
EVENT_START = datetime(2026, 5, 1)
EVENT_END = datetime(2026, 5, 5)
EVENT_REQUIRED_POSTS_PER_DAY = 2
EVENT_REQUIRED_INVITES = 3

def is_event_period():
    now = datetime.now()
    return EVENT_START.date() <= now.date() <= EVENT_END.date()

def parse_mysql_uri(uri):
    if not uri: return {}
    parsed = urlparse(uri)
    scheme = (parsed.scheme or "").lower()
    if not scheme.startswith("mysql"): return {}
    database = parsed.path.lstrip("/") if parsed.path else None
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else "",
        "database": unquote(database) if database else None,
        "source_uri": uri,
    }

def load_mysql_config():
    config = {
        "host": os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST"),
        "port": os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT"),
        "user": os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER"),
        "password": (
            os.environ.get("MYSQLPASSWORD") or os.environ.get("MYSQL_PASSWORD")
            or os.environ.get("MYSQL_ROOT_PASSWORD")
        ),
        "database": os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE"),
        "source": "MYSQLHOST",
    }
    has_direct = bool(config["host"] and config["user"] and config["database"])
    if has_direct:
        try: config["port"] = int(config["port"] or 3306)
        except: config["port"] = 3306
        return config
    for key in ["SQLALCHEMY_DATABASE_URI", "DATABASE_URL", "MYSQL_URL"]:
        uri = os.environ.get(key)
        parsed = parse_mysql_uri(uri)
        if parsed.get("host") and parsed.get("user") and parsed.get("database"):
            return {
                "host": parsed["host"], "port": parsed["port"] or 3306,
                "user": parsed["user"], "password": parsed["password"] or "",
                "database": parsed["database"], "source": key,
            }
    try: config["port"] = int(config["port"] or 3306)
    except: config["port"] = 3306
    return config

MYSQL_CONFIG = load_mysql_config()
MYSQL_HOST = MYSQL_CONFIG.get("host")
MYSQL_PORT = MYSQL_CONFIG.get("port")
MYSQL_USER = MYSQL_CONFIG.get("user")
MYSQL_PASSWORD = MYSQL_CONFIG.get("password") or ""
MYSQL_DATABASE = MYSQL_CONFIG.get("database")

def validate_mysql_env():
    if not MYSQL_HOST or not MYSQL_USER or not MYSQL_DATABASE:
        raise RuntimeError("MySQL 环境未配置正确")

def get_conn():
    validate_mysql_env()
    return pymysql.connect(
        host=MYSQL_HOST,
        port=int(MYSQL_PORT),
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def is_valid_member_code(code): return (code or "").strip().upper() == CHEAT_MEMBER_CODE

def current_tables():
    return {"message_table": "messages", "reply_table": "replies", "user_table": "users"}

def get_current_user():
    username = session.get("username")
    if not username: return None
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM users WHERE username=%s", (username,))
            return c.fetchone()
    finally:
        conn.close()

# 初始化DB
def init_db():
    conn = get_conn()
    with conn.cursor() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(30) UNIQUE,
            password VARCHAR(255) NOT NULL
        ) CHARACTER SET utf8mb4
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT, username VARCHAR(64), content TEXT,
            created_at VARCHAR(32) NOT NULL
        ) CHARACTER SET utf8mb4
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id INT PRIMARY KEY,
            points INT DEFAULT 0,
            is_member TINYINT DEFAULT 0,
            last_checkin_date VARCHAR(16) DEFAULT '',
            extra_json TEXT,
            updated_at VARCHAR(32) DEFAULT ''
        ) CHARACTER SET utf8mb4
        """)
    conn.commit()
    conn.close()

@app.route("/")
def home(): return render_template("message.html")

@app.route("/me")
def me():
    user = get_current_user()
    if not user: return jsonify({"logged_in":False})
    conn = get_conn()
    with conn.cursor() as c:
        c.execute("SELECT * FROM user_points WHERE user_id=%s", (user["id"],))
        row = c.fetchone() or {}
    conn.close()
    # 活动期间会员认定
    if is_event_period():
        try:
            conn = get_conn()
            with conn.cursor() as c:
                # 检查今日发帖数
                c.execute("SELECT COUNT(*) as cnt FROM messages WHERE user_id=%s AND DATE(created_at)=CURDATE()",(user["id"],))
                count_today = c.fetchone()["cnt"]
                # 被邀请人数
                c.execute("SELECT COUNT(*) as cnt FROM users WHERE inviter_username=%s",(user["username"],))
                invite_cnt = c.fetchone()["cnt"]
                # 活动会员逻辑
                is_member = 1 if (count_today >= EVENT_REQUIRED_POSTS_PER_DAY or invite_cnt >= EVENT_REQUIRED_INVITES) else 0
        finally:
            conn.close()
    else:
        is_member = int(row.get("is_member") or 0)
    return jsonify({"logged_in":True,"username":user["username"],"is_member":bool(is_member)})

@app.route("/register",methods=["POST"])
def register():
    username=(request.form.get("username") or "").strip()
    password=(request.form.get("password") or "").strip()
    inviter=(request.form.get("inviter") or "").strip()
    if len(username)<2 or len(password)<2:
        return jsonify({"status":"error","message":"用户名或密码无效"})
    conn=get_conn()
    with conn.cursor() as c:
        c.execute("SELECT id FROM users WHERE username=%s",(username,))
        if c.fetchone():return jsonify({"status":"error","message":"用户名已存在"})
        c.execute("INSERT INTO users(username,password)VALUES(%s,%s)",(username,password))
        c.execute("SELECT id FROM users WHERE username=%s",(username,))
        u=c.fetchone()
        if u:
            c.execute("INSERT IGNORE INTO user_points(user_id,updated_at,extra_json)VALUES(%s,%s,%s)",(u["id"],now_str(),f"inviter:{inviter}"))
    conn.commit(); conn.close()
    return jsonify({"status":"ok"})

@app.route("/login",methods=["POST"])
def login():
    username=(request.form.get("username") or "").strip()
    password=(request.form.get("password") or "").strip()
    conn=get_conn()
    with conn.cursor() as c:
        c.execute("SELECT * FROM users WHERE username=%s AND password=%s",(username,password))
        u=c.fetchone()
    conn.close()
    if not u:return jsonify({"status":"error","message":"用户名或密码错误"})
    session["username"]=u["username"]
    return jsonify({"status":"ok","username":u["username"]})

@app.route("/logout",methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status":"ok"})

@app.route("/upload",methods=["POST"])
def upload():
    user=get_current_user()
    content=(request.form.get("content") or "").strip()
    if not content:
        return jsonify({"status":"error","message":"内容不能为空"})
    conn=get_conn()
    with conn.cursor() as c:
        c.execute("INSERT INTO messages(user_id,username,content,created_at)VALUES(%s,%s,%s,%s)",
                  (user["id"] if user else None,user["username"] if user else "匿名",content,now_str()))
    conn.commit(); conn.close()
    return jsonify({"status":"ok","activity_mode":is_event_period()})

@app.route("/messages")
def messages():
    conn=get_conn()
    with conn.cursor() as c:
        c.execute("SELECT * FROM messages ORDER BY id DESC")
        rows=c.fetchall()
    conn.close()
    return jsonify({"status":"ok","messages":rows})

init_db()

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=True)

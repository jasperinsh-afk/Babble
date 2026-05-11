import os
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse, unquote

import pymysql
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename

# ========= 初始化 =========
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "replace-with-your-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 上传文件限制 5MB

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "images")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# ========= MySQL 配置解析 =========
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
        problems.append("缺少 MYSQLHOST / MYSQL_HOST")
    if not MYSQL_USER:
        problems.append("缺少 MYSQLUSER / MYSQL_USER")
    if not MYSQL_DATABASE:
        problems.append("缺少 MYSQLDATABASE / MYSQL_DATABASE")
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
        autocommit=False
    )


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def allowed_image_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


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
        VALID_SPECIAL_CODE = "114PZ514"
        if code == VALID_SPECIAL_CODE:
            session["is_member"] = True
            session["points"] = 100
            return jsonify({"status": "ok", "is_member": True, "upgraded": True})
        return jsonify({"status": "fail", "message": "无效的特殊码"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "验证异常", "detail": repr(e)}), 500


# ========= 登录态接口 =========
@app.route("/me")
def me():
    try:
        username = session.get("username")
        if not username:
            return jsonify({"logged_in": False, "username": None})
        return jsonify({"logged_in": True, "username": username})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"logged_in": False, "username": None, "error": repr(e)}), 500


# ========= 新增：会员积分状态查询 =========
@app.route("/points_status")
def points_status():
    is_member = session.get("is_member", False)
    points = session.get("points", 0)
    return jsonify({
        "status": "ok",
        "is_member": bool(is_member),
        "points": int(points),
        "today_signed": False,
        "today_theme_rewarded": False
    })


# ========= 新增：留言列表接口 =========
@app.route("/messages")
def messages():
    demo_list = [
        {
            "id": 1,
            "username": "张三",
            "content": "第一条留言，测试一下。",
            "date": now_str(),
            "is_premium": session.get("is_member", False)
        },
        {
            "id": 2,
            "username": "匿名",
            "content": "第二条留言。",
            "date": now_str(),
            "is_premium": False
        }
    ]
    return jsonify({"status": "ok", "messages": demo_list})


# ========= 登录 / 登出 / 注册示例 =========
@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username")
    password = request.form.get("password")
    if username and password:
        session["username"] = username
        return jsonify({"status": "ok", "message": "登录成功", "username": username})
    return jsonify({"status": "fail", "message": "缺少用户名或密码"}), 400


@app.route("/logout")
def logout():
    session.clear()
    return jsonify({"status": "ok", "message": "已退出登录"})


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username")
    password = request.form.get("password")
    if not username or not password:
        return jsonify({"status": "fail", "message": "缺少用户名或密码"}), 400
    return jsonify({"status": "ok", "message": "注册成功(示例)", "username": username})


# ========= 图片上传示例 =========
@app.route("/upload_image", methods=["POST"])
def upload_image():
    if "image" not in request.files:
        return jsonify({"status": "fail", "message": "未选择文件"}), 400
    file = request.files["image"]
    if file.filename == "":
        return jsonify({"status": "fail", "message": "空文件名"}), 400
    if allowed_image_file(file.filename):
        filename = secure_filename(file.filename)
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(save_path)
        rel_path = os.path.relpath(save_path, BASE_DIR).replace("\\", "/")
        return jsonify({"status": "ok", "path": "/" + rel_path})
    return jsonify({"status": "fail", "message": "不支持的文件类型"}), 400


# ========= 错误处理 =========
@app.errorhandler(413)
def too_large(e):
    return jsonify({"status": "error", "message": "上传文件过大"}), 413


@app.errorhandler(500)
def internal_error(e):
    traceback.print_exc()
    return jsonify({"status": "error", "message": "服务器内部错误", "detail": repr(e)}), 500


# ========= 启动日志 =========
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
except Exception:
    print("========== DB INIT ERROR ==========")
    traceback.print_exc()
    print("===================================")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

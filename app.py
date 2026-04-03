import os
import random
import string
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

# =========================
# 基础配置
# =========================
app.secret_key = os.environ.get("SECRET_KEY", "replace-with-a-strong-secret-key")


def get_db_uri():
    raw_uri = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("MYSQL_URL")
        or os.environ.get("SQLALCHEMY_DATABASE_URI")
    )

    # 如果没有配置数据库，自动降级为 sqlite，保证应用能启动
    if not raw_uri:
        return "sqlite:///app.db"

    # 兼容 mysql://
    if raw_uri.startswith("mysql://"):
        raw_uri = raw_uri.replace("mysql://", "mysql+pymysql://", 1)

    return raw_uri


app.config["SQLALCHEMY_DATABASE_URI"] = get_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 仅对非 sqlite 启用连接池参数
db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
if db_uri.startswith("sqlite"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True
    }
else:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 180,
        "pool_size": 3,
        "max_overflow": 2,
        "pool_timeout": 30,
    }

UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

db = SQLAlchemy(app)


# =========================
# 数据模型
# =========================
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=True)
    date = db.Column(db.String(50), nullable=True)
    username = db.Column(db.String(30), nullable=True)
    image_path = db.Column(db.String(255), nullable=True)
    is_premium = db.Column(db.Integer, default=0)


class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    date = db.Column(db.String(50), nullable=True)
    username = db.Column(db.String(30), nullable=True)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    date = db.Column(db.String(50), nullable=True)
    register_ip = db.Column(db.String(50), nullable=True, index=True)


class PostLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50), index=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# =========================
# 工具函数
# =========================
def now_cn_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_real_ip(req):
    xff = req.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip(), True
    return req.remote_addr or "0.0.0.0", False


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def random_filename(filename):
    ext = filename.rsplit(".", 1)[1].lower()
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=18))
    return f"{rand}.{ext}"


def check_post_rate_limit(ip: str, limit=3, window_seconds=60):
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=window_seconds)

    # 清理过期日志
    PostLog.query.filter(PostLog.created_at < window_start).delete()
    db.session.commit()

    count = PostLog.query.filter(
        PostLog.ip == ip,
        PostLog.created_at >= window_start
    ).count()

    if count >= limit:
        return False, window_seconds

    db.session.add(PostLog(ip=ip, created_at=now))
    db.session.commit()
    return True, 0


def check_and_add_columns():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()

    if "user" in tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "register_ip" not in columns:
            try:
                db.session.execute(text("ALTER TABLE user ADD COLUMN register_ip VARCHAR(50)"))
                db.session.commit()
            except Exception:
                db.session.rollback()

    if "message" in tables:
        columns = [c["name"] for c in inspector.get_columns("message")]
        if "image_path" not in columns:
            try:
                db.session.execute(text("ALTER TABLE message ADD COLUMN image_path VARCHAR(255)"))
                db.session.commit()
            except Exception:
                db.session.rollback()
        if "is_premium" not in columns:
            try:
                db.session.execute(text("ALTER TABLE message ADD COLUMN is_premium INT DEFAULT 0"))
                db.session.commit()
            except Exception:
                db.session.rollback()
        if "username" not in columns:
            try:
                db.session.execute(text("ALTER TABLE message ADD COLUMN username VARCHAR(30)"))
                db.session.commit()
            except Exception:
                db.session.rollback()

    if "reply" in tables:
        columns = [c["name"] for c in inspector.get_columns("reply")]
        if "username" not in columns:
            try:
                db.session.execute(text("ALTER TABLE reply ADD COLUMN username VARCHAR(30)"))
                db.session.commit()
            except Exception:
                db.session.rollback()


def init_db():
    with app.app_context():
        db.create_all()
        check_and_add_columns()


# 在模块加载时初始化数据库，保证 gunicorn 启动时也会建表
init_db()


# =========================
# 页面路由
# =========================
@app.route("/")
def index():
    return redirect("/message")


@app.route("/message")
def message_page():
    return render_template("message.html")


# =========================
# 鉴权接口
# =========================
@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        return jsonify({"status": "error", "message": "用户名和密码不能为空"})
    if len(username) < 2 or len(username) > 30:
        return jsonify({"status": "error", "message": "用户名长度需在2-30之间"})

    ip, _ = get_real_ip(request)

    if User.query.filter_by(register_ip=ip).first():
        return jsonify({"status": "error", "message": "该IP已注册过账号，无法重复注册"})
    if User.query.filter_by(username=username).first():
        return jsonify({"status": "error", "message": "用户名已存在"})

    u = User(
        username=username,
        password_hash=generate_password_hash(password),
        date=now_cn_str(),
        register_ip=ip
    )
    db.session.add(u)
    db.session.commit()
    return jsonify({"status": "ok", "message": "注册成功"})


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"status": "error", "message": "用户不存在"})
    if not check_password_hash(user.password_hash, password):
        return jsonify({"status": "error", "message": "密码错误"})

    session["username"] = user.username
    return jsonify({"status": "ok", "username": user.username})


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("username", None)
    return jsonify({"status": "ok"})


@app.route("/me", methods=["GET"])
def me():
    username = session.get("username")
    if not username:
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "username": username})


@app.route("/change_username", methods=["POST"])
def change_username():
    if not session.get("username"):
        return jsonify({"status": "error", "message": "请先登录"}), 401

    new_username = request.form.get("new_username", "").strip()
    if not new_username:
        return jsonify({"status": "error", "message": "新用户名不能为空"})
    if len(new_username) < 2 or len(new_username) > 30:
        return jsonify({"status": "error", "message": "用户名长度需在2-30之间"})
    if User.query.filter_by(username=new_username).first():
        return jsonify({"status": "error", "message": "用户名已存在"})

    old_username = session["username"]
    user = User.query.filter_by(username=old_username).first()
    if not user:
        session.pop("username", None)
        return jsonify({"status": "error", "message": "用户不存在，请重新登录"}), 401

    user.username = new_username
    Message.query.filter_by(username=old_username).update({"username": new_username})
    Reply.query.filter_by(username=old_username).update({"username": new_username})
    db.session.commit()

    session["username"] = new_username
    return jsonify({"status": "ok", "username": new_username})


@app.route("/delete_account", methods=["POST"])
def delete_account():
    if not session.get("username"):
        return jsonify({"status": "error", "message": "请先登录"}), 401

    username = session["username"]
    user = User.query.filter_by(username=username).first()
    if not user:
        session.pop("username", None)
        return jsonify({"status": "error", "message": "用户不存在"}), 404

    Message.query.filter_by(username=username).update({"username": "已注销用户"})
    Reply.query.filter_by(username=username).update({"username": "已注销用户"})
    db.session.delete(user)
    db.session.commit()
    session.pop("username", None)

    return jsonify({"status": "ok", "message": "账号已注销"})


# =========================
# 消息接口
# =========================
@app.route("/messages", methods=["GET"])
def get_messages():
    msgs = Message.query.order_by(Message.id.desc()).all()
    all_replies = Reply.query.order_by(Reply.id.asc()).all()

    reply_map = {}
    for r in all_replies:
        reply_map.setdefault(r.message_id, []).append({
            "id": r.id,
            "content": r.content,
            "date": r.date,
            "username": r.username or "匿名"
        })

    data = []
    for m in msgs:
        data.append({
            "id": m.id,
            "content": m.content or "",
            "date": m.date or "",
            "username": m.username or "匿名",
            "image_path": m.image_path or "",
            "is_premium": int(m.is_premium or 0),
            "replies": reply_map.get(m.id, [])
        })

    return jsonify({"status": "ok", "messages": data})


@app.route("/upload", methods=["POST"])
def upload():
    username = session.get("username")
    if not username:
        return jsonify({"status": "error", "message": "请先登录后再发帖"}), 401

    ip, _ = get_real_ip(request)
    ok, _ = check_post_rate_limit(ip, limit=3, window_seconds=60)
    if not ok:
        return jsonify({"status": "error", "message": "发送过于频繁：60秒内最多3次"}), 429

    content = (request.form.get("content") or "").strip()
    member_code = (request.form.get("member_code") or "").strip().upper()

    file = request.files.get("image")
    image_path = None

    if file and file.filename:
        if not allowed_file(file.filename):
            return jsonify({"status": "error", "message": "图片格式不支持，仅允许 png/jpg/jpeg/gif/webp"})
        filename = random_filename(secure_filename(file.filename))
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(save_path)
        image_path = f"/static/uploads/{filename}"

    if not content and not image_path:
        return jsonify({"status": "error", "message": "内容和图片至少填写一个"})

    is_premium = 1 if member_code == "XINHUIYUAN888" else 0

    m = Message(
        content=content,
        date=now_cn_str(),
        username=username,
        image_path=image_path,
        is_premium=is_premium
    )
    db.session.add(m)
    db.session.commit()
    return jsonify({"status": "ok"})


@app.route("/reply", methods=["POST"])
def reply():
    username = session.get("username")
    if not username:
        return jsonify({"status": "error", "message": "请先登录后再回复"}), 401

    ip, _ = get_real_ip(request)
    ok, _ = check_post_rate_limit(ip, limit=3, window_seconds=60)
    if not ok:
        return jsonify({"status": "error", "message": "发送过于频繁：60秒内最多3次"}), 429

    message_id = request.form.get("message_id", "").strip()
    content = (request.form.get("reply_content") or "").strip()

    if not message_id.isdigit():
        return jsonify({"status": "error", "message": "message_id非法"})
    if not content:
        return jsonify({"status": "error", "message": "回复内容不能为空"})

    msg = db.session.get(Message, int(message_id))
    if not msg:
        return jsonify({"status": "error", "message": "原消息不存在"}), 404

    r = Reply(
        message_id=int(message_id),
        content=content,
        date=now_cn_str(),
        username=username
    )
    db.session.add(r)
    db.session.commit()
    return jsonify({"status": "ok"})


# =========================
# 启动入口
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

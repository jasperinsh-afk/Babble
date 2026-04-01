from flask import Flask, render_template, request, redirect, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import time
import os
from datetime import datetime
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash

def get_real_ip(req):
    """
    Railway / 代理环境下获取真实客户端 IP
    """
    xff = req.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip(), "X-Forwarded-For"

    xri = req.headers.get("X-Real-IP")
    if xri:
        return xri.strip(), "X-Real-IP"

    return req.remote_addr or "", "REMOTE_ADDR"

# =========================
# 基础配置
# =========================

os.makedirs("static/uploads", exist_ok=True)

def now_cn_str():
    utc_timestamp = time.time()
    beijing_timestamp = utc_timestamp + 8 * 3600
    beijing_dt = datetime.utcfromtimestamp(beijing_timestamp)
    return beijing_dt.strftime("%Y-%m-%d %H:%M:%S")

print("=== 服务器时间调试信息 ===")
print(f"当前时间戳: {time.time()}")
print(f"本地时间: {datetime.now()}")
print(f"UTC时间: {datetime.utcnow()}")
print(f"计算的北京时间: {now_cn_str()}")
print("=========================")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'MolicaSecret'

db = SQLAlchemy(app)

# =========================
# 数据模型
# =========================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    date = db.Column(db.String(50))

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
    username = db.Column(db.String(30), default='匿名')  # 新增：发帖用户名
    content = db.Column(db.Text)
    date = db.Column(db.String(50))
    is_premium = db.Column(db.String(1), default='0')
    replies = db.relationship(
        'Reply',
        backref='message',
        lazy='dynamic',
        cascade="all, delete-orphan"
    )

class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
    username = db.Column(db.String(30), default='匿名')  # 新增：回复用户名
    content = db.Column(db.Text)
    date = db.Column(db.String(50))
    is_premium = db.Column(db.String(1), default='0')
    message_id = db.Column(
        db.Integer,
        db.ForeignKey('message.id'),
        nullable=False
    )

# =========================
# 自动补列逻辑（无需手动SQL）
# =========================

def check_and_add_columns():
    print("🔍 正在检查数据库结构...")
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()

    # message.username
    if 'message' in tables:
        columns = [c['name'] for c in inspector.get_columns('message')]
        if 'username' not in columns:
            try:
                print("➕ 添加 message.username")
                db.session.execute(
                    text("ALTER TABLE message ADD COLUMN username VARCHAR(30) DEFAULT '匿名'")
                )
                db.session.commit()
            except Exception as e:
                print("⚠️ 添加 message.username 失败:", e)
                db.session.rollback()

    # reply.is_premium（兼容你旧库）
    if 'reply' in tables:
        columns = [c['name'] for c in inspector.get_columns('reply')]
        if 'is_premium' not in columns:
            try:
                print("➕ 添加 reply.is_premium")
                db.session.execute(
                    text("ALTER TABLE reply ADD COLUMN is_premium VARCHAR(1) DEFAULT '0'")
                )
                db.session.commit()
            except Exception as e:
                print("⚠️ 添加 reply.is_premium 失败:", e)
                db.session.rollback()

    # reply.username
    if 'reply' in tables:
        columns = [c['name'] for c in inspector.get_columns('reply')]
        if 'username' not in columns:
            try:
                print("➕ 添加 reply.username")
                db.session.execute(
                    text("ALTER TABLE reply ADD COLUMN username VARCHAR(30) DEFAULT '匿名'")
                )
                db.session.commit()
            except Exception as e:
                print("⚠️ 添加 reply.username 失败:", e)
                db.session.rollback()

    print("✅ 数据库结构检查完成")

with app.app_context():
    db.create_all()            # 自动建 user 表
    check_and_add_columns()    # 自动补 message/reply 字段

# =========================
# 页面路由
# =========================

@app.route("/")
@app.route("/index")
def home():
    return render_template("index.html")

@app.route("/message")
def message():
    msgs = Message.query.order_by(Message.id.desc()).all()
    return render_template("message.html", data=msgs)

# =========================
# 账号功能
# =========================

@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        return jsonify({"status": "error", "message": "用户名和密码不能为空"})

    if len(username) < 2 or len(username) > 30:
        return jsonify({"status": "error", "message": "用户名长度需在2-30之间"})

    if len(password) < 4:
        return jsonify({"status": "error", "message": "密码至少4位"})

    exists = User.query.filter_by(username=username).first()
    if exists:
        return jsonify({"status": "error", "message": "用户名已存在"})

    u = User(
        username=username,
        password_hash=generate_password_hash(password),
        date=now_cn_str()
    )
    db.session.add(u)
    db.session.commit()
    return jsonify({"status": "ok", "message": "注册成功"})

@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"status": "error", "message": "用户名或密码错误"})

    session["username"] = user.username
    return jsonify({"status": "ok", "username": user.username})

@app.route("/logout", methods=["POST"])
def logout():
    session.pop("username", None)
    return jsonify({"status": "ok"})

@app.route("/api/me")
def api_me():
    return jsonify({
        "logged_in": bool(session.get("username")),
        "username": session.get("username", "")
    })

# =========================
# 上传留言
# =========================

@app.route("/upload", methods=["POST"])
def upload():
    ip, ip_source = get_real_ip(request)

    content = request.form.get("content", "").strip()
    is_premium = request.form.get("is_premium", "0")
    date = now_cn_str()
    username = session.get("username", "匿名")

    if not content:
        return redirect("/message")

    msg = Message(
        ip=ip,
        username=username,
        content=content,
        date=date,
        is_premium=is_premium
    )

    db.session.add(msg)
    db.session.commit()

    print(f"📌 新留言 IP: {ip} | 来源: {ip_source} | 用户: {username}")

    return redirect("/message")

# =========================
# 回复
# =========================

@app.route("/reply", methods=["POST"])
def reply():
    ip, ip_source = get_real_ip(request)

    content = request.form.get("reply_content", "").strip()
    message_id = int(request.form.get("message_id"))
    is_premium = request.form.get("is_premium", "0")
    date = now_cn_str()
    username = session.get("username", "匿名")

    if not content:
        return jsonify({"status": "error", "message": "回复内容不能为空"})

    r = Reply(
        ip=ip,
        username=username,
        content=content,
        date=date,
        message_id=message_id,
        is_premium=is_premium
    )

    db.session.add(r)
    db.session.commit()

    print(f"📌 新回复 IP: {ip} | 来源: {ip_source} | 用户: {username}")

    return jsonify({"status": "ok"})

# =========================
# API
# =========================

@app.route("/api/messages")
def api_messages():
    msgs = Message.query.order_by(Message.id.desc()).all()
    data = []

    for m in msgs:
        item = {
            "id": m.id,
            "content": m.content,
            "date": m.date,
            "username": m.username or "匿名",
            "is_premium": m.is_premium,
            "replies": []
        }
        for r in m.replies:
            item["replies"].append({
                "content": r.content,
                "date": r.date,
                "username": r.username or "匿名",
                "is_premium": r.is_premium
            })
        data.append(item)

    return jsonify({"data": data})

# =========================
# 启动
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)

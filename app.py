from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
import time
import os
from datetime import datetime
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, text


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

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
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
    content = db.Column(db.Text)
    date = db.Column(db.String(50))
    is_premium = db.Column(db.String(1), default='0')
    message_id = db.Column(
        db.Integer,
        db.ForeignKey('message.id'),
        nullable=False
    )

# =========================
# 🚑 兜底方案：强制重建 message.is_premium
# =========================

def force_rebuild_message_is_premium():
    print("🔥 启动兜底修复：重建 message.is_premium")

    try:
        inspector = inspect(db.engine)
        if 'message' not in inspector.get_table_names():
            print("⚠️ message 表不存在，跳过兜底")
            return

        columns = [c['name'] for c in inspector.get_columns('message')]

        if 'is_premium' in columns:
            print("🗑️ 删除 message.is_premium ...")
            db.session.execute(
                text("ALTER TABLE message DROP COLUMN is_premium")
            )
            db.session.commit()
            print("✅ 已删除 message.is_premium")

        print("🔧 重建 message.is_premium（默认 0）...")
        db.session.execute(
            text(
                "ALTER TABLE message "
                "ADD COLUMN is_premium VARCHAR(1) NOT NULL DEFAULT '0'"
            )
        )
        db.session.commit()
        print("✅ message.is_premium 重建完成")

    except Exception as e:
        print("❌ 兜底修复失败：", e)
        db.session.rollback()


# =========================
# 正常补列逻辑（安全）
# =========================

def check_and_add_columns():
    print("🔍 正在检查数据库结构...")
    inspector = inspect(db.engine)

    if 'reply' in inspector.get_table_names():
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

    print("✅ 数据库结构检查完成")

# =========================
# 启动时执行
# =========================

with app.app_context():
    db.create_all()
    force_rebuild_message_is_premium()  # 🚑 只需成功跑一次
    check_and_add_columns()

# =========================
# 路由
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
# 上传
# =========================

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/upload", methods=["POST"])
def upload():
    ip = request.remote_addr
    content = request.form.get("content", "").strip()
    is_premium = request.form.get("is_premium", "0")
    date = now_cn_str()

    file = request.files.get("image")
    image_url = None

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique = f"{int(time.time())}_{filename}"
        path = os.path.join(app.root_path, "static/uploads", unique)
        file.save(path)
        image_url = url_for("static", filename=f"uploads/{unique}")

    if image_url:
        content = f"[图片]({image_url})\n{content}"

    if not content:
        return redirect("/message")

    msg = Message(
        ip=ip,
        content=content,
        date=date,
        is_premium=is_premium
    )
    db.session.add(msg)
    db.session.commit()

    return redirect("/message")

# =========================
# 回复
# =========================

@app.route("/reply", methods=["POST"])
def reply():
    ip = request.remote_addr
    content = request.form.get("reply_content", "")
    message_id = int(request.form.get("message_id"))
    is_premium = request.form.get("is_premium", "0")
    date = now_cn_str()

    r = Reply(
        ip=ip,
        content=content,
        date=date,
        message_id=message_id,
        is_premium=is_premium
    )
    db.session.add(r)
    db.session.commit()

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
            "is_premium": m.is_premium,
            "replies": []
        }
        for r in m.replies:
            item["replies"].append({
                "content": r.content,
                "date": r.date,
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

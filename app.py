from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
import time
import os
from datetime import datetime, timedelta
import sys
from werkzeug.utils import secure_filename

# 自动创建图片保存目录
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
print(f"time.tzname: {time.tzname}")
print("=========================")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'MolicaSecret'
db = SQLAlchemy(app)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
    content = db.Column(db.Text)
    date = db.Column(db.String(50))
    replies = db.relationship('Reply', backref='message', lazy='dynamic', cascade="all, delete-orphan")

class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
    content = db.Column(db.Text)
    date = db.Column(db.String(50))
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)

with app.app_context():
    db.create_all()

@app.route("/")
@app.route("/index")
def home():
    return render_template("index.html")

@app.route("/download")
def download():
    return render_template("download.html")

@app.route("/message")
def message():
    msgs = Message.query.order_by(Message.id.desc()).all()
    return render_template("message.html", data=msgs)

# 允许上传的图片类型
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/upload", methods=["POST"])
def upload():
    ip = request.remote_addr
    content = request.form.get("content", "")
    date = now_cn_str()

    # 处理图片上传
    file = request.files.get("image")
    image_url = None
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_name = f"{int(time.time())}_{filename}"
        save_path = os.path.join(app.root_path, 'static', 'uploads', unique_name)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        file.save(save_path)
        # 使用 url_for 生成正确的静态文件路径，确保有斜杠开头
        image_url = url_for('static', filename=f'uploads/{unique_name}', _external=False)
        # 确保 image_url 以斜杠开头
        if not image_url.startswith('/'):
            image_url = '/' + image_url

    # 如果有图片，把图片链接加到内容前面
    if image_url:
        content = f"[图片]({image_url})\n{content.strip()}"
    else:
        content = content.strip()

    if not content:
        print(f"【上传调试】内容为空，忽略提交。")
        return redirect('/message')

    print(f"【上传调试】接收到数据 -> IP: {ip}, 时间: {date}, 内容: {content[:100]}...")

    try:
        new_msg = Message(ip=ip, content=content, date=date)
        db.session.add(new_msg)
        db.session.commit()
        print(f"【上传调试】成功写入数据库，消息ID: {new_msg.id}")
    except Exception as e:
        db.session.rollback()
        print(f"【上传调试】严重错误：数据写入数据库失败！原因: {e}")

    return redirect('/message')

@app.route("/reply", methods=["POST"])
def reply():
    ip = request.remote_addr
    reply_content = request.form.get("reply_content")
    message_id = request.form.get("message_id")
    date = now_cn_str()

    print(f"回复消息 - 时间: {date}")

    try:
        message_id_int = int(message_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "无效的 message_id"}), 400

    new_reply = Reply(ip=ip, content=reply_content, date=date, message_id=message_id_int)
    db.session.add(new_reply)
    db.session.commit()
    return jsonify({"status": "ok", "message": "回复已保存"})

@app.route("/api/messages")
def api_messages():
    print(f"【API调试】/api/messages 被请求，正在查询数据库...")
    msgs = Message.query.order_by(Message.id.desc()).all()
    print(f"【API调试】查询完成，共找到 {len(msgs)} 条消息。")
    result = []
    for m in msgs:
        result.append({
            "id": m.id,
            "content": m.content,
            "date": m.date,
            "replies": [{"content": r.content, "date": r.date} for r in m.replies]
        })
    return jsonify({"data": result})

if __name__ == "__main__":
    app.run(host="192.168.3.60", port=8080, debug=True)

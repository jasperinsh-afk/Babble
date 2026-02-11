from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
import time
import os
from flask import jsonify
from datetime import datetime, timedelta
import sys

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
    replies = db.relationship('Reply', backref='message', cascade="all,delete")

class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
    content = db.Column(db.Text)
    date = db.Column(db.String(50))
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'))

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

@app.route("/upload", methods=["POST"])
def upload():
    ip = request.remote_addr
    content = request.form.get("content")
    date = now_cn_str()

    if not content or not content.strip():
        print(f"【上传调试】内容为空，忽略提交。")
        return redirect('/message')

    print(f"【上传调试】接收到数据 -> IP: {ip}, 时间: {date}, 内容: {content[:100]}...")

    try:
        new_msg = Message(ip=ip, content=content.strip(), date=date)
        db.session.add(new_msg)
        db.session.commit()
        print(f"【上传调试】成功写入数据库，消息ID: {new_msg.id}")
    except Exception as e:
        db.session.rollback()
        print(f"【上传调试】严重错误：数据写入数据库失败！原因: {e}")

    return redirect('/message')

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


@app.route("/api/messages")
def api_messages():
    msgs = Message.query.order_by(Message.id.desc()).all()
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
    app.run("192.168.3.60", 8080, debug=True)

from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
import time
import os
from flask import jsonify
from datetime import datetime, timedelta
import sys

def now_cn_str():
    """
    强制使用时间戳计算北京时间，避免任何时区问题
    """
    # 获取当前UTC时间戳
    utc_timestamp = time.time()
    
    # 计算北京时间戳（UTC+8）
    beijing_timestamp = utc_timestamp + 8 * 3600
    
    # 从时间戳创建datetime对象
    # 使用fromtimestamp的UTC版本，避免本地时区干扰
    beijing_dt = datetime.utcfromtimestamp(beijing_timestamp)
    
    return beijing_dt.strftime("%Y-%m-%d %H:%M:%S")

# 调试：显示各种时间
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
    
    print(f"上传消息 - 时间: {date}")

    new_msg = Message(ip=ip, content=content, date=date)
    db.session.add(new_msg)
    db.session.commit()
    return redirect('/message')

@app.route("/reply", methods=["POST"])
def reply():
    ip = request.remote_addr
    reply_content = request.form.get("reply_content")
    message_id = request.form.get("message_id")
    date = now_cn_str()
    
    print(f"回复消息 - 时间: {date}")

    new_reply = Reply(ip=ip, content=reply_content, date=date, message_id=message_id)
    db.session.add(new_reply)
    db.session.commit()
    return redirect('/message')

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

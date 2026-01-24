from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
import time
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'MolicaSecret'
db = SQLAlchemy(app)

# ---------------- 数据模型 ----------------
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

# ---------------- 初始化 ----------------
with app.app_context():
    db.create_all()

# ---------------- 路由部分 ----------------
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
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    new_msg = Message(ip=ip, content=content, date=date)
    db.session.add(new_msg)
    db.session.commit()
    return redirect('/message')

@app.route("/reply", methods=["POST"])
def reply():
    ip = request.remote_addr
    reply_content = request.form.get("reply_content")
    message_id = request.form.get("message_id")
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    new_reply = Reply(ip=ip, content=reply_content, date=date, message_id=message_id)
    db.session.add(new_reply)
    db.session.commit()
    return redirect('/message')

if __name__ == "__main__":
    app.run("192.168.3.60", 8080)

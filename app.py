'''
import flask
import json
import time

app = flask.Flask(__name__, static_url_path="/", static_folder="static")


@app.route("/")
@app.route("/index")
def home():
    f = open("./static/index.html", "rb")
    page = f.read()
    f.close()
    return page


@app.route("/download")
def download():
    f = open("./static/download.html", "rb")
    page = f.read()
    f.close()
    return page


@app.route("/message")
def message():
    f = open("./static/data.json", "rb")
    data = json.load(f)
    f.close()

    return flask.render_template("message.html", data=data)  # 将数据渲染到页面中


@app.route("/upload", methods=["POST", "GET"])

def upload():

    # 获取客户端ip地址
    ip = flask.request.remote_addr
    # 获取消息
    content = flask.request.form.get("content")
    # 获取特定格式的时间
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    # 将数据保存在字典中
    dic = {
        #"ip": ip,
        "content": content,
        "date": date
    }

    # 读取json文件
    f = open("./static/data.json", "rb")
    data = json.load(f) # 将数据转换成字典
    data["messages"].append(dic)
    f.close()

    # 写入保存json文件
    f = open("./static/data.json", "wb")
    f.write(json.dumps(data).encode("utf-8"))  # 字典转成字符串
    f.close()

    return flask.redirect('/message')

@app.route("/reply", methods=["POST"])
def reply():
    ip = flask.request.remote_addr
    reply_content = flask.request.form.get("reply_content")
    index = int(flask.request.form.get("index"))
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    reply_dic = {
        "ip": ip,
        "content": reply_content,
        "date": date
    }

    with open("./static/data.json", "rb") as f:
        data = json.load(f)

    # 若当前留言还没有 replies 字段则添加
    if "replies" not in data["messages"][index]:
        data["messages"][index]["replies"] = []

    data["messages"][index]["replies"].append(reply_dic)

    with open("./static/data.json", "wb") as f:
        f.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    return flask.redirect('/message')

app.run("192.168.3.60", 8080)
'''
from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
import time
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('mysql+pymysql://root:FXYQAUycRSHhvuAnFKtOvgRgVbGNSNfj@containers.railway.app:3306/railway')
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

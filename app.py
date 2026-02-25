from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect  # 新增：用于检查数据库结构
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
    is_premium = db.Column(db.String(1), default='0')  # 新增字段：是否为炫彩帖子
    replies = db.relationship('Reply', backref='message', lazy='dynamic', cascade="all, delete-orphan")

class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
    content = db.Column(db.Text)
    date = db.Column(db.String(50))
    is_premium = db.Column(db.String(1), default='0')  # 新增字段：是否为炫彩回复
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)

def check_and_add_columns():
    """检查并添加缺失的数据库列（不删除现有数据）"""
    print("🔍 正在检查数据库表结构...")
    
    inspector = inspect(db.engine)
    
    # 检查 message 表
    if 'message' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('message')]
        
        if 'is_premium' not in existing_columns:
            try:
                print("🔄 检测到 message 表缺少 is_premium 列，正在添加...")
                db.session.execute('ALTER TABLE message ADD COLUMN is_premium VARCHAR(1) DEFAULT "0"')
                db.session.commit()
                print("✅ 已成功为 message 表添加 is_premium 列")
            except Exception as e:
                print(f"⚠️ 添加 message.is_premium 列失败: {e}")
                db.session.rollback()
        else:
            print("✅ message 表结构完整")
    
    # 检查 reply 表
    if 'reply' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('reply')]
        
        if 'is_premium' not in existing_columns:
            try:
                print("🔄 检测到 reply 表缺少 is_premium 列，正在添加...")
                db.session.execute('ALTER TABLE reply ADD COLUMN is_premium VARCHAR(1) DEFAULT "0"')
                db.session.commit()
                print("✅ 已成功为 reply 表添加 is_premium 列")
            except Exception as e:
                print(f"⚠️ 添加 reply.is_premium 列失败: {e}")
                db.session.rollback()
        else:
            print("✅ reply 表结构完整")
    
    print("📊 数据库表结构检查完成")

with app.app_context():
    # 创建表（如果不存在）
    db.create_all()
    
    # 检查并添加缺失的列
    check_and_add_columns()

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
    is_premium = request.form.get("is_premium", "0")  # 新增：获取炫彩标记
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

    print(f"【上传调试】接收到数据 -> IP: {ip}, 时间: {date}, 炫彩: {is_premium}, 内容: {content[:100]}...")

    try:
        new_msg = Message(ip=ip, content=content, date=date, is_premium=is_premium)  # 保存炫彩标记
        db.session.add(new_msg)
        db.session.commit()
        print(f"【上传调试】成功写入数据库，消息ID: {new_msg.id}, 炫彩: {is_premium}")
    except Exception as e:
        db.session.rollback()
        print(f"【上传调试】严重错误：数据写入数据库失败！原因: {e}")

    return redirect('/message')

@app.route("/reply", methods=["POST"])
def reply():
    ip = request.remote_addr
    reply_content = request.form.get("reply_content")
    message_id = request.form.get("message_id")
    is_premium = request.form.get("is_premium", "0")  # 新增：获取炫彩标记
    date = now_cn_str()

    print(f"回复消息 - 时间: {date}, 炫彩: {is_premium}")

    try:
        message_id_int = int(message_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "无效的 message_id"}), 400

    new_reply = Reply(ip=ip, content=reply_content, date=date, 
                     message_id=message_id_int, is_premium=is_premium)  # 保存炫彩标记
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
        # 安全地获取 is_premium 字段（如果存在）
        is_premium_value = getattr(m, 'is_premium', '0')
        
        msg_data = {
            "id": m.id,
            "content": m.content,
            "date": m.date,
            "is_premium": is_premium_value,  # 返回炫彩标记
            "replies": []
        }
        
        for r in m.replies:
            # 安全地获取回复的 is_premium 字段
            reply_is_premium = getattr(r, 'is_premium', '0')
            msg_data["replies"].append({
                "content": r.content,
                "date": r.date,
                "is_premium": reply_is_premium  # 回复也返回炫彩标记
            })
        
        result.append(msg_data)
    return jsonify({"data": result})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))  # 获取环境变量 PORT，如果没有则用 8080
    app.run(host="0.0.0.0", port=port, debug=True) # host 必须是 0.0.0.0

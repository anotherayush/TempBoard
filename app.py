from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
import string
import random
import datetime
import io
from docx.api import Document
import base64

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///textboards.db'
app.config['SECRET_KEY'] = 'your_secret_key'
db = SQLAlchemy(app)
socketio = SocketIO(app)

import colorsys

def generate_color():
    hue = random.random()
    saturation = 0.7
    value = 0.8
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}' 

class TextBoard(db.Model):
    id = db.Column(db.String(4), primary_key=True)
    content = db.Column(db.Text)
    last_active = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    active_users = db.Column(db.Integer, default=0)
    images = db.Column(db.Text, default='[]')  

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    board_id = db.Column(db.String(4), db.ForeignKey('text_board.id'), nullable=False)
    color = db.Column(db.String(7), nullable=False)  # Store user color

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    board_id = db.Column(db.String(4), db.ForeignKey('text_board.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

def generate_unique_code():
    while True:
        code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        if not db.session.get(TextBoard, code):
            return code

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_board', methods=['POST'])
def create_board():
    code = generate_unique_code()
    new_board = TextBoard(id=code, content='', active_users=0)
    db.session.add(new_board)
    db.session.commit()
    return jsonify({"code": code})

@app.route('/<code>', methods=['GET', 'POST'])
def board(code):
    board = TextBoard.query.get_or_404(code)
    if request.method == 'POST':
        username = request.form['username']
        if User.query.filter_by(username=username, board_id=code).first():
            return "Username already taken for this board", 400
        user = User(username=username, board_id=code, color=generate_color())
        db.session.add(user)
        board.active_users += 1
        db.session.commit()
        session['username'] = username
        session['board_id'] = code
        socketio.emit('user_joined', {'username': username, 'color': user.color}, room=code)
    if 'username' not in session or session['board_id'] != code:
        return render_template('join_board.html', code=code)
    return render_template('board.html', board=board, username=session['username'])

import json

@app.route('/update_board/<code>', methods=['POST'])
def update_board(code):
    board = TextBoard.query.get_or_404(code)
    data = request.json
    board.content = data['content']
    if 'image' in data:
        images = json.loads(board.images)
        images.append(data['image'])
        board.images = json.dumps(images)
    board.last_active = datetime.datetime.utcnow()
    db.session.commit()
    socketio.emit('update_content', {'content': board.content, 'images': json.loads(board.images)}, room=code)
    return jsonify({"success": True})

@app.route('/leave_board/<code>')
def leave_board(code):
    board = TextBoard.query.get_or_404(code)
    username = session.pop('username', None)
    session.pop('board_id', None)
    if username:
        user = User.query.filter_by(username=username, board_id=code).first()
        if user:
            db.session.delete(user)
        board.active_users -= 1
        db.session.commit()
        socketio.emit('user_left', {'username': username}, room=code)
    if board.active_users <= 0:
        messages = Message.query.filter_by(board_id=code).all()
        for message in messages:
            db.session.delete(message)
        db.session.delete(board)
        db.session.commit()
    return jsonify({"success": True}) 

@app.route('/get_active_users/<code>')
def get_active_users(code):
    users = User.query.filter_by(board_id=code).all()
    return jsonify({'users': [{'username': user.username, 'color': user.color} for user in users]})

@app.route('/export_board/<code>')
def export_board(code):
    board = TextBoard.query.get_or_404(code)
    content = board.content
    images = json.loads(board.images)
    for i, img_data in enumerate(images):
        content += f"\n\n[Image {i+1}]"
    
    buffer = io.BytesIO()
    buffer.write(content.encode())
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=f"board_{code}.txt", mimetype="text/plain")

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)

@socketio.on('leave')
def on_leave(data):
    room = data['room']
    leave_room(room)

@socketio.on('cursor_move')
def on_cursor_move(data):
    emit('cursor_update', data, room=data['room'], include_self=False)

@socketio.on('text_selection')
def on_text_selection(data):
    emit('selection_update', data, room=data['room'], include_self=False)

@socketio.on('send_message')
def on_send_message(data):
    user = User.query.filter_by(username=data['username'], board_id=data['room']).first()
    message = Message(user_id=user.id, board_id=data['room'], content=data['message'])
    db.session.add(message)
    db.session.commit()
    emit('new_message', {
        'username': user.username,
        'color': user.color,
        'message': data['message'],
        'timestamp': message.timestamp.isoformat()
    }, room=data['room'])

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True)
# ПАТЧ ДЛЯ GEVENT - ДОЛЖЕН БЫТЬ ПЕРВОЙ СТРОКОЙ
from gevent import monkey
monkey.patch_all()

import os
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, flash, session, jsonify, render_template_string, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room
from sqlalchemy import text 

# ==========================================
# КОНФИГУРАЦИЯ ПРИЛОЖЕНИЯ
# ==========================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-key-for-youme-12345')

db_url = os.environ.get(
    'DATABASE_URL', 
    "postgresql://avnadmin:AVNS_A094KJpWYOSX9t3_eM6@youme-krossmag.l.aivencloud.com:25520/defaultdb?sslmode=require"
)
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,             
    'pool_recycle': 280,         
    'pool_pre_ping': True,       
    'pool_timeout': 20,          
}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins="*")

# ==========================================
# ВСПОМОГАТЕЛЬНАЯ ЛОГИКА И ПРАВА (SUDO)
# ==========================================
def now_msk():
    return datetime.utcnow() + timedelta(hours=3)

def format_bday(bd_str):
    if not bd_str or "." not in bd_str:
        return "Не указана"
    try:
        d, m, y = bd_str.split(".")
        months = {1: "янв.", 2: "февр.", 3: "мар.", 4: "апр.", 5: "мая", 6: "июня", 7: "июля", 8: "авг.", 9: "сент.", 10: "окт.", 11: "нояб.", 12: "дек."}
        m_str = months.get(int(m), m)
        return f"{d} {m_str} {y}г."
    except:
        return bd_str

def has_admin_priv():
    return current_user.is_admin or 'original_admin_id' in session

def can_see_deleted():
    return has_admin_priv() or current_user.perm_deleted_messages

def can_see_edits():
    return has_admin_priv() or current_user.is_moderator or current_user.perm_edit_history

def can_see_chatting():
    return has_admin_priv() or current_user.perm_see_chatting_with

def can_ban_users():
    return has_admin_priv() or current_user.perm_ban_users

def check_user_banned(u):
    if not u or not u.banned_until:
        return False, None, False
    if u.banned_until > now_msk():
        is_perm = u.banned_until.year >= 9999
        return True, u.banned_until, is_perm
    u.banned_until = None
    db.session.commit()
    return False, None, False

# ==========================================
# МОДЕЛИ БАЗЫ ДАННЫХ
# ==========================================
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=True) 
    class_name = db.Column(db.String(20), nullable=True) # Оставлено для совместимости БД, но в UI убрано

    avatar_url = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    about_me = db.Column(db.Text, nullable=True)
    birth_date = db.Column(db.String(20), nullable=True)
    last_seen = db.Column(db.DateTime, default=now_msk)

    show_phone = db.Column(db.Boolean, default=False)
    show_about = db.Column(db.Boolean, default=True)
    show_birth_date = db.Column(db.Boolean, default=False)

    # SUDO Права
    is_admin = db.Column(db.Boolean, default=False)
    is_moderator = db.Column(db.Boolean, default=False)
    perm_edit_history = db.Column(db.Boolean, default=False)
    perm_deleted_messages = db.Column(db.Boolean, default=False)
    perm_see_chatting_with = db.Column(db.Boolean, default=False)
    perm_ban_users = db.Column(db.Boolean, default=False)

    banned_until = db.Column(db.DateTime, nullable=True)

    promoted_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=now_msk)

class Contact(db.Model):
    __tablename__ = 'contacts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=now_msk)

class Chat(db.Model):
    __tablename__ = 'chats'
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), default='private')
    created_at = db.Column(db.DateTime, default=now_msk)

class ChatParticipant(db.Model):
    __tablename__ = 'chat_participants'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    text = db.Column(db.Text, nullable=True)
    image_base64 = db.Column(db.Text, nullable=True)
    voice_base64 = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=now_msk)
    is_read = db.Column(db.Boolean, default=False)
    
    is_deleted = db.Column(db.Boolean, default=False)
    is_edited = db.Column(db.Boolean, default=False)
    original_text = db.Column(db.Text, nullable=True)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True)
    forwarded_from_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Глобальные словари состояний
connected_users = {}
active_chat_views = {} # user_id -> partner_id с которым открыт чат

# ==========================================
# HTML ШАБЛОНЫ (Jinja2 + Tailwind + Alpine)
# ==========================================
BASE_HTML_HEAD = """
<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>You`me</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = { darkMode: 'class' }
    </script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #4B5563; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #374151; }

        .admin-badge {
            background-color: #3f2224; border: 1px solid #cc3033; color: #f76d70;
            padding: 0.1rem 0.4rem; border-radius: 0.375rem; font-size: 0.7rem; font-weight: 600; display: inline-block; line-height: 1;
        }
        .mod-badge {
            background-color: #1a3f20; border: 1px solid #28a745; color: #4ade80;
            padding: 0.1rem 0.4rem; border-radius: 0.375rem; font-size: 0.7rem; font-weight: 600; display: inline-block; line-height: 1;
        }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 h-[100dvh] max-h-[100dvh] w-screen overflow-hidden flex flex-col font-sans fixed inset-0 select-none">
    {% if session.get('original_admin_id') %}
    <div class="bg-red-600 text-white text-center py-2 text-xs md:text-sm font-bold flex justify-center items-center gap-2 md:gap-4 z-50 shadow-lg px-2 flex-shrink-0">
        Внимание: Режим от лица {{ current_user.first_name }}!
        <a href="{{ url_for('revert_impersonate') }}" class="bg-white text-red-600 px-2 py-1 rounded-md hover:bg-gray-200 transition">Вернуться</a>
    </div>
    {% endif %}
"""

BANNED_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex items-center justify-center bg-gray-900 px-4">
        <div class="bg-gray-800 border border-red-950 p-8 rounded-2xl shadow-2xl max-w-md w-full flex flex-col items-center text-center">
            
            <div class="w-20 h-20 rounded-full border-4 border-red-600 bg-black flex items-center justify-center shadow-lg mb-6">
                <span class="text-white font-black text-4xl leading-none">!</span>
            </div>

            <h2 class="text-2xl font-bold text-red-500 mb-4">Вход ограничен</h2>

            <p class="text-gray-200 text-base md:text-lg mb-6 font-medium leading-relaxed">
                {% if is_permanent %}
                    Вы были заблокированы навсегда
                {% else %}
                    Вы были заблокированы до<br>
                    <span class="font-mono font-bold text-red-400 text-lg block mt-2">{{ ban_date_str }}</span>
                {% endif %}
            </p>

            <div class="w-full border-t border-gray-700/80 pt-4 mt-2">
                <span class="text-xs text-gray-400 tracking-wider">Администрация You`Me</span>
            </div>

            <div class="mt-6">
                <a href="{{ url_for('logout') }}" class="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-4 py-2 rounded-full transition">Выйти из аккаунта</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

LOGIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex items-center justify-center bg-gray-900 px-4" x-data="{ isLogin: true }">
        <div class="bg-gray-800 p-6 md:p-8 rounded-xl shadow-2xl w-full max-w-md border border-gray-700">
            <h1 class="text-3xl font-bold text-center text-blue-500 mb-6 font-serif tracking-widest">You`me</h1>

            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="bg-red-500/20 border border-red-500 text-red-200 p-3 rounded mb-4 text-center text-sm">
                  {% for message in messages %}{{ message }}<br>{% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <form x-show="isLogin" action="{{ url_for('login') }}" method="POST" class="space-y-4">
                <input type="hidden" name="action" value="login">
                <div>
                    <input type="text" name="username" placeholder="Логин (@username)" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div>
                    <input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 md:py-2 rounded transition">Войти</button>
                <p class="text-center text-sm text-gray-400 mt-4">Нет аккаунта? <a href="#" @click.prevent="isLogin = false" class="text-blue-400 hover:underline">Регистрация</a></p>
            </form>

            <form x-show="!isLogin" action="{{ url_for('login') }}" method="POST" class="space-y-4" style="display: none;">
                <input type="hidden" name="action" value="register">
                <div>
                    <input type="text" name="username" placeholder="Придумайте логин (только латиница)" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div>
                    <input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div class="flex flex-col gap-4 md:gap-2">
                    <input type="text" name="first_name" placeholder="Имя" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                    <input type="text" name="last_name" placeholder="Фамилия (необязательно)" class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <button type="submit" class="w-full bg-green-600 hover:bg-green-700 text-white font-bold py-3 md:py-2 rounded transition">Зарегистрироваться</button>
                <p class="text-center text-sm text-gray-400 mt-4">Уже есть аккаунт? <a href="#" @click.prevent="isLogin = true" class="text-blue-400 hover:underline">Войти</a></p>
            </form>
        </div>
    </div>
</body>
</html>
"""

APP_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex overflow-hidden w-full h-full max-h-full" x-data="messengerApp()">

        <div class="bg-gray-900 border-r border-gray-800 flex-col flex-shrink-0 w-full md:w-80 h-full max-h-full"
             :class="currentChat ? 'hidden md:flex' : 'flex'">
             
            <div class="p-4 border-b border-gray-800 flex justify-between items-center flex-shrink-0 relative">
                <div class="flex items-center gap-3">
                    <div @click="openMyProfile()" class="w-10 h-10 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold cursor-pointer overflow-hidden shadow-md hover:ring-2 hover:ring-blue-400 transition">
                        <img x-show="myProfileData.avatar" :src="myProfileData.avatar" class="w-full h-full object-cover">
                        <span x-show="!myProfileData.avatar">{{ current_user.first_name[0] }}</span>
                    </div>
                    <img src="/logo.png" alt="You'Me" class="h-10 md:h-12 object-contain" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                    <div class="text-xl font-bold text-blue-500 tracking-wider" style="display:none;">You`me</div>
                </div>

                <div class="flex gap-2">
                    {% if current_user.is_admin or current_user.is_moderator or current_user.perm_ban_users or session.get('original_admin_id') %}
                    <a href="{{ url_for('admin_panel') }}" class="p-1 text-gray-400 hover:text-white" title="Панель Управления">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.427.738-3.2 2.23-2.47z"></path></svg>
                    </a>
                    {% endif %}
                    <a href="{{ url_for('logout') }}" class="p-1 text-gray-400 hover:text-red-500" title="Выйти">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                    </a>
                </div>
            </div>

            <div class="p-3 flex-shrink-0">
                <input type="text" autocomplete="new-password" spellcheck="false" x-model="searchQuery" @input.debounce.300ms="searchUsers()" placeholder="Поиск (@username или имя)..." class="w-full bg-gray-800 text-sm text-gray-200 rounded-full px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>

            <div class="flex-1 overflow-y-auto max-h-full">
                <template x-if="searchQuery.length > 0">
                    <div>
                        <div class="px-4 py-2 text-xs font-semibold text-gray-500 uppercase">Результаты</div>
                        <template x-for="user in searchResults" :key="user.id">
                            <div @click="startChat(user.id)" class="flex items-center gap-3 px-4 py-3 hover:bg-gray-800 cursor-pointer transition">
                                <div class="w-10 h-10 rounded-full bg-gradient-to-tr from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold overflow-hidden flex-shrink-0">
                                    <img x-show="user.avatar" :src="user.avatar" class="w-full h-full object-cover">
                                    <span x-show="!user.avatar" x-text="user.first_name[0]"></span>
                                </div>
                                <div class="flex-1 min-w-0">
                                    <div class="flex items-center gap-2">
                                        <div class="text-sm font-semibold truncate" x-text="user.first_name + ' ' + (user.last_name || '')"></div>
                                        <template x-if="user.is_admin"><span class="admin-badge">Admin</span></template>
                                        <template x-if="user.is_moderator"><span class="mod-badge">Moderator</span></template>
                                    </div>
                                    <div class="text-xs text-gray-400 truncate" x-text="'@' + user.username"></div>
                                </div>
                            </div>
                        </template>
                        <div x-show="searchResults.length === 0" class="px-4 text-sm text-gray-500">Поиск от 3-х символов...</div>
                    </div>
                </template>

                <template x-if="searchQuery.length === 0">
                    <div>
                        <template x-for="chat in chats" :key="chat.chat_id">
                            <div @click="openChat(chat)" class="flex items-center gap-3 px-4 py-3 hover:bg-gray-800 cursor-pointer transition" :class="currentChat && currentChat.chat_id === chat.chat_id ? 'bg-gray-800' : ''">
                                <div class="relative w-12 h-12 flex-shrink-0">
                                    <div class="w-full h-full rounded-full bg-gray-700 flex items-center justify-center text-white font-bold text-lg shadow-inner overflow-hidden">
                                        <img x-show="chat.partner_avatar" :src="chat.partner_avatar" class="w-full h-full object-cover">
                                        <span x-show="!chat.partner_avatar" x-text="chat.partner_name[0]"></span>
                                    </div>
                                    <div x-show="chat.is_online && !chat.partner_is_banned" class="absolute bottom-0 right-0 w-3.5 h-3.5 bg-blue-500 border-2 border-gray-900 rounded-full z-10"></div>
                                </div>

                                <div class="flex-1 min-w-0">
                                    <div class="flex justify-between items-center mb-1">
                                        <div class="text-sm font-semibold truncate flex items-center gap-2 pr-2">
                                            <span class="truncate" :class="chat.partner_is_banned ? 'line-through text-red-500' : ''" x-text="chat.partner_name"></span>
                                            <template x-if="chat.partner_is_admin"><span class="admin-badge flex-shrink-0">Admin</span></template>
                                            <template x-if="chat.partner_is_moderator"><span class="mod-badge flex-shrink-0">Moderator</span></template>
                                        </div>
                                        <div class="text-[10px] text-gray-500 whitespace-nowrap flex-shrink-0" x-text="chat.last_time"></div>
                                    </div>
                                    <div class="text-xs text-gray-400 truncate" :class="chat.custom_status ? 'text-blue-300 italic' : ''" x-text="chat.custom_status ? chat.custom_status : (chat.last_message || 'Нет сообщений')"></div>
                                </div>
                            </div>
                        </template>
                    </div>
                </template>
            </div>
        </div>

        <div class="flex-1 flex-col relative bg-[#0f172a] bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] h-full w-full max-h-full overflow-hidden" 
             style="background-blend-mode: overlay;"
             :class="currentChat ? 'flex' : 'hidden md:flex'">

            <template x-if="!currentChat">
                <div class="flex-1 flex items-center justify-center text-gray-500">
                    <div class="bg-gray-900/60 px-4 py-2 rounded-full backdrop-blur-sm text-sm md:text-base">Выберите чат для начала общения</div>
                </div>
            </template>

            <template x-if="currentChat">
                <div class="flex-1 flex flex-col h-full w-full max-h-full overflow-hidden">
                    
                    <div class="h-16 px-3 md:px-6 bg-gray-900/95 backdrop-blur-md border-b border-gray-800 flex items-center justify-between shadow-sm z-10 flex-shrink-0">
                        <div class="flex items-center gap-2 md:gap-4 min-w-0">
                            <button @click="closeChat()" class="md:hidden p-2 -ml-2 text-gray-400 hover:text-white transition flex-shrink-0">
                                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                            </button>
                            
                            <div class="flex items-center gap-3 cursor-pointer min-w-0" @click="openUserProfile(currentChat.partner_id)">
                                <div class="w-10 h-10 rounded-full overflow-hidden bg-gray-700 flex items-center justify-center text-white flex-shrink-0">
                                     <img x-show="currentChat.partner_avatar" :src="currentChat.partner_avatar" class="w-full h-full object-cover">
                                     <span x-show="!currentChat.partner_avatar" x-text="currentChat.partner_name[0]"></span>
                                </div>
                                <div class="flex flex-col min-w-0">
                                    <div class="flex items-center gap-2 min-w-0">
                                        <div class="text-white font-semibold text-sm md:text-base truncate flex items-center">
                                            <span x-text="currentChat.partner_name"></span>
                                            <template x-if="currentChat.partner_is_banned">
                                                <span class="text-red-700 font-bold ml-1 flex-shrink-0"> — Пользователь Заблокирован</span>
                                            </template>
                                        </div>
                                        <template x-if="currentChat.partner_is_admin"><span class="admin-badge hidden md:inline-block">Admin</span></template>
                                        <template x-if="currentChat.partner_is_moderator"><span class="mod-badge hidden md:inline-block">Moderator</span></template>
                                    </div>
                                    <div class="text-[11px] md:text-xs flex items-center gap-1 truncate">
                                        <span :class="currentChat.partner_is_banned ? 'text-red-700 font-semibold' : (typing[currentChat.chat_id] ? 'text-blue-400 italic animate-pulse' : (currentChat.custom_status ? 'text-blue-300 font-semibold' : (currentChat.is_online ? 'text-blue-400' : 'text-gray-400')))" 
                                              x-text="currentChat.partner_is_banned ? 'заблокирован' : (typing[currentChat.chat_id] ? 'печатает...' : (currentChat.custom_status ? currentChat.custom_status : (currentChat.is_online ? 'в сети' : 'был(а) ' + (currentChat.last_seen || 'недавно'))))"></span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="flex-1 overflow-y-auto p-4 md:p-6 space-y-4 max-h-full" id="messagesBox">
                        <template x-for="msg in messages" :key="msg.id">
                            <div class="flex w-full" :class="msg.sender_id === myId ? 'justify-end' : 'justify-start'">
                                
                                <div class="max-w-[85%] md:max-w-[70%] rounded-2xl px-3 py-2 md:px-4 shadow-md relative group flex flex-col select-text"
                                     :class="msg.is_deleted ? 'bg-red-950/40 border border-red-900 text-red-200 rounded-sm' : (msg.sender_id === myId ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-gray-800 text-gray-100 rounded-tl-sm')"
                                     @contextmenu.prevent="openContextMenu($event, msg, false)"
                                     @touchstart="handleTouchStart($event, msg)"
                                     @touchend="handleTouchEnd()"
                                     @touchmove="handleTouchEnd()">

                                    <template x-if="msg.forwarded_from_id">
                                        <div @click.stop="startChat(msg.forwarded_from_id)" class="text-[11px] text-blue-300 font-medium mb-1 border-b border-blue-500/20 pb-0.5 cursor-pointer hover:underline truncate">
                                            Переслано от: <span class="font-bold text-white" x-text="msg.forwarded_from_name"></span>
                                        </div>
                                    </template>

                                    <template x-if="msg.reply_to_id">
                                        <div class="bg-black/20 rounded-md px-2 py-1 mb-1 border-l-2 border-blue-400 text-[11px] text-gray-300 opacity-90 truncate">
                                            <span class="text-blue-400 font-bold block text-[9px] uppercase tracking-wide">Отвечено на:</span>
                                            <span x-text="msg.reply_text"></span>
                                        </div>
                                    </template>

                                    <template x-if="msg.image_base64">
                                        <img :src="msg.image_base64" class="rounded-lg mb-2 max-w-full h-auto cursor-pointer">
                                    </template>

                                    <template x-if="msg.voice_base64">
                                        <div class="my-1">
                                            <audio controls :src="msg.voice_base64" class="max-w-[210px] md:max-w-[280px] h-9 outline-none"></audio>
                                        </div>
                                    </template>

                                    <template x-if="msg.text">
                                        <div class="text-[14px] md:text-[15px] leading-relaxed break-words" x-text="msg.text"></div>
                                    </template>

                                    <div class="text-[10px] text-right mt-1 flex items-center justify-end gap-1 opacity-70" :class="msg.is_deleted ? 'text-red-400' : (msg.sender_id === myId ? 'text-blue-200' : 'text-gray-400')">
                                        <template x-if="msg.is_edited && !msg.is_deleted">
                                            <span class="text-[9px] italic mr-1 text-gray-300">(изменено)</span>
                                        </template>
                                        <template x-if="msg.is_deleted">
                                            <span class="text-[9px] font-bold text-red-400 mr-1">(удалено)</span>
                                        </template>
                                        <span x-text="msg.time"></span>
                                        <template x-if="msg.sender_id === myId && !msg.is_deleted">
                                            <span class="font-bold text-[11px]" :class="msg.is_read ? 'text-[#4da3ff]' : 'text-blue-200'" x-text="msg.is_read ? '✓✓' : '✓'"></span>
                                        </template>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>

                    <div class="bg-gray-900 border-t border-gray-800 w-full flex-shrink-0 pb-safe">
                        
                        <div x-show="replyToMessage" style="display:none;" class="bg-gray-800/80 p-2 px-4 flex justify-between items-center text-xs text-gray-300 border-b border-gray-700/50">
                            <div class="truncate flex items-center gap-1">
                                <span class="text-blue-400 font-bold uppercase text-[10px]">Ответить на:</span>
                                <span class="italic truncate max-w-xs" x-text="replyToMessage ? (replyToMessage.voice_base64 ? '[Голосовое]' : (replyToMessage.text || '[Фото]')) : ''"></span>
                            </div>
                            <button @click="replyToMessage = null" class="text-gray-400 hover:text-white font-bold text-sm px-1">✕</button>
                        </div>

                        <div x-show="editMessage" style="display:none;" class="bg-gray-800/80 p-2 px-4 flex justify-between items-center text-xs text-gray-300 border-b border-gray-700/50">
                            <div class="truncate flex items-center gap-1">
                                <span class="text-yellow-500 font-bold uppercase text-[10px]">Редактирование:</span>
                                <span class="italic truncate max-w-xs" x-text="editMessage ? editMessage.text : ''"></span>
                            </div>
                            <button @click="cancelEdit()" class="text-gray-400 hover:text-white font-bold text-sm px-1">✕</button>
                        </div>

                        <div x-show="imagePreview" style="display:none;" class="p-2 bg-gray-800/50 border-b border-gray-700/50">
                            <div class="relative inline-block">
                                <img :src="imagePreview" class="h-16 rounded-lg border border-gray-600 shadow-md">
                                <button @click="imagePreview = null" class="absolute -top-2 -right-2 bg-red-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs font-bold shadow">✕</button>
                            </div>
                        </div>

                        <div class="p-2 md:p-4 flex items-center gap-2 md:gap-3 w-full max-w-4xl mx-auto">
                            
                            <template x-if="currentChat.partner_is_banned">
                                <div class="flex-1 bg-gray-800/60 text-red-700 font-bold text-sm md:text-base rounded-full px-4 py-2 md:py-3 flex items-center border border-red-900/30 select-none">
                                    Пользователь Заблокирован
                                </div>
                            </template>
                            <template x-if="currentChat.partner_is_banned">
                                <button disabled class="flex-shrink-0 bg-blue-600/20 text-red-700 font-black rounded-full w-10 h-10 md:w-12 md:h-12 flex items-center justify-center cursor-not-allowed border border-red-900/30">
                                    <span class="text-xl">!</span>
                                </button>
                            </template>

                            <template x-if="!currentChat.partner_is_banned && !isRecording">
                                <label class="cursor-pointer p-2 text-gray-400 hover:text-blue-500 transition flex-shrink-0">
                                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
                                    <input type="file" class="hidden" accept="image/*" @change="handleImageSelect">
                                </label>
                            </template>

                            <template x-if="!currentChat.partner_is_banned && !isRecording">
                                <input type="text" x-model="newMessage" @keydown.enter="sendMessage()" @input="sendTyping()" placeholder="Сообщение..." class="flex-1 min-w-0 bg-gray-800 text-sm md:text-base text-white rounded-full px-4 py-2 md:py-3 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner">
                            </template>

                            <template x-if="!currentChat.partner_is_banned && isRecording">
                                <div class="flex-1 bg-red-950/60 border border-red-800 text-red-200 rounded-full px-4 py-2 md:py-3 flex items-center justify-center gap-2 font-mono font-bold animate-pulse">
                                    <div class="w-3 h-3 rounded-full bg-red-500"></div>
                                    <span x-text="formatTimer(recordTimer)"></span>
                                </div>
                            </template>

                            <template x-if="!currentChat.partner_is_banned && !isRecording">
                                <button @click="sendMessage()" class="flex-shrink-0 bg-blue-600 hover:bg-blue-500 text-white rounded-full w-10 h-10 md:w-12 md:h-12 flex items-center justify-center transition shadow-lg" :disabled="!newMessage.trim() && !imagePreview">
                                    <svg class="w-4 h-4 md:w-5 md:h-5 ml-1 transform -rotate-45" fill="currentColor" viewBox="0 0 20 20"><path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z"></path></svg>
                                </button>
                            </template>

                            <template x-if="!currentChat.partner_is_banned">
                                <button type="button" @click="toggleVoiceRecord()" :class="isRecording ? 'bg-red-600 text-white animate-pulse' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'" class="flex-shrink-0 rounded-full w-10 h-10 md:w-12 md:h-12 flex items-center justify-center transition shadow-lg">
                                    <svg class="w-4 h-4 md:w-5 md:h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clip-rule="evenodd"></path></svg>
                                </button>
                            </template>

                        </div>
                    </div>
                </div>
            </template>
        </div>

        <div x-show="contextMenu.show" 
             @click.away="contextMenu.show = false"
             class="fixed bg-gray-800 border border-gray-700 text-white rounded-xl shadow-2xl w-44 py-1.5 z-50 text-xs md:text-sm font-medium"
             :style="`left: ${contextMenu.x}px; top: ${contextMenu.y}px;`"
             style="display: none;">
             
             <button @click="actionReply()" class="w-full text-left px-4 py-2 hover:bg-gray-700 flex items-center gap-2 text-gray-200">
                 <span>Ответить</span>
             </button>
             
             <template x-if="contextMenu.msg && contextMenu.msg.sender_id === myId && !contextMenu.msg.is_deleted && !contextMenu.msg.voice_base64">
                 <button @click="actionEdit()" class="w-full text-left px-4 py-2 hover:bg-gray-700 flex items-center gap-2 text-gray-200">
                     <span>Изменить</span>
                 </button>
             </template>
             
             <button @click="actionForward()" class="w-full text-left px-4 py-2 hover:bg-gray-700 flex items-center gap-2 text-gray-200">
                 <span>Переслать</span>
             </button>
             
             <template x-if="contextMenu.msg && (contextMenu.msg.sender_id === myId || myProfileData.can_see_deleted) && !contextMenu.msg.is_deleted">
                 <button @click="actionDelete()" class="w-full text-left px-4 py-2 hover:bg-red-950/40 text-red-400 flex items-center gap-2">
                     <span>Удалить</span>
                 </button>
             </template>

             <template x-if="myProfileData.can_see_edits && contextMenu.msg && contextMenu.msg.is_edited">
                 <button @click="actionShowHistory()" class="w-full text-left px-4 py-2 hover:bg-yellow-950/40 text-yellow-400 border-t border-gray-700 mt-1 flex items-center gap-2">
                     <span>История изменений</span>
                 </button>
             </template>
        </div>

        <div x-show="forwardModal" style="display: none;" 
             class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-0 md:p-4"
             @click.self="forwardModal = false">
             <div class="bg-[#1e293b] w-full h-full md:h-auto md:max-w-md md:rounded-xl shadow-2xl flex flex-col overflow-hidden border border-gray-700">
                 <div class="p-4 bg-gray-900 border-b border-gray-700 flex justify-between items-center flex-shrink-0">
                     <h3 class="font-bold text-white text-base">Переслать сообщение в...</h3>
                     <button @click="forwardModal = false" class="text-gray-400 hover:text-white font-bold text-xl px-2">✕</button>
                 </div>
                 <div class="flex-1 overflow-y-auto max-h-full p-2 space-y-1">
                     <template x-for="chat in chats" :key="chat.chat_id">
                         <div @click="executeForward(chat.chat_id)" class="flex items-center gap-3 px-4 py-3 bg-gray-800/40 hover:bg-blue-600 rounded-lg cursor-pointer transition">
                             <div class="w-10 h-10 rounded-full bg-gray-700 overflow-hidden flex items-center justify-center font-bold text-white flex-shrink-0">
                                 <img x-show="chat.partner_avatar" :src="chat.partner_avatar" class="w-full h-full object-cover">
                                 <span x-show="!chat.partner_avatar" x-text="chat.partner_name[0]"></span>
                             </div>
                             <div class="text-sm font-semibold text-white truncate" x-text="chat.partner_name"></div>
                         </div>
                     </template>
                 </div>
             </div>
        </div>

        <div x-show="showHistoryModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" @click.self="showHistoryModal = false">
            <div class="bg-[#1e293b] p-5 rounded-xl border border-gray-700 max-w-sm w-full shadow-2xl">
                <h3 class="text-yellow-400 font-bold mb-3 text-sm md:text-base">Исходный текст сообщения</h3>
                <div class="text-gray-200 text-sm bg-gray-900 p-3 rounded border border-gray-800 whitespace-pre-wrap break-words max-h-60 overflow-y-auto select-text" x-text="historyText"></div>
                <div class="mt-4 flex justify-end">
                    <button @click="showHistoryModal = false" class="bg-gray-700 hover:bg-gray-600 text-white font-bold py-1.5 px-4 rounded-full text-xs transition">Закрыть</button>
                </div>
            </div>
        </div>

        <div x-show="showProfileModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" @click.self="closeProfileModal()">
             <div class="bg-[#242f3d] w-full max-w-sm rounded-lg shadow-2xl overflow-hidden flex flex-col relative text-gray-100">

                <div class="absolute top-4 right-4 flex gap-4 z-20">
                    <button x-show="isMyProfile" @click="editMode = true" class="text-white hover:text-blue-400 drop-shadow-md">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg>
                    </button>
                    <button @click="closeProfileModal()" class="text-white hover:text-red-400 drop-shadow-md">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </button>
                </div>

                <div x-show="!editMode" class="flex flex-col">
                    <div class="relative pb-6 bg-gradient-to-b from-[#1c242f] to-[#242f3d]">
                        <div class="w-24 h-24 md:w-32 md:h-32 mx-auto mt-8 rounded-full bg-blue-600 flex items-center justify-center text-4xl font-bold shadow-lg overflow-hidden border-2 border-transparent">
                            <img x-show="viewProfileData.avatar" :src="viewProfileData.avatar" class="w-full h-full object-cover">
                            <span x-show="!viewProfileData.avatar" x-text="viewProfileData.first_name ? viewProfileData.first_name[0] : ''"></span>
                        </div>
                        <div class="text-center mt-4 px-4">
                            <div class="text-lg md:text-xl font-bold flex items-center justify-center gap-2 flex-wrap">
                                <span x-text="viewProfileData.first_name + ' ' + (viewProfileData.last_name || '')"></span>
                                <template x-if="viewProfileData.is_admin"><span class="admin-badge">Admin</span></template>
                                <template x-if="viewProfileData.is_moderator"><span class="mod-badge">Moderator</span></template>
                            </div>
                             <div class="text-xs md:text-sm mt-1" :class="viewProfileData.is_online ? 'text-blue-400' : 'text-gray-400'" 
                                 x-text="viewProfileData.custom_status ? viewProfileData.custom_status : (viewProfileData.is_online ? 'в сети' : 'был(а) ' + (viewProfileData.last_seen || 'недавно'))"></div>
                        </div>
                    </div>

                    <div class="px-6 pb-6 space-y-4">
                        <template x-if="viewProfileData.phone">
                            <div class="border-b border-gray-700 pb-2">
                                <div class="text-[14px] md:text-[15px] font-medium select-text" x-text="viewProfileData.phone"></div>
                                <div class="text-[10px] md:text-xs text-gray-500">Телефон</div>
                            </div>
                        </template>

                        <template x-if="viewProfileData.about_me">
                             <div class="border-b border-gray-700 pb-2">
                                <div class="text-[14px] md:text-[15px] whitespace-pre-wrap select-text" x-text="viewProfileData.about_me"></div>
                                <div class="text-[10px] md:text-xs text-gray-500">О себе</div>
                             </div>
                        </template>

                        <div class="border-b border-gray-700 pb-2">
                            <div class="text-[14px] md:text-[15px] text-blue-400 select-text" x-text="'@' + viewProfileData.username"></div>
                            <div class="text-[10px] md:text-xs text-gray-500">Имя пользователя</div>
                        </div>

                        <template x-if="viewProfileData.formatted_bday">
                             <div class="border-b border-gray-700 pb-2">
                                <div class="text-[14px] md:text-[15px]" x-text="viewProfileData.formatted_bday"></div>
                                <div class="text-[10px] md:text-xs text-gray-500">День рождения</div>
                             </div>
                        </template>

                        <template x-if="!isMyProfile && !viewProfileData.phone && !viewProfileData.about_me && !viewProfileData.formatted_bday">
                            <div class="text-center text-gray-500 text-xs md:text-sm mt-4 italic">Дополнительная информация скрыта или не указана</div>
                        </template>
                    </div>
                </div>

                <div x-show="editMode" class="p-6 overflow-y-auto max-h-[80vh]">
                    <h3 class="text-base md:text-lg font-bold mb-4 text-blue-400">Редактирование профиля</h3>

                    <div class="flex flex-col items-center mb-4">
                        <div class="w-20 h-20 md:w-24 md:h-24 rounded-full bg-blue-600 mb-2 flex items-center justify-center text-3xl font-bold overflow-hidden relative group">
                            <img x-show="editProfileData.avatar" :src="editProfileData.avatar" class="w-full h-full object-cover">
                            <span x-show="!editProfileData.avatar" x-text="editProfileData.first_name[0]"></span>

                            <label class="absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center cursor-pointer transition">
                                <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 16V8a2 2 0 012-2h3l1-2h6l1 2h3a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 13a3 3 0 100-6 3 3 0 000 6z"></path></svg>
                                <input type="file" class="hidden" accept="image/*" @change="handleAvatarSelect">
                            </label>
                        </div>
                        <div class="text-[10px] md:text-xs text-gray-400">Нажмите для изменения фото</div>
                    </div>

                    <div class="space-y-4">
                         <div>
                            <label class="text-[10px] md:text-xs text-gray-400">Имя</label>
                            <input type="text" x-model="editProfileData.first_name" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-[10px] md:text-xs text-gray-400">Фамилия</label>
                            <input type="text" x-model="editProfileData.last_name" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-[10px] md:text-xs text-gray-400">Имя пользователя (никнейм)</label>
                            <input type="text" x-model="editProfileData.username" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-[10px] md:text-xs text-gray-400">День рождения</label>
                            <div class="flex gap-2">
                                <input type="number" x-model="editProfileData.birth_day" placeholder="День" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                                <input type="number" x-model="editProfileData.birth_month" placeholder="Мес" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                                <input type="number" x-model="editProfileData.birth_year" placeholder="Год" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                            </div>
                        </div>

                        <div>
                            <label class="text-[10px] md:text-xs text-gray-400">Телефон</label>
                            <input type="text" x-model="editProfileData.phone" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500">
                        </div>
                        <div>
                            <label class="text-[10px] md:text-xs text-gray-400">О себе</label>
                            <textarea x-model="editProfileData.about_me" rows="2" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500"></textarea>
                        </div>

                        <div class="mt-4 pt-4 border-t border-gray-700">
                            <h4 class="text-xs md:text-sm font-semibold mb-2 text-gray-300">Настройки приватности</h4>
                            <label class="flex items-center gap-2 mb-1">
                                <input type="checkbox" x-model="editProfileData.show_phone" class="rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500">
                                <span class="text-xs md:text-sm text-gray-300">Показывать Телефон</span>
                            </label>
                            <label class="flex items-center gap-2 mb-1">
                                <input type="checkbox" x-model="editProfileData.show_about" class="rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500">
                                <span class="text-xs md:text-sm text-gray-300">Показывать "О себе"</span>
                            </label>
                            <label class="flex items-center gap-2">
                                <input type="checkbox" x-model="editProfileData.show_birth_date" class="rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500">
                                <span class="text-xs md:text-sm text-gray-300">Показывать День рождения</span>
                            </label>
                        </div>

                        <div class="mt-4 pt-4 border-t border-gray-700">
                             <h4 class="text-xs md:text-sm font-semibold mb-2 text-gray-300">Смена пароля</h4>
                             <input type="password" x-model="editProfileData.new_password" placeholder="Новый пароль" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500">
                        </div>

                        <div class="flex gap-2 pt-4">
                            <button @click="saveProfile()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white py-2 rounded text-sm font-bold transition">Сохранить</button>
                            <button @click="editMode = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-2 rounded text-sm font-bold transition">Отмена</button>
                        </div>
                    </div>
                </div>

            </div>
        </div>

    </div>

    <script>
        function messengerApp() {
            return {
                socket: null,
                myId: {{ current_user.id }},
                chats: [],
                searchQuery: '',
                searchResults: [],
                currentChat: null,
                messages: [],
                newMessage: '',
                imagePreview: null,
                typing: {},

                isRecording: false,
                mediaRecorder: null,
                audioChunks: [],
                recordTimer: 0,
                recordInterval: null,

                contextMenu: { show: false, x: 0, y: 0, msg: null },
                longPressTimer: null,
                touchX: 0,
                touchY: 0,
                replyToMessage: null,
                editMessage: null,
                forwardModal: false,
                forwardMessageTarget: null,
                showHistoryModal: false,
                historyText: '',

                showProfileModal: false,
                isMyProfile: false,
                editMode: false,
                myProfileData: {}, 
                viewProfileData: {},
                editProfileData: {}, 

                init() {
                    this.fetchMyProfile();
                    this.socket = io();
                    this.socket.on('connect', () => {
                        this.loadChats();
                    });
                    this.socket.on('force_logout', () => {
                        window.location.href = '/logout';
                    });
                    this.socket.on('new_message', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                            if(data.sender_id !== this.myId) {
                                this.reloadCurrentMessages();
                            } else {
                                this.messages.push(data);
                                this.scrollToBottom();
                            }
                        }
                        this.loadChats();
                    });
                    this.socket.on('message_updated', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                            this.reloadCurrentMessages();
                        }
                    });
                    this.socket.on('messages_read', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                            this.messages.forEach(m => {
                                if (m.sender_id === this.myId) m.is_read = true;
                            });
                        }
                        this.loadChats();
                    });
                    this.socket.on('typing_status', (data) => {
                        this.typing[data.chat_id] = data.is_typing;
                        setTimeout(() => { this.typing[data.chat_id] = false }, 3000);
                    });
                    this.socket.on('status_update', (data) => {
                         let chat = this.chats.find(c => c.partner_id === data.user_id);
                         let customStatus = (this.myProfileData.perm_see_chatting_with && data.chatting_with_name) ? `общается с: ${data.chatting_with_name}` : null;
                         
                         if (chat) {
                             chat.is_online = data.status === 'online';
                             if(data.last_seen) chat.last_seen = data.last_seen;
                             chat.custom_status = customStatus;
                         }
                         if (this.currentChat && this.currentChat.partner_id === data.user_id) {
                             this.currentChat.is_online = data.status === 'online';
                             if(data.last_seen) this.currentChat.last_seen = data.last_seen;
                             this.currentChat.custom_status = customStatus;
                         }
                         if (this.viewProfileData.id === data.user_id) {
                             this.viewProfileData.is_online = data.status === 'online';
                             if(data.last_seen) this.viewProfileData.last_seen = data.last_seen;
                             this.viewProfileData.custom_status = customStatus;
                         }
                         this.loadChats();
                    });
                },

                closeChat() {
                    this.currentChat = null;
                    this.replyToMessage = null;
                    this.editMessage = null;
                    this.stopRecording();
                    this.socket.emit('close_chat');
                },

                openContextMenu(e, msg, isMobile) {
                    this.contextMenu.msg = msg;
                    if (isMobile) {
                        this.contextMenu.x = Math.min(this.touchX, window.innerWidth - 190);
                        this.contextMenu.y = Math.min(this.touchY, window.innerHeight - 200);
                    } else {
                        this.contextMenu.x = Math.min(e.clientX, window.innerWidth - 190);
                        this.contextMenu.y = Math.min(e.clientY, window.innerHeight - 200);
                    }
                    this.contextMenu.show = true;
                },
                handleTouchStart(e, msg) {
                    if (e.touches && e.touches[0]) {
                        this.touchX = e.touches[0].clientX;
                        this.touchY = e.touches[0].clientY;
                    }
                    this.longPressTimer = setTimeout(() => {
                        this.openContextMenu(null, msg, true);
                    }, 500);
                },
                handleTouchEnd() {
                    clearTimeout(this.longPressTimer);
                },

                actionReply() {
                    this.contextMenu.show = false;
                    this.editMessage = null;
                    this.replyToMessage = this.contextMenu.msg;
                },
                actionEdit() {
                    this.contextMenu.show = false;
                    this.replyToMessage = null;
                    this.editMessage = this.contextMenu.msg;
                    this.newMessage = this.contextMenu.msg.text;
                },
                cancelEdit() {
                    this.editMessage = null;
                    this.newMessage = '';
                },
                actionForward() {
                    this.contextMenu.show = false;
                    this.forwardMessageTarget = this.contextMenu.msg;
                    this.forwardModal = true;
                },
                executeForward(chatId) {
                    if (!this.forwardMessageTarget) return;
                    this.socket.emit('send_message', {
                        chat_id: chatId,
                        text: this.forwardMessageTarget.text,
                        image_base64: this.forwardMessageTarget.image_base64,
                        voice_base64: this.forwardMessageTarget.voice_base64,
                        forwarded_from_id: this.forwardMessageTarget.sender_id
                    });
                    this.forwardModal = false;
                    this.forwardMessageTarget = null;
                    this.loadChats();
                },
                actionDelete() {
                    this.contextMenu.show = false;
                    if(confirm("Удалить это сообщение?")) {
                        this.socket.emit('delete_message', { message_id: this.contextMenu.msg.id });
                    }
                },
                actionShowHistory() {
                    this.contextMenu.show = false;
                    this.historyText = this.contextMenu.msg.original_text || 'История изменений отсутствует.';
                    this.showHistoryModal = true;
                },

                async fetchMyProfile() {
                    const res = await fetch('/api/profile/me');
                    this.myProfileData = await res.json();
                },
                openMyProfile() {
                    this.isMyProfile = true;
                    this.editMode = false;
                    this.viewProfileData = { ...this.myProfileData };
                    this.editProfileData = { ...this.myProfileData, new_password: '' };
                    this.showProfileModal = true;
                },
                async openUserProfile(userId) {
                    if(userId === this.myId) { return this.openMyProfile(); }
                    this.isMyProfile = false;
                    this.editMode = false;
                    const res = await fetch('/api/profile/' + userId);
                    this.viewProfileData = await res.json();
                    this.showProfileModal = true;
                },
                closeProfileModal() {
                    this.showProfileModal = false;
                    this.editMode = false;
                },
                handleAvatarSelect(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (e) => { this.editProfileData.avatar = e.target.result; };
                    reader.readAsDataURL(file);
                },
                async saveProfile() {
                    const res = await fetch('/api/profile/me', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(this.editProfileData)
                    });
                    if(res.ok) {
                        await this.fetchMyProfile();
                        this.viewProfileData = { ...this.myProfileData };
                        this.editMode = false;
                    }
                },

                async loadChats() {
                    const res = await fetch('/api/chats');
                    this.chats = await res.json();
                },
                async searchUsers() {
                    let q = this.searchQuery.trim();
                    if (!q) { this.searchResults = []; return; }
                    if (q.startsWith('@') && q.length < 4) { this.searchResults = []; return; }
                    if (!q.startsWith('@') && q.length < 3) { this.searchResults = []; return; }

                    const res = await fetch('/api/search_users?q=' + encodeURIComponent(q));
                    this.searchResults = await res.json();
                },
                async startChat(userId) {
                    const res = await fetch('/api/chat/start/' + userId, { method: 'POST' });
                    const chatData = await res.json();
                    this.searchQuery = '';
                    this.searchResults = [];
                    await this.loadChats();
                    const chat = this.chats.find(c => c.chat_id === chatData.chat_id);
                    if (chat) this.openChat(chat);
                },
                async openChat(chat) {
                    this.currentChat = chat;
                    this.replyToMessage = null;
                    this.editMessage = null;
                    this.stopRecording();
                    this.socket.emit('open_chat', { partner_id: chat.partner_id });
                    await this.reloadCurrentMessages();
                },
                async reloadCurrentMessages() {
                    if (!this.currentChat) return;
                    const res = await fetch('/api/chat/' + this.currentChat.chat_id + '/messages');
                    this.messages = await res.json();
                    this.scrollToBottom();
                },

                handleImageSelect(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (e) => { this.imagePreview = e.target.result; };
                    reader.readAsDataURL(file);
                },
                sendMessage() {
                    if (!this.newMessage.trim() && !this.imagePreview) return;
                    if (this.editMessage) {
                        this.socket.emit('edit_message', {
                            message_id: this.editMessage.id,
                            text: this.newMessage.trim()
                        });
                        this.editMessage = null;
                    } else {
                        const payload = {
                            chat_id: this.currentChat.chat_id,
                            text: this.newMessage.trim(),
                            image_base64: this.imagePreview,
                            reply_to_id: this.replyToMessage ? this.replyToMessage.id : null
                        };
                        this.socket.emit('send_message', payload);
                        this.replyToMessage = null;
                    }

                    this.newMessage = '';
                    this.imagePreview = null;
                },

                async toggleVoiceRecord() {
                    if (!this.isRecording) {
                        await this.startRecording();
                    } else {
                        await this.stopRecording();
                    }
                },
                async startRecording() {
                    try {
                        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                        this.mediaRecorder = new MediaRecorder(stream);
                        this.audioChunks = [];
                        this.recordTimer = 0;
                        this.isRecording = true;

                        this.recordInterval = setInterval(() => {
                            this.recordTimer++;
                        }, 1000);

                        this.mediaRecorder.ondataavailable = (e) => {
                            if (e.data.size > 0) {
                                this.audioChunks.push(e.data);
                            }
                        };

                        this.mediaRecorder.onstop = () => {
                            clearInterval(this.recordInterval);
                            this.isRecording = false;

                            const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                            const reader = new FileReader();
                            reader.readAsDataURL(audioBlob);
                            reader.onloadend = () => {
                                const base64Audio = reader.result;
                                const payload = {
                                    chat_id: this.currentChat.chat_id,
                                    voice_base64: base64Audio,
                                    reply_to_id: this.replyToMessage ? this.replyToMessage.id : null
                                };
                                this.socket.emit('send_message', payload);
                                this.replyToMessage = null;
                            };

                            stream.getTracks().forEach(track => track.stop());
                        };

                        this.mediaRecorder.start();
                    } catch (err) {
                        alert('Не удалось получить доступ к микрофону: ' + err.message);
                        this.isRecording = false;
                    }
                },
                stopRecording() {
                    if (this.mediaRecorder && this.isRecording) {
                        this.mediaRecorder.stop();
                    }
                },
                formatTimer(seconds) {
                    const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
                    const secs = (seconds % 60).toString().padStart(2, '0');
                    return `${mins}:${secs}`;
                },

                sendTyping() {
                    if (this.currentChat) {
                        this.socket.emit('typing', { chat_id: this.currentChat.chat_id });
                    }
                },
                scrollToBottom() {
                    setTimeout(() => {
                        const box = document.getElementById('messagesBox');
                        if (box) box.scrollTop = box.scrollHeight;
                    }, 100);
                }
            }
        }
    </script>
</body>
</html>
"""

ADMIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="container mx-auto p-4 md:p-6 pt-10" x-data="adminApp()">
        <div class="flex justify-between items-center mb-8">
            <h1 class="text-xl md:text-3xl font-bold text-white">Панель Управления</h1>
            <a href="{{ url_for('index') }}" class="text-blue-400 hover:text-blue-300 transition text-sm md:text-base">&larr; В мессенджер</a>
        </div>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="bg-blue-500/20 text-blue-300 p-3 rounded mb-4 text-sm border border-blue-500">
              {% for message in messages %}{{ message }}<br>{% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        <div class="bg-gray-800 rounded-xl shadow-xl border border-gray-700 overflow-x-auto">
            <table class="w-full text-left border-collapse min-w-[750px]">
                <thead>
                    <tr class="bg-gray-900 border-b border-gray-700 text-gray-400 uppercase text-[10px] md:text-xs">
                         <th class="p-3 md:p-4">ID</th>
                        <th class="p-3 md:p-4">Пользователь / Ник</th>
                        <th class="p-3 md:p-4">Статус</th>
                        <th class="p-3 md:p-4 text-right">Действия</th>
                    </tr>
                </thead>
                <tbody class="text-xs md:text-sm">
                    {% for u in users %}
                    <tr class="border-b border-gray-700 hover:bg-gray-750 transition">
                         <td class="p-3 md:p-4 text-gray-500">#{{ u.id }}</td>
                        <td class="p-3 md:p-4">
                            <div class="font-semibold text-white flex items-center gap-2">
                                 {{ u.first_name }} {{ u.last_name or '' }}
                                {% if u.is_admin %}<span class="admin-badge">Admin</span>{% endif %}
                                {% if u.is_moderator %}<span class="mod-badge">Moderator</span>{% endif %}
                                {% if u.banned_until %}<span class="bg-red-950 border border-red-700 text-red-400 px-1.5 py-0.5 rounded text-[10px] font-bold">Banned</span>{% endif %}
                            </div>
                            <div class="text-[10px] md:text-xs text-blue-400">@{{ u.username }}</div>
                        </td>
                        <td class="p-3 md:p-4 text-gray-500">
                            {% if u.id in connected %} <span class="text-blue-500 font-bold">В сети</span> 
                            {% else %} Был(а) {{ u.last_seen.strftime('%H:%M %d.%m') if u.last_seen else '-' }}
                             {% endif %}
                        </td>
                        <td class="p-3 md:p-4 text-right space-x-1 md:space-x-2">
                            <button @click="openHistory({{ u.id }}, '{{ u.first_name }} {{ u.last_name or '' }}')" class="inline-block bg-indigo-900/50 hover:bg-indigo-800 text-indigo-300 border border-indigo-700 px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition">Список общения</button>
                            
                            {% if can_ban_users %}
                                {% if u.id != current_user.id and not u.is_admin %}
                                    <button @click="openBanModal({{ u.id }}, '{{ u.first_name }} {{ u.last_name or '' }}', '{{ 'forever' if u.banned_until and u.banned_until.year >= 9999 else (u.banned_until.strftime('%Y-%m-%dT%H:%M') if u.banned_until else '') }}')" 
                                            class="inline-block bg-red-900/50 hover:bg-red-800 text-red-300 border border-red-700 px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition">
                                        {{ 'Разблокировать' if u.banned_until else 'Блокировка' }}
                                    </button>
                                {% endif %}
                            {% endif %}

                            {% if has_admin_priv %}
                                {% if u.id != current_user.id %}
                                    <button @click="openPerms({ id: {{ u.id }}, is_admin: {{ 'true' if u.is_admin else 'false' }}, is_moderator: {{ 'true' if u.is_moderator else 'false' }}, perm_edit_history: {{ 'true' if u.perm_edit_history else 'false' }}, perm_deleted_messages: {{ 'true' if u.perm_deleted_messages else 'false' }}, perm_see_chatting_with: {{ 'true' if u.perm_see_chatting_with else 'false' }}, perm_ban_users: {{ 'true' if u.perm_ban_users else 'false' }} })" class="inline-block bg-green-900/50 hover:bg-green-800 text-green-300 border border-green-700 px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition">Управление правами</button>
                                    
                                    {% if current_user.promoted_by_id != u.id %}
                                         <a href="{{ url_for('impersonate', target_id=u.id) }}" class="inline-block bg-blue-600 hover:bg-blue-500 text-white px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition shadow">Войти как</a>
                                    {% endif %}
                                {% else %}
                                    <span class="text-gray-600 text-[10px] md:text-xs italic">Это вы</span>
                                {% endif %}
                             {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                 </tbody>
            </table>
        </div>

        <div x-show="showPermsModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" @click.self="showPermsModal = false">
            <div class="bg-[#1e293b] p-6 rounded-xl border border-gray-700 w-full max-w-sm shadow-2xl">
                <h3 class="text-white font-bold text-lg mb-4 border-b border-gray-700 pb-2">Права пользователя</h3>
                
                <div class="space-y-3 mb-6">
                    <label class="flex items-center gap-3 cursor-pointer">
                        <input type="checkbox" x-model="permsUser.is_admin" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded">
                        <span class="text-sm font-medium text-red-400">sudo admin</span>
                    </label>
                    <label class="flex items-center gap-3 cursor-pointer">
                        <input type="checkbox" x-model="permsUser.is_moderator" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded">
                        <span class="text-sm font-medium text-green-400">sudo moderate</span>
                    </label>
                    <label class="flex items-center gap-3 cursor-pointer">
                        <input type="checkbox" x-model="permsUser.perm_ban_users" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded">
                        <span class="text-sm font-medium text-purple-400">sudo блокировка</span>
                    </label>
                    <label class="flex items-center gap-3 cursor-pointer">
                        <input type="checkbox" x-model="permsUser.perm_edit_history" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded">
                        <span class="text-sm font-medium text-gray-300">sudo история изменений</span>
                    </label>
                    <label class="flex items-center gap-3 cursor-pointer">
                        <input type="checkbox" x-model="permsUser.perm_deleted_messages" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded">
                        <span class="text-sm font-medium text-gray-300">sudo удаленные сообщения</span>
                    </label>
                    <label class="flex items-center gap-3 cursor-pointer">
                        <input type="checkbox" x-model="permsUser.perm_see_chatting_with" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded">
                        <span class="text-sm font-medium text-gray-300">sudo с кем общается</span>
                     </label>
                </div>

                <div class="flex gap-2">
                    <button @click="savePerms()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white font-bold py-2 rounded transition">Сохранить</button>
                    <button @click="showPermsModal = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white font-bold py-2 rounded transition">Отмена</button>
                </div>
            </div>
        </div>

        <div x-show="showBanModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" @click.self="showBanModal = false">
            <div class="bg-[#1e293b] p-6 rounded-xl border border-red-700 w-full max-w-sm shadow-2xl">
                <h3 class="text-red-400 font-bold text-lg mb-4 border-b border-gray-700 pb-2">Блокировка: <span class="text-white" x-text="banTargetName"></span></h3>
                
                <div class="space-y-4 mb-6">
                    <div>
                        <label class="flex items-center gap-2 cursor-pointer mb-2">
                            <input type="radio" name="bmode" value="forever" x-model="banMode" class="text-red-600 bg-gray-700 border-gray-600">
                            <span class="text-sm text-white font-semibold">Вечная блокировка</span>
                        </label>
                        <label class="flex items-center gap-2 cursor-pointer">
                            <input type="radio" name="bmode" value="temporary" x-model="banMode" class="text-red-600 bg-gray-700 border-gray-600">
                            <span class="text-sm text-white font-semibold">Свое время блокировки</span>
                        </label>
                    </div>

                    <div x-show="banMode === 'temporary'" class="pt-2">
                        <label class="text-xs text-gray-400 block mb-1">Разблокировать в (МСК):</label>
                        <input type="datetime-local" x-model="banCustomDate" class="w-full bg-gray-900 border border-gray-700 rounded p-2 text-white text-sm focus:ring-1 focus:ring-red-500">
                    </div>
                </div>

                <div class="flex flex-col gap-2">
                    <div class="flex gap-2">
                        <button @click="executeBan()" class="flex-1 bg-red-600 hover:bg-red-500 text-white font-bold py-2 rounded transition">Применить</button>
                        <button @click="showBanModal = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white font-bold py-2 rounded transition">Отмена</button>
                    </div>
                    <button @click="executeUnban()" class="w-full bg-green-700/60 hover:bg-green-700 text-green-200 font-bold py-1.5 rounded text-xs transition mt-2">Снять блокировку</button>
                </div>
            </div>
        </div>

        <div x-show="showHistoryModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" @click.self="showHistoryModal = false">
            <div class="bg-[#1e293b] p-6 rounded-xl border border-gray-700 w-full max-w-lg shadow-2xl flex flex-col max-h-[80vh]">
                <h3 class="text-white font-bold text-lg mb-4 border-b border-gray-700 pb-2">Общение за 24ч: <span class="text-blue-400" x-text="historyUserName"></span></h3>
                
                <div class="flex-1 overflow-y-auto mb-4">
                    <template x-if="historyData.length === 0">
                        <div class="text-gray-500 text-sm italic text-center py-4">Нет активности за последние сутки.</div>
                    </template>
                    <div class="space-y-2">
                         <template x-for="item in historyData" :key="item.username">
                            <div class="bg-gray-800 p-3 rounded border border-gray-700 flex justify-between items-center">
                                <div>
                                     <div class="text-sm font-bold text-white" x-text="item.name"></div>
                                    <div class="text-xs text-blue-400" x-text="'@' + item.username"></div>
                                </div>
                                <div class="text-xs font-mono text-gray-400 bg-gray-900 px-2 py-1 rounded" x-text="item.time_range"></div>
                            </div>
                        </template>
                     </div>
                </div>

                <button @click="showHistoryModal = false" class="w-full bg-gray-700 hover:bg-gray-600 text-white font-bold py-2 rounded transition">Закрыть</button>
            </div>
        </div>

    </div>

    <script>
        function adminApp() {
             return {
                showPermsModal: false,
                permsUser: {},
                showHistoryModal: false,
                historyUserName: '',
                historyData: [],

                showBanModal: false,
                banTargetId: null,
                banTargetName: '',
                banMode: 'temporary',
                banCustomDate: '',

                openPerms(userData) {
                    this.permsUser = userData;
                    this.showPermsModal = true;
                },
                async savePerms() {
                    await fetch('/api/admin/permissions/' + this.permsUser.id, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.permsUser)
                    });
                    location.reload();
                },
                async openHistory(userId, userName) {
                    this.historyUserName = userName;
                    const res = await fetch('/api/admin/history_24h/' + userId);
                    this.historyData = await res.json();
                    this.showHistoryModal = true;
                },

                openBanModal(id, name, currentBan) {
                    this.banTargetId = id;
                    this.banTargetName = name;
                    if (currentBan === 'forever') {
                        this.banMode = 'forever';
                    } else if (currentBan !== '') {
                        this.banMode = 'temporary';
                        this.banCustomDate = currentBan;
                    } else {
                        this.banMode = 'temporary';
                        let tomorrow = new Date(Date.now() + 86400000);
                        let tzoffset = tomorrow.getTimezoneOffset() * 60000;
                        this.banCustomDate = new Date(tomorrow - tzoffset).toISOString().slice(0, 16);
                    }
                    this.showBanModal = true;
                },
                async executeBan() {
                    await fetch('/api/admin/ban/' + this.banTargetId, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ action: 'ban', type: this.banMode, until: this.banCustomDate })
                    });
                    location.reload();
                },
                async executeUnban() {
                    await fetch('/api/admin/ban/' + this.banTargetId, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ action: 'unban' })
                    });
                    location.reload();
                }
            }
        }
    </script>
</body>
</html>
"""

# ==========================================
# МАРШРУТЫ (АВТОРИЗАЦИЯ И ПАНЕЛЬ)
# ==========================================
@app.route('/logo.png')
def serve_logo():
    return send_from_directory(os.getcwd(), 'logo.png')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated and not session.get('original_admin_id'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username')
        password = request.form.get('password')

        if action == 'register':
            if User.query.filter_by(username=username).first():
                flash('Пользователь с таким логином уже существует')
                return redirect(url_for('login'))

            new_user = User(
                username=username,
                password=password,
                first_name=request.form.get('first_name'),
                last_name=request.form.get('last_name') or None,
                last_seen=now_msk()
            )
            db.session.add(new_user)
            db.session.commit()
            
            login_user(new_user)
            return redirect(url_for('index'))

        elif action == 'login':
            clean_username = username.lstrip('@')
            user = User.query.filter_by(username=clean_username, password=password).first()
            if user:
                login_user(user)
                return redirect(url_for('index'))
 
            flash('Неверный логин или пароль')

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
@login_required
def logout():
    session.pop('original_admin_id', None)
    current_user.last_seen = now_msk()
    db.session.commit()
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    banned, until_dt, is_perm = check_user_banned(current_user)
    if banned:
        ban_str = until_dt.strftime('%dд. %mмес. %Yг. %H:%M:%S') if until_dt else ""
        return render_template_string(BANNED_TEMPLATE, is_permanent=is_perm, ban_date_str=ban_str)
    return render_template_string(APP_TEMPLATE)

# ==========================================
# API ДЛЯ ПРОФИЛЯ
# ==========================================
@app.route('/api/profile/me', methods=['GET', 'POST'])
@login_required
def my_profile():
    if request.method == 'POST':
        data = request.json
        current_user.first_name = data.get('first_name', current_user.first_name)
        current_user.last_name = data.get('last_name') or None

        new_username = data.get('username')
        if new_username:
            if not new_username.startswith('@'):
                new_username = '@' + new_username
            current_user.username = new_username

        b_day = data.get('birth_day')
        b_month = data.get('birth_month')
        b_year = data.get('birth_year')
        if b_day and b_month and b_year:
            current_user.birth_date = f"{b_day}.{b_month}.{b_year}"

        current_user.phone = data.get('phone')
        current_user.about_me = data.get('about_me')
        if data.get('avatar'): current_user.avatar_url = data.get('avatar')

        current_user.show_phone = data.get('show_phone', False)
        current_user.show_about = data.get('show_about', True)
        current_user.show_birth_date = data.get('show_birth_date', False)

        new_pwd = data.get('new_password')
        if new_pwd and new_pwd.strip() != "":
            current_user.password = new_pwd.strip()

        db.session.commit()
        return jsonify({'status': 'ok'})

    bd = current_user.birth_date
    b_day, b_month, b_year = "", "", ""
    if bd and "." in bd:
        parts = bd.split(".")
        if len(parts) == 3: b_day, b_month, b_year = parts

    return jsonify({
        'id': current_user.id,
        'first_name': current_user.first_name,
        'last_name': current_user.last_name,
        'username': current_user.username,
        'avatar': current_user.avatar_url,
        'phone': current_user.phone,
        'about_me': current_user.about_me,
        'birth_day': b_day,
        'birth_month': b_month,
        'birth_year': b_year,
        'formatted_bday': format_bday(current_user.birth_date),
        'show_phone': current_user.show_phone,
        'show_about': current_user.show_about,
        'show_birth_date': current_user.show_birth_date,
        'is_admin': current_user.is_admin,
        'is_moderator': current_user.is_moderator,
        'has_admin_priv': has_admin_priv(),
        'can_see_deleted': can_see_deleted(),
        'can_see_edits': can_see_edits(),
        'perm_see_chatting_with': can_see_chatting(),
        'can_ban_users': can_ban_users(),
        'is_online': True
    })

@app.route('/api/profile/<int:user_id>')
@login_required
def get_user_profile(user_id):
    user = User.query.get_or_404(user_id)
    last_seen_str = user.last_seen.strftime('%H:%M') if user.last_seen else ''

    # Трекинг с кем общается
    custom_status = None
    if can_see_chatting() and user.id in active_chat_views:
        p_id = active_chat_views[user.id]
        p = User.query.get(p_id)
        if p: custom_status = f"общается с: {p.first_name} {p.last_name or ''}"

    data = {
        'id': user.id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'username': user.username,
        'avatar': user.avatar_url,
        'is_admin': user.is_admin,
        'is_moderator': user.is_moderator,
        'is_online': user.id in connected_users,
        'last_seen': last_seen_str,
        'custom_status': custom_status,
        'phone': user.phone if user.show_phone else None,
        'about_me': user.about_me if user.show_about else None,
        'formatted_bday': format_bday(user.birth_date) if user.show_birth_date else None
    }
    return jsonify(data)

# ==========================================
# API ДЛЯ ЧАТОВ И ПОИСКА
# ==========================================
@app.route('/api/chats')
@login_required
def get_chats():
    participants = ChatParticipant.query.filter_by(user_id=current_user.id).all()
    chat_ids = [p.chat_id for p in participants]

    chats_data = []
    for cid in chat_ids:
        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == cid, ChatParticipant.user_id != current_user.id).first()
        if not partner_cp: continue

        partner = User.query.get(partner_cp.user_id)
        last_msg = Message.query.filter_by(chat_id=cid).order_by(Message.timestamp.desc()).first()

        custom_status = None
        if can_see_chatting() and partner.id in active_chat_views:
            p = User.query.get(active_chat_views[partner.id])
            if p: custom_status = f"общается с: {p.first_name} {p.last_name or ''}"

        partner_banned, _, _ = check_user_banned(partner)

        lmsg_preview = ''
        if last_msg:
            if last_msg.voice_base64: lmsg_preview = '[Голосовое]'
            elif last_msg.text: lmsg_preview = last_msg.text
            elif last_msg.image_base64: lmsg_preview = '[Фото]'

        chats_data.append({
            'chat_id': cid,
            'partner_id': partner.id,
            'partner_name': f"{partner.first_name} {partner.last_name or ''}",
            'partner_avatar': partner.avatar_url,
            'partner_is_admin': partner.is_admin,
            'partner_is_moderator': partner.is_moderator,
            'partner_is_banned': partner_banned,
            'custom_status': custom_status,
            'last_message': lmsg_preview,
            'last_time': last_msg.timestamp.strftime('%H:%M') if last_msg else '',
            'is_online': partner.id in connected_users,
            'last_seen': partner.last_seen.strftime('%H:%M') if partner.last_seen else ''
        })
    return jsonify(chats_data)

@app.route('/api/search_users')
@login_required
def search_users():
    q = request.args.get('q', '').strip()
    if not q: return jsonify([])

    if q.startswith('@'):
        q = q[1:]
        users = User.query.filter(User.id != current_user.id, User.username.ilike(f'%{q}%')).limit(10).all()
    else:
        users = User.query.filter(
            (User.id != current_user.id) &
            (User.first_name.ilike(f'%{q}%') | User.last_name.ilike(f'%{q}%'))
        ).limit(10).all()

    return jsonify([{
        'id': u.id,
        'first_name': u.first_name,
        'last_name': u.last_name,
        'username': u.username,
        'avatar': u.avatar_url,
        'is_admin': u.is_admin,
        'is_moderator': u.is_moderator
    } for u in users])

@app.route('/api/chat/start/<int:target_id>', methods=['POST'])
@login_required
def start_chat(target_id):
    my_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=current_user.id).all())
    target_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=target_id).all())
    common = my_chats.intersection(target_chats)

    if common:
        chat_id = list(common)[0]
    else:
        new_chat = Chat(type='private')
        db.session.add(new_chat)
        db.session.commit()
        chat_id = new_chat.id
        db.session.add_all([
            ChatParticipant(chat_id=chat_id, user_id=current_user.id),
            ChatParticipant(chat_id=chat_id, user_id=target_id),
            Contact(user_id=current_user.id, contact_id=target_id),
            Contact(user_id=target_id, contact_id=current_user.id)
        ])
        db.session.commit()
    return jsonify({'chat_id': chat_id})

@app.route('/api/chat/<int:chat_id>/messages')
@login_required
def get_messages(chat_id):
    unread_msgs = Message.query.filter(Message.chat_id == chat_id, Message.sender_id != current_user.id, Message.is_read == False).all()
    if unread_msgs:
        for msg in unread_msgs: msg.is_read = True
        db.session.commit()

        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == chat_id, ChatParticipant.user_id != current_user.id).first()
        if partner_cp:
            socketio.emit('messages_read', {'chat_id': chat_id}, room=f"user_{partner_cp.user_id}")

    if can_see_deleted():
        messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.timestamp.asc()).all()
    else:
        messages = Message.query.filter_by(chat_id=chat_id, is_deleted=False).order_by(Message.timestamp.asc()).all()

    result = []
    see_edits = can_see_edits()
    for m in messages:
        reply_text = ""
        if m.reply_to_id:
            rm = Message.query.get(m.reply_to_id)
            if rm:
                if rm.voice_base64: reply_text = "[Голосовое]"
                elif rm.text: reply_text = (rm.text[:25] + "...") if len(rm.text) > 25 else rm.text
                else: reply_text = "[Фото]"

        fwd_name = ""
        if m.forwarded_from_id:
            fu = User.query.get(m.forwarded_from_id)
            if fu: fwd_name = f"{fu.first_name} {fu.last_name or ''}"

        result.append({
            'id': m.id, 'sender_id': m.sender_id, 'text': m.text,
            'image_base64': m.image_base64, 'voice_base64': m.voice_base64,
            'time': m.timestamp.strftime('%H:%M'),
            'is_read': m.is_read, 'is_deleted': m.is_deleted, 'is_edited': m.is_edited,
            'original_text': m.original_text if see_edits else None,
            'reply_to_id': m.reply_to_id, 'reply_text': reply_text,
            'forwarded_from_id': m.forwarded_from_id, 'forwarded_from_name': fwd_name
        })
    return jsonify(result)

# ==========================================
# АДМИН ПАНЕЛЬ И SUDO-РОЛИ
# ==========================================
@app.route('/admin')
@login_required
def admin_panel():
    if not (has_admin_priv() or current_user.is_moderator or current_user.perm_ban_users):
        flash("Доступ запрещен")
        return redirect(url_for('index'))
    users = User.query.order_by(User.id.desc()).all()
    return render_template_string(ADMIN_TEMPLATE, users=users, connected=connected_users, has_admin_priv=has_admin_priv(), can_ban_users=can_ban_users())

@app.route('/api/admin/permissions/<int:target_id>', methods=['POST'])
@login_required
def update_permissions(target_id):
    if not has_admin_priv(): return "Forbidden", 403
    target = User.query.get_or_404(target_id)
    data = request.json
    
    target.is_admin = data.get('is_admin', False)
    target.is_moderator = data.get('is_moderator', False)
    target.perm_edit_history = data.get('perm_edit_history', False)
    target.perm_deleted_messages = data.get('perm_deleted_messages', False)
    target.perm_see_chatting_with = data.get('perm_see_chatting_with', False)
    target.perm_ban_users = data.get('perm_ban_users', False)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/admin/ban/<int:target_id>', methods=['POST'])
@login_required
def admin_ban_user_endpoint(target_id):
    if not can_ban_users(): return "Forbidden", 403
    target = User.query.get_or_404(target_id)
    if target.is_admin and not current_user.is_admin: return "Cannot ban admin", 403

    data = request.json
    action = data.get('action')

    if action == 'unban':
        target.banned_until = None
    elif action == 'forever':
        target.banned_until = datetime(9999, 12, 31, 23, 59, 59)
        socketio.emit('force_logout', {}, room=f"user_{target.id}")
    elif action == 'temporary':
        until_str = data.get('until')
        if until_str:
            try:
                dt = datetime.strptime(until_str.replace("T", " ")[:16], "%Y-%m-%d %H:%M")
                target.banned_until = dt
                socketio.emit('force_logout', {}, room=f"user_{target.id}")
            except Exception as e:
                return "Invalid date format", 400

    db.session.commit()
    broadcast_user_status(target.id)
    return jsonify({'status': 'ok'})

@app.route('/api/admin/history_24h/<int:target_id>')
@login_required
def admin_history_24h(target_id):
    if not (has_admin_priv() or current_user.is_moderator or can_ban_users()): return "Forbidden", 403
    
    yesterday = now_msk() - timedelta(days=1)
    participants = ChatParticipant.query.filter_by(user_id=target_id).all()
    
    result = []
    for p in participants:
        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == p.chat_id, ChatParticipant.user_id != target_id).first()
        if not partner_cp: continue
        
        partner = User.query.get(partner_cp.user_id)
        if not partner: continue
        
        msgs = Message.query.filter(Message.chat_id == p.chat_id, Message.timestamp >= yesterday).all()
        if msgs:
            msgs.sort(key=lambda x: x.timestamp)
            first_time = msgs[0].timestamp.strftime('%H:%M')
            last_time = msgs[-1].timestamp.strftime('%H:%M')
            
            result.append({
                'name': f"{partner.first_name} {partner.last_name or ''}",
                'username': partner.username,
                'time_range': f"{first_time} - {last_time}"
            })
    return jsonify(result)

@app.route('/admin/impersonate/<int:target_id>')
@login_required
def impersonate(target_id):
    if not has_admin_priv(): return "Access denied", 403
    if 'original_admin_id' not in session:
        session['original_admin_id'] = current_user.id
    target_user = User.query.get_or_404(target_id)
    login_user(target_user)
    return redirect(url_for('index'))

@app.route('/admin/revert')
@login_required
def revert_impersonate():
    admin_id = session.pop('original_admin_id', None)
    if admin_id:
        admin_user = User.query.get(admin_id)
        if admin_user: login_user(admin_user)
    return redirect(url_for('admin_panel'))

# ==========================================
# SOCKET.IO СЕРВЕРНАЯ ЛОГИКА
# ==========================================
def broadcast_user_status(user_id):
    status = 'online' if user_id in connected_users else 'offline'
    u = User.query.get(user_id)
    last_seen = u.last_seen.strftime('%H:%M') if u and u.last_seen else ''
    
    chatting_with_name = None
    if user_id in active_chat_views:
        partner = User.query.get(active_chat_views[user_id])
        if partner: chatting_with_name = f"{partner.first_name} {partner.last_name or ''}"

    emit('status_update', {
        'user_id': user_id, 'status': status, 'last_seen': last_seen,
        'chatting_with_name': chatting_with_name
    }, broadcast=True)

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        banned, _, _ = check_user_banned(current_user)
        if banned:
            return False # Жестко отсекаем коннект заблокированному

        join_room(f"user_{current_user.id}")
        connected_users[current_user.id] = request.sid
        current_user.last_seen = now_msk()
        db.session.commit()
        broadcast_user_status(current_user.id)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        if current_user.id in connected_users: del connected_users[current_user.id]
        if current_user.id in active_chat_views: del active_chat_views[current_user.id]
        u = User.query.get(current_user.id)
        if u:
            u.last_seen = now_msk()
            db.session.commit()
            broadcast_user_status(current_user.id)

@socketio.on('open_chat')
def handle_open_chat(data):
    if current_user.is_authenticated:
        active_chat_views[current_user.id] = data.get('partner_id')
        broadcast_user_status(current_user.id)

@socketio.on('close_chat')
def handle_close_chat():
    if current_user.is_authenticated and current_user.id in active_chat_views:
        del active_chat_views[current_user.id]
        broadcast_user_status(current_user.id)

@socketio.on('typing')
def handle_typing(data):
    chat_id = data.get('chat_id')
    partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == chat_id, ChatParticipant.user_id != current_user.id).first()
    if partner_cp: emit('typing_status', {'chat_id': chat_id, 'is_typing': True}, room=f"user_{partner_cp.user_id}")

@socketio.on('send_message')
def handle_message(data):
    chat_id = data.get('chat_id')
    reply_to_id = data.get('reply_to_id')
    forwarded_from_id = data.get('forwarded_from_id')

    msg = Message(
        chat_id=chat_id, sender_id=current_user.id, 
        text=data.get('text', ''), image_base64=data.get('image_base64'), 
        voice_base64=data.get('voice_base64'),
        reply_to_id=reply_to_id, forwarded_from_id=forwarded_from_id, is_read=False
    )
    db.session.add(msg)
    db.session.commit()

    reply_text = ""
    if reply_to_id:
        rm = Message.query.get(reply_to_id)
        if rm:
            if rm.voice_base64: reply_text = "[Голосовое]"
            elif rm.text: reply_text = (rm.text[:25] + "...") if len(rm.text) > 25 else rm.text
            else: reply_text = "[Фото]"

    fwd_name = ""
    if forwarded_from_id:
        fu = User.query.get(forwarded_from_id)
        if fu: fwd_name = f"{fu.first_name} {fu.last_name or ''}"

    msg_data = {
        'id': msg.id, 'chat_id': chat_id, 'sender_id': current_user.id,
        'text': msg.text, 'image_base64': msg.image_base64, 'voice_base64': msg.voice_base64,
        'time': msg.timestamp.strftime('%H:%M'), 'is_read': False,
        'is_deleted': False, 'is_edited': False, 'original_text': None,
        'reply_to_id': reply_to_id, 'reply_text': reply_text,
        'forwarded_from_id': forwarded_from_id, 'forwarded_from_name': fwd_name
    }
    for p in ChatParticipant.query.filter_by(chat_id=chat_id).all():
        emit('new_message', msg_data, room=f"user_{p.user_id}")

@socketio.on('edit_message')
def handle_edit_message(data):
    msg_id = data.get('message_id')
    new_text = data.get('text', '')
    msg = Message.query.get(msg_id)
    
    if msg and (msg.sender_id == current_user.id or has_admin_priv()) and not msg.is_deleted:
        if not msg.is_edited:
            msg.original_text = msg.text
            msg.is_edited = True
        msg.text = new_text
        db.session.commit()

        for p in ChatParticipant.query.filter_by(chat_id=msg.chat_id).all():
            emit('message_updated', {'chat_id': msg.chat_id}, room=f"user_{p.user_id}")

@socketio.on('delete_message')
def handle_delete_message(data):
    msg_id = data.get('message_id')
    msg = Message.query.get(msg_id)
    
    if msg and (msg.sender_id == current_user.id or has_admin_priv()):
        msg.is_deleted = True
        db.session.commit()

        for p in ChatParticipant.query.filter_by(chat_id=msg.chat_id).all():
            emit('message_updated', {'chat_id': msg.chat_id}, room=f"user_{p.user_id}")

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И ЗАПУСК
# ==========================================
def init_db():
    with app.app_context():
        db.create_all()

        try:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_edited BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS original_text TEXT;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS forwarded_from_id INTEGER;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS voice_base64 TEXT;"))
            
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_moderator BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_edit_history BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_deleted_messages BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_see_chatting_with BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_ban_users BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_until TIMESTAMP;"))
            
            db.session.execute(text("ALTER TABLE users ALTER COLUMN last_name DROP NOT NULL;"))
            db.session.commit()
            print("База данных успешно синхронизирована (Sudo-колонки и Voice добавлены).")
        except Exception as e:
            db.session.rollback()
            print(f"Ошибка при обновлении структуры базы данных: {e}")

        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin', password='admin',
                first_name='Admin', last_name='',
                is_admin=True, last_seen=now_msk()
            )
            db.session.add(admin)
            db.session.commit()

init_db()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)

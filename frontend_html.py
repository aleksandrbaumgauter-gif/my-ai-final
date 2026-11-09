HTML_HEAD = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Agent</title>
    <style>
        body { font-family: system-ui, sans-serif; background: #121212; color: #eee; padding: 15px; margin: 0; }
        .input-group { display: flex; flex-direction: column; gap: 10px; margin-bottom: 20px; }
        @media(min-width: 600px) { .input-group { flex-direction: row; flex-wrap: wrap; } }
        input[type="text"] { flex-grow: 1; padding: 12px; border-radius: 6px; border: 1px solid #333; background: #222; color: #fff; font-size: 16px; }
        button { padding: 12px 20px; background: #007acc; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; }
        button:hover { opacity: 0.8; }
        .btn-combo { background: #d84315; } .btn-art { background: #6a1b9a; } .btn-factory { background: #000; border: 1px solid #555; }
        .btn-clear { background: #c4302b; } .btn-stats { background: #555; } .btn-summary { background: #e67e22; }
        .btn-search { background: #4285f4; } .btn-deep { background: #0f9d58; }
        .grid { display: grid; grid-template-columns: 1fr; gap: 15px; }
        @media(min-width: 768px) { .grid { grid-template-columns: 1fr 1fr; } }
        .card { background: #1e1e1e; border: 1px solid #333; border-radius: 8px; padding: 15px; }
        .card h3 { margin-top: 0; color: #4caf50; font-size: 16px; }
        .card.cached { border-color: #2196f3; }
        .content { white-space: pre-wrap; font-family: monospace; font-size: 14px; }
        #status-bar { text-align: center; padding: 10px; color: #ff9800; margin-bottom: 15px; }
        .btn-save { font-size: 11px; padding: 4px 8px; background: #4caf50; color: white; border-radius: 4px; margin-left: 10px; cursor: pointer; border: none; }
        .btn-again { font-size: 11px; padding: 4px 8px; background: #ff9800; color: white; border-radius: 4px; margin-left: 5px; cursor: pointer; border: none; }
        #summary-box { background: #2c1810; border: 1px solid #e67e22; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 13px; display: none; }
    </style>
</head>
<body>
    <h1 style="text-align:center;">🤖 AI Агент</h1>
    <div id="status-bar">Готов к работе</div>
    <div id="summary-box"><b>🗜️ Резюме:</b> <span id="summary-text"></span></div>
    
    <div class="input-group">
        <input type="text" id="prompt" placeholder="Введите задачу..." value="Напиши код на Python">
        <input type="file" id="fileInput" accept="image/*" style="color:#aaa; padding:10px;">
    </div>
    <div class="input-group">
        <select id="count" style="padding:10px; background:#222; color:#fff; border:1px solid #333; border-radius:6px;">
            <option value="1">1 кадр</option><option value="2">2 кадра</option><option value="3" selected>3 кадра</option>
        </select>
        <button class="btn-factory" onclick="startFactory()">🏭 Фабрика</button>
        <button class="btn-combo" onclick="startStream()">🔥 Всё в одном</button>
        <button class="btn-art" onclick="generateImage()">🎨 Нарисовать</button>
        <button class="btn-search" onclick="webSearch(false)">🔍 Поиск</button>
        <button class="btn-deep" onclick="webSearch(true)">📖 Поиск + Чтение</button>
        <button class="btn-deep" onclick="youtubeSummary()" style="background:#c4302b;">📹 Пересказ видео</button>
        <button class="btn-search" onclick="testPush()" style="background:#ff9800;">🔔 Проверить пуши</button>
    </div>
    <div class="input-group">
        <button class="btn-summary" onclick="summarizeHistory()">🗜️ Сжать историю</button>
        <button class="btn-clear" onclick="clearCache()">🧹 Очистить кэш</button>
        <button class="btn-stats" onclick="showCacheStats()">📊 Кэш</button>
        <button class="btn-clear" onclick="clearChat()">🗑️ Диалог</button>
        <button class="btn-art" onclick="saveNote()" style="background:#2196f3;">📌 Запомнить</button>
        <button class="btn-stats" onclick="showNotes()" style="background:#4caf50;">📋 Мои заметки</button>
        <button class="btn-clear" onclick="clearSearchCards()" style="background:#555;">❌ Очистить поиск</button>
    </div>
    <div class="grid" id="grid"></div>
    <div id="image-box" style="text-align:center; margin-top:20px;"></div>
    <script>
"""

HTML_FOOT = """
    </script>
</body>
</html>
"""
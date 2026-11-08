import os, glob, json, base64, httpx, asyncio, re
from urllib.parse import urlparse, parse_qs
from fastapi.responses import HTMLResponse
from part1 import app, workers, registry
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

def get_youtube_transcript(url):
    try:
        query = urlparse(url).query
        video_id = parse_qs(query).get("v")
        if not video_id:
            if "youtu.be" in url: video_id = [url.split("/")[-1].split("?")[0]]
            else: return None, "Неверный формат ссылки"
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id[0])
            data = transcript_list.find_transcript(['ru', 'en']).fetch()
        except Exception: data = YouTubeTranscriptApi.get_transcript(video_id[0], languages=['ru', 'en'])
        return " ".join([t['text'] for t in data])[:15000], None
    except Exception as e: return None, str(e)

def download_video(url, out_path_prefix):
    try:
        ydl_opts = {
            'format': 'worstvideo[height<=144]+worstaudio/worst[ext=mp4]/worst',
            'outtmpl': out_path_prefix,
            'quiet': True,
            'no_warnings': True,
            'max_filesize': 15 * 1024 * 1024,
            'socket_timeout': 10,
            'retries': 2
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
        files = glob.glob(f"{out_path_prefix}*"); return files[0] if files else None
    except Exception: return None

async def analyze_video_with_gemini(url, prompt):
    gemini_key = os.getenv("GEMINI_KEY", "")
    if not gemini_key: 
        yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': 'Нет ключа Gemini.'})}\n\n"
        return
    yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '⏳ Скачиваем видео (144p)...'}, ensure_ascii=False)}\n\n"
    temp_file_prefix = "temp_video"
    for f in glob.glob(f"{temp_file_prefix}*"): os.remove(f)
    actual_file = download_video(url, temp_file_prefix)
    if not actual_file: 
        yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': 'Не удалось скачать видео (или >15МБ).'})}\n\n"
        return
    yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '📤 Отправляем в Gemini...'}, ensure_ascii=False)}\n\n"
    try:
        with open(actual_file, "rb") as f: video_data = base64.b64encode(f.read()).decode('utf-8')
        mime = "video/mp4" if actual_file.endswith(".mp4") else "video/webm"
        url_api = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url_api, json={"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": video_data}}]}]})
            if resp.status_code == 200:
                yield f"data: {json.dumps({'type': 'token', 'provider': 'GEMINI (Видео)', 'text': resp.json()['candidates'][0]['content']['parts'][0]['text']}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'provider': 'GEMINI (Видео)', 'total': 0.0})}\n\n"
                return
    except Exception: pass
    finally:
        for f in glob.glob(f"{temp_file_prefix}*"): os.remove(f)

HTML = """
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
        input[type="text"] { flex-grow: 1; padding: 12px; border-radius: 6px; border: 1px solid #333; background: #222; color: #fff; font-size: 16px; min-width: 200px; }
        input[type="file"] { color: #aaa; padding: 10px; font-size: 14px; }
        select { padding: 12px; border-radius: 6px; border: 1px solid #333; background: #222; color: #fff; font-size: 16px; }
        button { padding: 12px 20px; background: #007acc; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 15px; transition: 0.2s; }
        button:hover { opacity: 0.8; }
        .btn-combo { background: #d84315; }
        .btn-art { background: #6a1b9a; }
        .btn-factory { background: #000; border: 1px solid #555; }
        .btn-haiper { background: #00c853; }
        .grid { display: grid; grid-template-columns: 1fr; gap: 15px; margin-bottom: 20px; }
        @media(min-width: 768px) { .grid { grid-template-columns: 1fr 1fr; } }
        .card { background: #1e1e1e; border: 1px solid #333; border-radius: 8px; padding: 15px; min-height: 100px; position: relative; }
        .card h3 { margin-top: 0; color: #4caf50; display: flex; justify-content: space-between; align-items: center; font-size: 16px; }
        .content { white-space: pre-wrap; font-family: monospace; font-size: 14px; line-height: 1.4; }
        .image-box { text-align: center; margin-top: 20px; }
        .image-box img { max-width: 100%; border-radius: 8px; border: 1px solid #333; margin-bottom: 10px; }
        #status-bar { text-align: center; padding: 10px; font-weight: bold; color: #ff9800; font-size: 16px; margin-bottom: 15px; }
        .mem-bar-container { margin-bottom: 15px; background: #1e1e1e; padding: 10px; border-radius: 8px; display: flex; align-items: center; gap: 10px; }
        .mem-bar-bg { flex-grow: 1; height: 8px; background: #333; border-radius: 4px; overflow: hidden; }
        .mem-bar-fill { height: 100%; width: 0%; background: #4caf50; transition: width 0.3s; }
        .btn-save { font-size: 11px; padding: 4px 8px; background: #4caf50; color: white; border-radius: 4px; margin-left: 10px; }
    </style>
</head>
<body>
    <h1 style="text-align:center;">🤖 AI Агент</h1>
    
    <div class="mem-bar-container">
        <span id="mem-text" style="font-size:12px; width: 110px;">Память: 0%</span>
        <div class="mem-bar-bg"><div id="mem-bar" class="mem-bar-fill"></div></div>
        <button onclick="clearHistory()" style="background:#c4302b; padding:6px 12px; font-size:12px;">🗑️ Очистить</button>
    </div>

    <div id="status-bar">Готов к работе</div>

    <div class="input-group">
        <input type="text" id="prompt" placeholder="Введите задачу..." value="Что на видео? https://youtu.be/dQw4w9WgXcQ">
        <input type="file" id="fileInput" accept="image/*">
    </div>

    <div class="input-group" style="justify-content: center; background: #1e1e1e; padding: 15px; border-radius: 8px;">
        <label for="count" style="align-self: center;">Количество кадров:</label>
        <select id="count">
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3" selected>3</option>
        </select>
        <button class="btn-factory" onclick="startFactory()">🏭 Запустить Фабрику</button>
    </div>

    <div class="input-group">
        <button class="btn-combo" onclick="startStream()">🔥 Всё в одном</button>
        <button class="btn-art" onclick="generateImage()">🎨 Нарисовать</button>
        <button class="btn-haiper" onclick="window.open('https://haiper.ai', '_blank')">🌐 Оживить на Haiper</button>
    </div>

    <h3 style="text-align:center; color:#888;">Результаты</h3>
    <div class="grid" id="grid"></div>
    <div class="image-box" id="image-box"></div>

    <h3 style="text-align:center; color:#888; margin-top:30px;">Сохраненная история</h3>
    <div class="grid" id="history-grid"></div>

    <script>
        let currentEventSource = null;
        const HISTORY_LIMIT = 3; // <--- ИЗМЕНЕНО: Храним только 3 записи

        if ("Notification" in window) { Notification.requestPermission(); }

        function sendNotification(text) {
            if ("Notification" in window && Notification.permission === "granted") {
                new Notification("AI Агент", { body: text });
            }
        }

        // --- СЖАТИЕ ФОТО НА КЛИЕНТЕ ---
        function compressImage(file, maxWidth = 800) {
            return new Promise((resolve) => {
                const reader = new FileReader();
                reader.readAsDataURL(file);
                reader.onload = (event) => {
                    const img = new Image();
                    img.src = event.target.result;
                    img.onload = () => {
                        const canvas = document.createElement('canvas');
                        const scale = maxWidth / img.width;
                        canvas.width = maxWidth;
                        canvas.height = img.height * scale;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                        resolve(canvas.toDataURL('image/jpeg', 0.7).split(',')[1]);
                    };
                };
            });
        }

        // --- ПАМЯТЬ И ИСТОРИЯ ---
        function updateMemoryBar() {
            let history = JSON.parse(localStorage.getItem('chat_history') || "[]");
            // 3 записи = 100% памяти
            let percent = Math.min(100, (history.length / HISTORY_LIMIT) * 100); 
            const bar = document.getElementById("mem-bar");
            const text = document.getElementById("mem-text");
            if (bar && text) {
                bar.style.width = percent + "%";
                text.innerText = `Память: ${Math.round(percent)}%`;
                bar.style.background = percent > 80 ? "#f44336" : "#4caf50";
            }
        }

        function clearHistory() {
            if (confirm("Очистить всю историю? Это освободит память.")) {
                localStorage.removeItem('chat_history');
                document.getElementById("history-grid").innerHTML = "";
                updateMemoryBar();
            }
        }

        function saveToHistory(type, content) {
            let history = JSON.parse(localStorage.getItem('chat_history') || "[]");
            history.push({ type: type, content: content });
            // Ограничиваем историю 3 последними записями
            if (history.length > HISTORY_LIMIT) history = history.slice(-HISTORY_LIMIT);
            
            try {
                localStorage.setItem('chat_history', JSON.stringify(history));
            } catch (e) {
                alert("Память переполнена! Удалите старые картинки из истории.");
                return;
            }
            updateMemoryBar();
            loadHistory();
        }

        function loadHistory() {
            const grid = document.getElementById("history-grid");
            if (!grid) return;
            grid.innerHTML = "";
            let history = JSON.parse(localStorage.getItem('chat_history') || "[]");
            history.forEach((item, index) => {
                const div = document.createElement("div");
                div.className = "card";
                if (item.type === "text") {
                    div.innerHTML = `<h3>Текст ${index+1}</h3><div class="content">${item.content}</div>`;
                } else {
                    div.innerHTML = `<h3>Картинка ${index+1}</h3><img src="${item.content}" loading="lazy" style="max-width:100%; border-radius:8px;">`;
                }
                grid.appendChild(div);
            });
        }

        window.onload = function() { 
            let history = JSON.parse(localStorage.getItem('chat_history') || "[]");
            if (history.length > HISTORY_LIMIT) {
                localStorage.setItem('chat_history', JSON.stringify(history.slice(-HISTORY_LIMIT)));
            }
            updateMemoryBar(); 
            loadHistory(); 
        };

        // --- ОСНОВНЫЕ ФУНКЦИИ ---
        async function startStream() {
            const inputVal = document.getElementById('prompt').value;
            const fileInput = document.getElementById('fileInput');
            if (!inputVal && !fileInput.files.length) return;
            if (currentEventSource) currentEventSource.close();
            document.getElementById("grid").innerHTML = "";
            document.getElementById("image-box").innerHTML = "";
            const statusBar = document.getElementById("status-bar");

            if (fileInput.files.length > 0) {
                statusBar.innerText = "📸 Анализируем фото...";
                statusBar.style.color = "#ff9800";
                const file = fileInput.files[0];
                
                const base64Image = await compressImage(file);
                
                const res = await fetch('/analyze_image', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ prompt: inputVal || "Что на фото?", image: base64Image })
                });
                const data = await res.json();
                const div = document.createElement("div");
                div.className = "card";
                const safeText = data.text.replace(/'/g, "\\'").replace(/"/g, '&quot;').replace(/\\n/g, ' ');
                div.innerHTML = `<h3>АНАЛИЗ ФОТО <button class="btn-save" onclick="saveToHistory('text', '${safeText}')">💾 В историю</button></h3><div class="content" style="color:#ff9800">${data.text}</div>`;
                document.getElementById("grid").appendChild(div);
                statusBar.innerText = "✅ Фото проанализировано!";
                statusBar.style.color = "#4caf50";
                return;
            }

            statusBar.innerText = "⏳ Отправляем запрос в нейросети...";
            statusBar.style.color = "#ff9800";
            currentEventSource = new EventSource(`/stream?prompt=${encodeURIComponent(inputVal)}`);
            const cards = {};
            let fullText = "";

            currentEventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);
                const p = data.provider;
                
                if (data.type === "token" && data.text.includes("Скачиваем")) {
                    statusBar.innerText = data.text;
                    statusBar.style.color = "#ff9800";
                    return;
                }
                
                statusBar.innerText = `⏳ Ждем ответ от ${p}...`;

                if (!cards[p]) {
                    const div = document.createElement("div");
                    div.className = "card";
                    div.innerHTML = `<h3>${p} <button class="btn-save" onclick="saveToHistory('text', document.getElementById('text-${p}').innerText)">💾 В историю</button></h3><div class="content" id="text-${p}"></div>`;
                    document.getElementById("grid").appendChild(div);
                    cards[p] = true;
                }
                if (data.type === "token") {
                    fullText += data.text;
                    document.getElementById(`text-${p}`).innerText += data.text;
                } else if (data.type === "done") {
                    statusBar.innerText = `✅ ${p} ответил!`;
                    statusBar.style.color = "#4caf50";
                }
            };
            currentEventSource.onerror = function() {
                statusBar.innerText = "❌ Соединение прервано";
                statusBar.style.color = "#f44336";
                currentEventSource.close();
            };
        }

        function generateImage() {
            const prompt = document.getElementById("prompt").value;
            const box = document.getElementById("image-box");
            const statusBar = document.getElementById("status-bar");
            statusBar.innerText = "🎨 Отправляем запрос в FLUX...";
            statusBar.style.color = "#ff9800";
            box.innerHTML = '<p>⏳ Рисуем...</p>';
            
            fetch(`/image?prompt=${encodeURIComponent(prompt)}`)
                .then(res => res.blob())
                .then(blob => {
                    const url = URL.createObjectURL(blob);
                    box.innerHTML = `<img src="${url}"><br><button class="btn-save" style="margin-top:10px; font-size:14px; padding:8px 15px;" onclick="saveToHistory('image', '${url}')">💾 Сохранить в историю</button>`;
                    statusBar.innerText = "✅ Картинка готова!";
                    statusBar.style.color = "#4caf50";
                })
                .catch(() => {
                    box.innerHTML = '<p style="color:red">❌ Ошибка</p>';
                    statusBar.innerText = "❌ Ошибка при создании картинки";
                    statusBar.style.color = "#f44336";
                });
        }

        function startFactory() {
            const prompt = document.getElementById("prompt").value;
            const count = document.getElementById("count").value;
            const box = document.getElementById("image-box");
            const statusBar = document.getElementById("status-bar");
            box.innerHTML = '';
            statusBar.innerText = "🏭 Запускаем фабрику...";
            statusBar.style.color = "#ff9800";

            const es = new EventSource(`/factory?prompt=${encodeURIComponent(prompt)}&count=${count}`);
            es.onmessage = function(e) {
                const d = JSON.parse(e.data);
                if (d.status) {
                    statusBar.innerText = d.status;
                    statusBar.style.color = d.status.includes("Ждем") ? "#f44336" : "#ff9800";
                }
                if (d.image) {
                    const div = document.createElement("div");
                    div.style.margin = "10px 0";
                    div.innerHTML = `<p style="color:#aaa;">Кадр ${d.scene}</p><img src="data:image/jpeg;base64,${d.image}" loading="lazy" style="max-width:100%; border-radius:8px;"><br><button class="btn-save" style="margin-top:8px;" onclick="saveToHistory('image', 'data:image/jpeg;base64,${d.image}')">💾 Сохранить кадр</button>`;
                    box.appendChild(div);
                }
                if (d.done) {
                    statusBar.innerText = "✅ Фабрика завершила работу!";
                    statusBar.style.color = "#4caf50";
                    sendNotification("Фабрика закончила работу! Кадры готовы.");
                    es.close();
                }
            };
            es.onerror = function() {
                statusBar.innerText = "❌ Фабрика остановилась";
                statusBar.style.color = "#f44336";
                es.close();
            };
        }
    </script>
</body>
</html>
"""

@app.get("/")
async def index(): return HTMLResponse(HTML)
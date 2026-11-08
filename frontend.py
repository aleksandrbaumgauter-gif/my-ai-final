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
        input[type="text"] { flex-grow: 1; padding: 12px; border-radius: 6px; border: 1px solid #333; background: #222; color: #fff; font-size: 16px; }
        button { padding: 12px 20px; background: #007acc; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; }
        button:hover { opacity: 0.8; }
        .btn-combo { background: #d84315; } .btn-art { background: #6a1b9a; } .btn-factory { background: #000; border: 1px solid #555; }
        .btn-clear { background: #c4302b; } .btn-stats { background: #555; } .btn-summary { background: #e67e22; }
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
    </div>
    <div class="input-group">
        <button class="btn-summary" onclick="summarizeHistory()">🗜️ Сжать историю</button>
        <button class="btn-clear" onclick="clearCache()">🧹 Очистить кэш</button>
        <button class="btn-stats" onclick="showCacheStats()">📊 Кэш</button>
        <button class="btn-clear" onclick="clearChat()">🗑️ Диалог</button>
        <button class="btn-art" onclick="saveNote()" style="background:#2196f3;">📌 Запомнить</button>
        <button class="btn-stats" onclick="showNotes()" style="background:#4caf50;">📋 Мои заметки</button>
    </div>
    <div class="grid" id="grid"></div>
    <div id="image-box" style="text-align:center; margin-top:20px;"></div>

    <script>
        let currentEventSource = null;

        async function setupPush() {
            if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
            const reg = await navigator.serviceWorker.register('/sw.js');
            let sub = await reg.pushManager.getSubscription();
            if (!sub) {
                const PUBLIC_KEY = "BB9_AyDK3Ib0EQXvHSq66iQkTWMoQ5Ow-Id8e1w3z31PIXoSWqWQUQqUn6f1Z8";
                sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(PUBLIC_KEY) });
            }
            await fetch('/subscribe', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(sub) });
        }
        function urlBase64ToUint8Array(base64String) {
            const padding = '='.repeat((4 - base64String.length % 4) % 4);
            const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
            const rawData = window.atob(base64); const outputArray = new Uint8Array(rawData.length);
            for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
            return outputArray;
        }
        window.addEventListener('load', setupPush);

        if ("Notification" in window && Notification.permission !== "granted") { Notification.requestPermission(); }

        function compressImage(file) { return new Promise(res => { const r = new FileReader(); r.readAsDataURL(file); r.onload = e => { const i = new Image(); i.src = e.target.result; i.onload = () => { const c = document.createElement('canvas'); c.width = 800; c.height = i.height * (800/i.width); c.getContext('2d').drawImage(i, 0, 0, c.width, c.height); res(c.toDataURL('image/jpeg', 0.7).split(',')[1]); }; }; }); }
        function saveToHistory(type, content) { let h = JSON.parse(localStorage.getItem('chat_history') || "[]"); h.push({ type, content }); if (h.length > 3) h = h.slice(-3); try { localStorage.setItem('chat_history', JSON.stringify(h)); } catch (e) { alert("Память переполнена!"); return; } }
        function getChatHistory() { return JSON.parse(localStorage.getItem('chat_dialog') || "[]"); }
        function saveChatHistory(h) { localStorage.setItem('chat_dialog', JSON.stringify(h)); }
        function addToChat(role, content) { let h = getChatHistory(); h.push({ role, content }); if (h.length > 12) h = h.slice(-12); saveChatHistory(h); }

        window.onload = function() { const s = localStorage.getItem('chat_summary'); if (s) { document.getElementById('summary-box').style.display = 'block'; document.getElementById('summary-text').innerText = s; } };

        async function summarizeHistory() {
            const h = getChatHistory();
            if (h.length < 4) return alert("Слишком короткая история.");
            document.getElementById("status-bar").innerText = "🗜️ Сжимаем...";
            const res = await fetch('/summarize', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ history: h }) });
            const data = await res.json();
            if (data.summary) {
                localStorage.setItem('chat_summary', data.summary);
                saveChatHistory(h.slice(-2));
                document.getElementById('summary-box').style.display = 'block';
                document.getElementById('summary-text').innerText = data.summary;
                document.getElementById("status-bar").innerText = "✅ Сжато!";
            } else alert("Ошибка сжатия");
        }
        function clearChat() { if(!confirm("Очистить диалог?")) return; localStorage.removeItem('chat_dialog'); localStorage.removeItem('chat_summary'); document.getElementById('summary-box').style.display = 'none'; alert("Очищено"); }
        function clearCache() { if(!confirm("Очистить кэш?")) return; fetch('/cache/clear', { method: 'POST' }).then(r => r.json()).then(d => alert(`Удалено: ${d.deleted}`)); }
        function showCacheStats() { fetch('/cache/stats').then(r => r.json()).then(d => alert(`Записей: ${d.records}\nРазмер: ${(d.size_bytes/1024).toFixed(1)} КБ`)); }

        function saveNote() {
            const pv = document.getElementById('prompt').value;
            if (!pv) return alert("Введите текст для сохранения");
            let notes = JSON.parse(localStorage.getItem('my_notes') || "[]");
            notes.push(pv); localStorage.setItem('my_notes', JSON.stringify(notes));
            alert("✅ Сохранено!");
        }
        function showNotes() {
            let notes = JSON.parse(localStorage.getItem('my_notes') || "[]");
            if (notes.length === 0) return alert("Заметок нет.");
            let text = "Заметки:\n\n"; notes.forEach((n, i) => { text += `${i+1}. ${n}\n`; });
            let choice = prompt(text + "\nВведите номер, чтобы вставить:");
            if (choice && !isNaN(choice) && notes[choice - 1]) document.getElementById('prompt').value = notes[choice - 1];
        }

        async function startStream() {
            const pv = document.getElementById('prompt').value;
            const fi = document.getElementById('fileInput');
            const sb = document.getElementById("status-bar");
            if (currentEventSource) currentEventSource.close();
            document.getElementById("grid").innerHTML = ""; document.getElementById("image-box").innerHTML = "";
            
            if (fi.files.length > 0) {
                sb.innerText = "📸 Анализируем фото...";
                const b64 = await compressImage(fi.files[0]);
                const res = await fetch('/analyze_image', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ prompt: pv || "Что на фото?", image: b64 }) });
                const data = await res.json();
                
                const d = document.createElement("div"); d.className = "card";
                const h3 = document.createElement("h3"); h3.textContent = "АНАЛИЗ ФОТО ";
                const btn = document.createElement("button"); btn.className = "btn-save"; btn.textContent = "💾";
                btn.onclick = () => saveToHistory('text', data.text);
                h3.appendChild(btn);
                const contentDiv = document.createElement("div"); contentDiv.className = "content"; contentDiv.textContent = data.text;
                
                d.appendChild(h3); d.appendChild(contentDiv);
                document.getElementById("grid").appendChild(d); sb.innerText = "✅ Готово!"; return;
            }
            
            sb.innerText = "⏳ Отправляем...";
            const summary = localStorage.getItem('chat_summary');
            const recent = getChatHistory().slice(-2);
            let fullPrompt = pv;
            if (summary || recent.length > 0) {
                let ctx = ""; if (summary) ctx += `[Резюме: ${summary}]\n`; if (recent.length > 0) ctx += `[История: ${recent.map(m => m.role + ": " + m.content).join(" | ")}]\n`;
                fullPrompt = ctx + "\n" + pv;
            }
            
            currentEventSource = new EventSource(`/stream?prompt=${encodeURIComponent(fullPrompt)}&cache_prompt=${encodeURIComponent(pv)}`);
            const cards = {};
            let savedToHistory = false;
            
            currentEventSource.onmessage = (e) => {
                const data = JSON.parse(e.data), p = data.provider;
                const isCached = p.startsWith("КЭШ");
                sb.innerText = isCached ? "🗄️ Из кэша" : `⏳ Ждем ${p}...`;
                
                if (!cards[p]) { 
                    const d = document.createElement("div"); d.className = "card"; if (isCached) d.classList.add('cached');
                    const header = document.createElement("h3"); header.textContent = p + " ";
                    const saveBtn = document.createElement("button"); saveBtn.className = "btn-save"; saveBtn.textContent = "💾";
                    saveBtn.onclick = () => saveToHistory('text', document.getElementById(`text-${p}`).textContent);
                    const againBtn = document.createElement("button"); againBtn.className = "btn-again"; againBtn.textContent = "🔄";
                    againBtn.onclick = () => askAgain(encodeURIComponent(pv));
                    header.appendChild(saveBtn); header.appendChild(againBtn);
                    const contentDiv = document.createElement("div"); contentDiv.className = "content"; contentDiv.id = `text-${p}`;
                    d.appendChild(header); d.appendChild(contentDiv);
                    document.getElementById("grid").appendChild(d); cards[p] = true; 
                }
                if (data.type === "token") { document.getElementById(`text-${p}`).textContent += data.text; } 
                else if (data.type === "done") { 
                    sb.innerText = `✅ ${p} ответил!`; 
                    if (!savedToHistory) { addToChat("user", pv); addToChat("assistant", document.getElementById(`text-${p}`).textContent); savedToHistory = true; }
                } else if (data.type === "error") { document.getElementById(`text-${p}`).textContent += `\n❌ ${data.error}`; }
            };
            currentEventSource.onerror = () => { sb.innerText = "❌ Обрыв"; currentEventSource.close(); };
        }

        function askAgain(pvEncoded) { document.getElementById('prompt').value = decodeURIComponent(pvEncoded); startStream(); }

        function generateImage() {
            const p = document.getElementById("prompt").value, b = document.getElementById("image-box"), sb = document.getElementById("status-bar");
            b.innerHTML = '<p>⏳ Рисуем...</p>'; sb.innerText = "🎨 Рисуем...";
            fetch(`/image?prompt=${encodeURIComponent(p)}`).then(r => r.blob()).then(blob => {
                const url = URL.createObjectURL(blob);
                b.innerHTML = ""; const img = document.createElement("img"); img.src = url; img.style.maxWidth = "100%"; img.style.borderRadius = "8px";
                const btn = document.createElement("button"); btn.className = "btn-save"; btn.textContent = "💾 Сохранить";
                btn.onclick = () => saveToHistory('image', url);
                b.appendChild(img); b.appendChild(document.createElement("br")); b.appendChild(btn);
                sb.innerText = "✅ Готово!";
            }).catch(() => { b.innerHTML = '<p style="color:red">❌ Ошибка</p>'; sb.innerText = "❌ Ошибка"; });
        }

        function startFactory() {
            const p = document.getElementById("prompt").value, c = document.getElementById("count").value, b = document.getElementById("image-box"), sb = document.getElementById("status-bar");
            b.innerHTML = ''; sb.innerText = "🏭 Запускаем...";
            fetch(`/factory/start?prompt=${encodeURIComponent(p)}&count=${c}`, { method: 'POST' }).then(r => r.json()).then(data => {
                const jid = data.job_id;
                const pingInterval = setInterval(() => { fetch('/ping').catch(() => {}); }, 5 * 60 * 1000);
                const poll = setInterval(async () => {
                    const s = await (await fetch(`/factory/status/${jid}`)).json();
                    if (s.result && s.result.length > 0) {
                        b.innerHTML = "";
                        s.result.forEach((imgData, i) => { 
                            const d = document.createElement("div"); 
                            const pText = document.createElement("p"); pText.textContent = `Кадр ${i+1}`;
                            const img = document.createElement("img"); img.src = `data:image/jpeg;base64,${imgData}`; img.style.maxWidth = "100%"; img.style.borderRadius = "8px";
                            const btn = document.createElement("button"); btn.className = "btn-save"; btn.textContent = "💾";
                            btn.onclick = () => saveToHistory('image', `data:image/jpeg;base64,${imgData}`);
                            d.appendChild(pText); d.appendChild(img); d.appendChild(document.createElement("br")); d.appendChild(btn);
                            b.appendChild(d); 
                        });
                        sb.innerText = `🏭 Готово ${s.result.length} из ${c}...`;
                    }
                    if (s.status === "done") { 
                        clearInterval(poll); clearInterval(pingInterval); sb.innerText = "✅ Фабрика завершена!"; 
                        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
                        if ("Notification" in window && Notification.permission === "granted") { new Notification("🏭 AI Агент", { body: "Фабрика завершила работу!" }); }
                    }
                }, 3000);
            });
        }
    </script>
</body>
</html>
"""
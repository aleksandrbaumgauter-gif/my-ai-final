from frontend_html import HTML_HEAD, HTML_FOOT

HTML_JS = """
        let currentEventSource = null;

        async function setupPush() {
            if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
            const reg = await navigator.serviceWorker.register('/sw.js');
            let sub = await reg.pushManager.getSubscription();
            if (!sub) {
                const PUBLIC_KEY = "BBzi3QLw6q9Tv0mMcprrO9BMevowqy1qo9NobPEcKuk7C73qNPg3yfWj-3oBVcueVgy7zCy_6tJJw6ipC9uMHMk";
                sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(PUBLIC_KEY) });
            }
            await fetch('/subscribe', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(sub) });
        }
        function urlBase64ToUint8Array(base64String) {
            const padding = '='.repeat((4 - base64String.length % 4) % 4);
            // 👇 ИСПРАВЛЕНО: убран обратный слеш
            const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
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

        async function webSearch(isDeep) {
            const query = document.getElementById('prompt').value;
            if (!query) return alert("Введите запрос для поиска");
            const sb = document.getElementById("status-bar");
            sb.innerText = isDeep ? "📖 Читаем сайт..." : "🔍 Ищем...";
            try {
                const res = await fetch(`/search?query=${encodeURIComponent(query)}&deep=${isDeep}`);
                const data = await res.json();
                const d = document.createElement("div"); d.className = "card search-card"; d.style.borderColor = "#4285f4";
                const h3 = document.createElement("h3"); h3.textContent = isDeep ? "📖 ПОИСК + ЧТЕНИЕ" : "🔍 ПОИСК";
                const contentDiv = document.createElement("div"); contentDiv.className = "content"; contentDiv.textContent = data.results;
                d.appendChild(h3); d.appendChild(contentDiv);
                document.getElementById("grid").prepend(d);
                sb.innerText = "✅ Готово!";
            } catch (e) { sb.innerText = "❌ Ошибка поиска"; }
        }

        async function youtubeSummary() {
            const url = document.getElementById('prompt').value;
            if (!url.includes('youtube') && !url.includes('youtu.be')) return alert("Введите ссылку на YouTube");
            const sb = document.getElementById("status-bar");
            sb.innerText = "📹 Читаем субтитры...";
            try {
                const res = await fetch(`/youtube_summary?url=${encodeURIComponent(url)}`);
                const data = await res.json();
                const d = document.createElement("div"); d.className = "card";
                const h3 = document.createElement("h3"); h3.textContent = "📹 ПЕРЕСКАЗ ВИДЕО";
                const contentDiv = document.createElement("div"); contentDiv.className = "content"; contentDiv.textContent = data.summary;
                d.appendChild(h3); d.appendChild(contentDiv);
                document.getElementById("grid").prepend(d);
                sb.innerText = "✅ Готово!";
            } catch (e) { sb.innerText = "❌ Ошибка"; }
        }

        async function testPush() {
            const sb = document.getElementById("status-bar");
            sb.innerText = "🔔 Отправляем тест...";
            try {
                const res = await fetch('/push/test', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'ok') sb.innerText = `✅ Отправлено на ${data.sent} устройств!`;
                else sb.innerText = `❌ ${data.message}`;
            } catch (e) { sb.innerText = "❌ Ошибка отправки"; }
        }

        function clearSearchCards() {
            const cards = document.querySelectorAll('.search-card');
            cards.forEach(c => c.remove());
            document.getElementById("status-bar").innerText = "Карточки поиска очищены.";
        }

        async function startStream() {
            const pvInput = document.getElementById('prompt').value;
            let pv = pvInput;
            const fi = document.getElementById('fileInput');
            const sb = document.getElementById("status-bar");
            if (currentEventSource) currentEventSource.close();
            document.getElementById("grid").innerHTML = ""; document.getElementById("image-box").innerHTML = "";
            
            if (fi.files.length === 0) {
                const searchKeywords = ["погода", "новости", "сегодня", "курс", "найди", "поищи", "последние", "кто такой", "где находится"];
                const questionTriggers = ["что такое", "кто такой", "где находится", "когда произошло", "сколько стоит", "как работает"];
                const needSearch = searchKeywords.some(w => pv.toLowerCase().includes(w)) || questionTriggers.some(w => pv.toLowerCase().startsWith(w));
                if (needSearch) {
                    sb.innerText = "🔍 Агент ищет информацию...";
                    try {
                        const searchRes = await fetch(`/search?query=${encodeURIComponent(pv)}&deep=false`);
                        const searchData = await searchRes.json();
                        pv = `Я нашел в интернете следующую информацию:\n${searchData.results}\n\nИспользуй её, чтобы дать точный ответ на вопрос: ${pv}`;
                    } catch (e) { console.log("Авто-поиск не удался", e); }
                }
            }
            
            if (fi.files.length > 0) {
                sb.innerText = "📸 Анализируем фото...";
                const b64 = await compressImage(fi.files[0]);
                const res = await fetch('/analyze_image', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ prompt: pvInput || "Что на фото?", image: b64 }) });
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
            
            currentEventSource = new EventSource(`/stream?prompt=${encodeURIComponent(fullPrompt)}&cache_prompt=${encodeURIComponent(pvInput)}`);
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
                    againBtn.onclick = () => askAgain(encodeURIComponent(pvInput));
                    header.appendChild(saveBtn); header.appendChild(againBtn);
                    const contentDiv = document.createElement("div"); contentDiv.className = "content"; contentDiv.id = `text-${p}`;
                    d.appendChild(header); d.appendChild(contentDiv);
                    document.getElementById("grid").appendChild(d); cards[p] = true; 
                }
                if (data.type === "token") { document.getElementById(`text-${p}`).textContent += data.text; } 
                else if (data.type === "done") { 
                    sb.innerText = `✅ ${p} ответил!`; 
                    if (!savedToHistory) { addToChat("user", pvInput); addToChat("assistant", document.getElementById(`text-${p}`).textContent); savedToHistory = true; }
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
"""

HTML = HTML_HEAD + HTML_JS + HTML_FOOT

async def analyze_video_with_gemini(url, prompt):
    gemini_key = os.getenv("GEMINI_KEY", "")
    if not gemini_key: 
        yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': 'Нет ключа Gemini.'})}\n\n"
        return
    yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '⏳ Скачиваем видео...'}, ensure_ascii=False)}\n\n"
    temp_file_prefix = "temp_video"
    for f in glob.glob(f"{temp_file_prefix}*"): os.remove(f)
    actual_file = download_video(url, temp_file_prefix)
    if not actual_file: 
        yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': 'Не удалось скачать видео (или >20МБ).'})}\n\n"
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

HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>AI Agent</title><style>body{font-family:system-ui,sans-serif;background:#121212;color:#eee;padding:15px;margin:0}.input-group{display:flex;flex-direction:column;gap:10px;margin-bottom:20px}@media(min-width:600px){.input-group{flex-direction:row;flex-wrap:wrap}}input[type="text"]{flex-grow:1;padding:12px;border-radius:6px;border:1px solid #333;background:#222;color:#fff;font-size:16px;min-width:200px}input[type="file"]{color:#aaa;padding:10px;font-size:14px}button{padding:12px 20px;background:#007acc;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:bold;font-size:15px}button:hover{background:#005999}.btn-combo{background:#d84315}.btn-combo:hover{background:#bf360c}.btn-art{background:#6a1b9a}.btn-art:hover{background:#4a148c}.btn-video{background:#c4302b}.btn-video:hover{background:#9e2622}.grid{display:grid;grid-template-columns:1fr;gap:15px}@media(min-width:768px){.grid{grid-template-columns:1fr 1fr}}.card{background:#1e1e1e;border:1px solid #333;border-radius:8px;padding:15px;min-height:150px}.card h3{margin-top:0;color:#4caf50;display:flex;justify-content:space-between;font-size:18px}.content{white-space:pre-wrap;font-family:monospace;font-size:15px;line-height:1.4}.image-box{text-align:center;margin-top:20px}.image-box img, .image-box video{max-width:100%;border-radius:8px;border:1px solid #333}</style></head><body><h1 style="text-align:center;">🤖 AI Агент</h1><div class="input-group"><input type="text" id="prompt" placeholder="Текст или ссылка..." value="Что на видео? https://youtu.be/dQw4w9WgXcQ"><input type="file" id="fileInput" accept="image/*"><button class="btn-combo" onclick="startStream()">🔥 Всё в одном</button><button class="btn-art" onclick="generateImage()">🎨 Нарисовать</button><button class="btn-video" onclick="generateVideo()">🎬 Видео</button></div><div class="grid" id="grid"></div><div class="image-box" id="image-box"></div><script>let currentEventSource=null;async function startStream(){const inputVal=document.getElementById('prompt').value;const fileInput=document.getElementById('fileInput');if(!inputVal&&!fileInput.files.length)return;if(currentEventSource)currentEventSource.close();document.getElementById("grid").innerHTML="";document.getElementById("image-box").innerHTML="";let base64Image=null;if(fileInput.files.length>0){const file=fileInput.files[0];base64Image=await new Promise((resolve)=>{const reader=new FileReader();reader.onloadend=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(file);});}if(base64Image){const res=await fetch('/analyze_image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:inputVal,image:base64Image})});const data=await res.json();const div=document.createElement("div");div.className="card";div.innerHTML=`<h3>АНАЛИЗ ФОТО</h3><div class="content" style="color:#ff9800">${data.text}</div>`;document.getElementById("grid").appendChild(div);return;}currentEventSource=new EventSource(`/stream?prompt=${encodeURIComponent(inputVal)}`);const cards={};currentEventSource.onmessage=function(e){const data=JSON.parse(e.data);const p=data.provider;if(!cards[p]){const div=document.createElement("div");div.className="card";div.innerHTML=`<h3>${p} <span style="font-size:14px" id="ttft-${p}">...</span></h3><div class="content" id="text-${p}"></div>`;document.getElementById("grid").appendChild(div);cards[p]=true;}if(data.type==="ttft"){const badge=data.value===0?"⚡ КЭШ":data.value.toFixed(2)+"s";document.getElementById(`ttft-${p}`).innerText=badge;}else if(data.type==="token"){document.getElementById(`text-${p}`).innerText+=data.text;}else if(data.type==="error"){document.getElementById(`text-${p}`).innerText+=`\\n[ОШИБКА: ${data.error}]`;}};}function generateImage(){const prompt=document.getElementById("prompt").value;const box=document.getElementById("image-box");box.innerHTML='<p>⏳ Рисуем...</p>';fetch(`/image?prompt=${encodeURIComponent(prompt)}`).then(res=>res.blob()).then(blob=>{const img=document.createElement("img");img.src=URL.createObjectURL(blob);box.innerHTML='';box.appendChild(img);}).catch(()=>{box.innerHTML='<p style="color:red">❌ Ошибка</p>';});}function generateVideo(){const prompt=document.getElementById("prompt").value;const box=document.getElementById("image-box");box.innerHTML='<p>⏳ Генерируем видео (до 2 минут)...</p>';fetch(`/video?prompt=${encodeURIComponent(prompt)}`).then(res=>{if(!res.ok)throw new Error("Ошибка");return res.blob();}).then(blob=>{const url=URL.createObjectURL(blob);box.innerHTML=`<video controls autoplay loop src="${url}"></video>`;}).catch(()=>{box.innerHTML='<p style="color:red">❌ Не удалось (лимит или модель перегружена)</p>';});}</script></body></html>"""

@app.get("/")
async def index(): return HTMLResponse(HTML)

class ImageRequest(BaseModel): prompt: str; image: str

@app.post("/analyze_image")
async def analyze_image(req: ImageRequest):
    gemini_key = os.getenv("GEMINI_KEY", "")
    if gemini_key:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}", json={"contents": [{"parts": [{"text": req.prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": req.image}}]}]})
                if resp.status_code == 200: return {"text": f"👁️ **Gemini:** {resp.json()['candidates'][0]['content']['parts'][0]['text']}"}
        except Exception: pass
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").split(",")[0].strip()
    if openrouter_key:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {openrouter_key}"}, json={"model": "allenai/molmo-2-8b:free", "messages": [{"role": "user", "content": [{"type": "text", "text": req.prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{req.image}"}}]}]})
                if resp.status_code == 200: return {"text": f"🌐 **OpenRouter:** {resp.json()['choices'][0]['message']['content']}"}
                return {"text": f"❌ Ошибка OpenRouter: {resp.text}"}
        except Exception as e: return {"text": f"❌ Сеть: {e}"}
    return {"text": "❌ Нет ключей для фото."}

@app.get("/stream")
async def stream(prompt: str = Query(...)):
    async def generator():
        youtube_url = re.search(r'(https?://[^\s]+)', prompt)
        if youtube_url:
            url = youtube_url.group(1)
            user_question = prompt.replace(url, "").strip()
            question = user_question if user_question else "Опиши подробно, что происходит в этом видео."
            success = False
            async for chunk in analyze_video_with_gemini(url, question):
                yield chunk
                if '"type": "done"' in chunk: success = True
            if not success:
                yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '⚠️ Видео не осилил, беру субтитры...'}, ensure_ascii=False)}\n\n"
                transcript, error = get_youtube_transcript(url)
                if not error:
                    final_prompt = f"Вопрос: '{question}'\n\nСубтитры:\n{transcript}"
                    queue = asyncio.Queue()
                    tasks = []
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        for p in workers:
                            if p.cfg["id"] == "gemini" or not registry.get(p.name).is_available(): continue
                            tasks.append(asyncio.create_task(p.stream(client, final_prompt, queue)))
                        active = len(tasks)
                        while active > 0:
                            ev = await queue.get()
                            if ev["type"] in ["done", "error"]: active -= 1
                            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': f'Субтитры тоже не достались: {error}'})}\n\n"
            return
        queue = asyncio.Queue()
        tasks = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for p in workers:
                if p.cfg["id"] == "gemini" or not registry.get(p.name).is_available(): continue
                tasks.append(asyncio.create_task(p.stream(client, prompt, queue)))
            active = len(tasks)
            while active > 0:
                ev = await queue.get()
                if ev["type"] in ["done", "error"]: active -= 1
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    return StreamingResponse(generator(), media_type="text/event-stream")

@app.get("/image")
async def image(prompt: str = Query(...)):
    image_data = await generate_image_hf(prompt)
    if image_data:
        return Response(content=image_data, media_type="image/jpeg")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://image.pollinations.ai/prompt/{prompt}?width=512&height=512&nologo=true")
        return Response(content=resp.content, media_type="image/jpeg")

@app.get("/video")
async def video(prompt: str = Query(...)):
    video_data = await generate_video_hf(prompt)
    if video_data:
        return Response(content=video_data, media_type="video/mp4")
    return Response(content=b"Error generating video", status_code=500)

async def generate_image_hf(prompt):
    tokens_str = os.getenv("HUGGINGFACE_TOKEN", "")
    tokens = [t.strip() for t in tokens_str.split(",") if t.strip()]
    if not tokens: return None
    models_to_try = ["black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0"]
    for model_id in models_to_try:
        api_url = f"https://api-inference.huggingface.co/models/{model_id}"
        payload = {"inputs": prompt}
        for token in tokens:
            headers = {"Authorization": f"Bearer {token}"}
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(api_url, headers=headers, json=payload)
                    if resp.status_code == 200: return resp.content
                    elif resp.status_code == 429: continue
                    elif resp.status_code == 503: break
            except Exception: continue
    return None

async def generate_video_hf(prompt):
    tokens_str = os.getenv("HUGGINGFACE_TOKEN", "")
    tokens = [t.strip() for t in tokens_str.split(",") if t.strip()]
    if not tokens: return None
    api_url = "https://api-inference.huggingface.co/models/damo-vilab/text-to-video-ms-1.7b"
    payload = {"inputs": prompt}
    for token in tokens:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(api_url, headers=headers, json=payload)
                if resp.status_code == 200: return resp.content
                elif resp.status_code == 429: continue
        except Exception: continue
    return None

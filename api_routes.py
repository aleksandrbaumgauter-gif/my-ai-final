import os, json, base64, asyncio, httpx, re, glob
from fastapi import Query, BackgroundTasks
from fastapi.responses import Response, StreamingResponse, HTMLResponse
from pydantic import BaseModel
from main import app, workers, registry, Status, jobs, get_youtube_transcript, generate_image_hf
from cache_db import cache
from pywebpush import webpush

# ВСТАВЬТЕ СЮДА ВАШ ПРИВАТНЫЙ КЛЮЧ (он уже вставлен)
VAPID_PRIVATE_KEY = """1d8e1w3z31PIXoSWqWQUQqUn6f1Z8""" 

class SummarizeRequest(BaseModel): history: list
class ImageRequest(BaseModel): prompt: str; image: str

@app.post("/summarize")
async def summarize(req: SummarizeRequest):
    if not req.history: return {"summary": "История пуста."}
    text = "\n".join([f"{m['role']}: {m['content']}" for m in req.history[-20:]])
    prompt = f"Сожми диалог в 150 слов.\n\n{text}"
    groq_keys = [k.strip() for k in os.getenv("GROQ_KEY", "").split(",") if k.strip()]
    for key in groq_keys:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 500})
                if r.status_code == 200: return {"summary": r.json()["choices"][0]["message"]["content"]}
        except Exception: pass
    return {"error": "Все ключи Groq заняты"}

@app.post("/analyze_image")
async def analyze_image(req: ImageRequest):
    key = os.getenv("GEMINI_KEY", "")
    if key:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}", json={"contents": [{"parts": [{"text": req.prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": req.image}}]}]})
                if r.status_code == 200: return {"text": f"👁️ **Gemini:** {r.json()['candidates'][0]['content']['parts'][0]['text']}"}
        except Exception: pass
    return {"text": "❌ Ошибка анализа фото."}

@app.post("/cache/clear")
async def cache_clear(): return {"deleted": cache.clear()}

@app.get("/cache/stats")
async def cache_stats(): return cache.stats()

@app.get("/stream")
async def stream(prompt: str = Query(...), cache_prompt: str = Query(None), skip_cache: bool = Query(False)):
    async def generator():
        check_prompt = cache_prompt if cache_prompt else prompt
        cached = cache.get(check_prompt, skip=skip_cache)
        if cached:
            yield f"data: {json.dumps({'type': 'token', 'provider': 'КЭШ', 'text': cached}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'provider': 'КЭШ', 'total': 0})}\n\n"
            return

        yt = re.search(r'(https?://[^\s]+)', prompt)
        if yt:
            url = yt.group(1); q = prompt.replace(url, "").strip() or "Опиши видео."
            watch_words = ["посмотри", "рассмотри", "что на видео", "визуально"]
            if any(w in prompt.lower() for w in watch_words):
                gemini_keys = [k.strip() for k in os.getenv("GEMINI_KEY", "").split(",") if k.strip()]
                for f in glob.glob("temp_video*"): os.remove(f)
                from main import download_video
                actual = download_video(url, "temp_video")
                if actual:
                    with open(actual, "rb") as f: vdata = base64.b64encode(f.read()).decode('utf-8')
                    for key in gemini_keys:
                        api = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
                        async with httpx.AsyncClient(timeout=120.0) as client:
                            r = await client.post(api, json={"contents": [{"parts": [{"text": q}, {"inline_data": {"mime_type": "video/mp4", "data": vdata}}]}]})
                            if r.status_code == 200:
                                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                                yield f"data: {json.dumps({'type': 'token', 'provider': 'GEMINI (Видео)', 'text': text}, ensure_ascii=False)}\n\n"
                                yield f"data: {json.dumps({'type': 'done', 'provider': 'GEMINI (Видео)', 'total': 0.0})}\n\n"
                                return
                yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '📝 Беру субтитры...'}, ensure_ascii=False)}\n\n"
            
            tr, err = get_youtube_transcript(url)
            if not err: prompt = f"Вопрос: '{q}'\n\nСубтитры:\n{tr}"
            else:
                yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': f'Ошибка: {err}'})}\n\n"; return

        queue = asyncio.Queue(); tasks = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for p in workers:
                if p.name == "GEMINI" or registry.get(p.name)["status"] != Status.GREEN.value: continue
                tasks.append(asyncio.create_task(p.stream(client, prompt, queue)))
            active = len(tasks)
            if active == 0:
                yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': 'Все заняты.'}, ensure_ascii=False)}\n\n"; return
            while active > 0:
                ev = await queue.get()
                if ev["type"] in ["done", "error"]:
                    active -= 1
                    if ev["type"] == "error": registry.on_error(ev["provider"])
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    return StreamingResponse(generator(), media_type="text/event-stream")

@app.get("/image")
async def image(prompt: str = Query(...)):
    d = await generate_image_hf(prompt)
    if not d: return Response(content="Ошибка", media_type="text/plain")
    return Response(content=d, media_type="image/jpeg")

@app.post("/subscribe")
async def subscribe(sub: dict):
    subs = []
    if os.path.exists("subs.json"):
        with open("subs.json", "r") as f: subs = json.load(f)
    if sub not in subs:
        subs.append(sub)
        with open("subs.json", "w") as f: json.dump(subs, f)
    return {"status": "ok"}

@app.post("/factory/start")
async def factory_start(bg: BackgroundTasks, prompt: str = Query(...), count: int = Query(3)):
    jid = jobs.create("factory"); bg.add_task(run_factory_job, jid, prompt, count); return {"job_id": jid}

async def run_factory_job(jid, prompt, count):
    imgs = []
    for i in range(count):
        b = await generate_image_hf(f"{prompt}, scene {i+1}")
        if b: imgs.append(base64.b64encode(b).decode('utf-8')); jobs.update(jid, result=imgs.copy())
        await asyncio.sleep(2)
    jobs.update(jid, status="done")
    
    try:
        if os.path.exists("subs.json"):
            with open("subs.json", "r") as f: subs = json.load(f)
            for sub in subs:
                try:
                    webpush(sub, data=json.dumps({"title": "🏭 Фабрика готова!", "body": "Картинки сгенерированы."}), vapid_private_key=VAPID_PRIVATE_KEY, vapid_claims={"sub": "mailto:test@test.com"})
                except Exception: pass
    except Exception: pass

@app.get("/factory/status/{jid}")
async def factory_status(jid: str):
    j = jobs.get(jid); return j if j else {"status": "not_found"}

@app.get("/ping")
async def ping(): return {"status": "ok"}

from frontend import HTML
@app.get("/")
async def index(): return HTMLResponse(HTML)

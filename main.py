import os, glob, json, base64, httpx, asyncio, re, uuid
from urllib.parse import urlparse, parse_qs
from enum import Enum
from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from cache_db import cache
import yt_dlp

load_dotenv()

# ================= ПРОВАЙДЕРЫ =================
PROVIDERS_CONFIG = [
    {"id": "groq", "name": "GROQ", "type": "openai_compat", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "api_key_env": "GROQ_KEY", "max_tokens": 1500, "priority": 1},
    {"id": "openrouter", "name": "OPENROUTER", "type": "openai_compat", "base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free", "api_key_env": "OPENROUTER_API_KEY", "max_tokens": 1500, "priority": 2},
    {"id": "deepseek", "name": "DEEPSEEK", "type": "openai_compat", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key_env": "DEEPSEEK_KEY", "max_tokens": 500, "priority": 3},
    {"id": "gemini", "name": "GEMINI", "type": "gemini_native", "model": "gemini-1.5-flash", "api_key_env": "GEMINI_KEY", "max_tokens": 500, "priority": 4},
    {"id": "mistral", "name": "MISTRAL", "type": "openai_compat", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "api_key_env": "MISTRAL_API_KEY", "max_tokens": 500, "priority": 5}
]

class Status(str, Enum): GREEN, RED = "green", "red"

class StateRegistry:
    def __init__(self): self._states = {}
    def register(self, name): self._states[name] = {"status": Status.GREEN}
    def get(self, name): return self._states.get(name, {"status": Status.GREEN})
    def on_error(self, name):
        if name in self._states: self._states[name]["status"] = Status.RED
        asyncio.get_event_loop().call_later(30, lambda: self._states.update({name: {"status": Status.GREEN}}))

class BaseProvider:
    def __init__(self, cfg): 
        self.cfg, self.name = cfg, cfg["name"]
        keys_str = os.getenv(cfg["api_key_env"], "")
        self.api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        self.current_key = self.api_keys[0] if self.api_keys else ""

    def switch_key(self):
        if self.current_key in self.api_keys:
            idx = self.api_keys.index(self.current_key)
            if idx + 1 < len(self.api_keys):
                self.current_key = self.api_keys[idx + 1]
                return True
        return False

class OpenAICompatProvider(BaseProvider):
    async def stream(self, client, prompt, queue):
        if not self.current_key: return await queue.put({"type": "error", "provider": self.name, "error": "Нет ключа"})
        models = ["meta-llama/llama-3.3-70b-instruct:free"] if self.cfg["id"] == "openrouter" else [self.cfg["model"]]
        for model_name in models:
            url = f"{self.cfg['base_url'].rstrip('/')}/chat/completions"
            payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": True, "max_tokens": self.cfg["max_tokens"]}
            try:
                async with client.stream("POST", url, headers={"Authorization": f"Bearer {self.current_key}"}, json=payload) as resp:
                    if resp.status_code != 200:
                        if resp.status_code in (429, 401, 403) and self.switch_key(): return await self.stream(client, prompt, queue)
                        continue
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line[6:] != "[DONE]":
                            text = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                            if text: await queue.put({"type": "token", "provider": self.name, "text": text})
                await queue.put({"type": "done", "provider": self.name, "total": 0})
                return
            except Exception: continue
        await queue.put({"type": "error", "provider": self.name, "error": "Ошибка сети"})

class GeminiNativeProvider(BaseProvider):
    async def stream(self, client, prompt, queue):
        if not self.current_key: return await queue.put({"type": "error", "provider": self.name, "error": "Нет ключа"})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.cfg['model']}:streamGenerateContent?alt=sse&key={self.current_key}"
        try:
            async with client.stream("POST", url, json={"contents": [{"parts": [{"text": prompt}]}]}) as resp:
                if resp.status_code != 200:
                    if resp.status_code in (429, 401, 403) and self.switch_key(): return await self.stream(client, prompt, queue)
                    raise Exception(f"Ошибка {resp.status_code}")
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        parts = json.loads(line[6:]).get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        if parts and parts[0].get("text"): await queue.put({"type": "token", "provider": self.name, "text": parts[0]["text"]})
            await queue.put({"type": "done", "provider": self.name, "total": 0})
        except Exception as e: await queue.put({"type": "error", "provider": self.name, "error": str(e)})

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
workers = [(OpenAICompatProvider if c["type"] == "openai_compat" else GeminiNativeProvider)(c) for c in sorted(PROVIDERS_CONFIG, key=lambda x: x["priority"])]
registry = StateRegistry()
for p in workers: registry.register(p.name)

# ================= ВИДЕО И СУБТИТРЫ =================
def get_youtube_transcript(url):
    try:
        query = urlparse(url).query
        video_id = parse_qs(query).get("v")
        if not video_id:
            if "youtu.be" in url: video_id = [url.split("/")[-1].split("?")[0]]
            else: return None, "Неверный формат ссылки"
        try:
            tl = YouTubeTranscriptApi.list_transcripts(video_id[0])
            data = tl.find_transcript(['ru', 'en']).fetch()
        except Exception: data = YouTubeTranscriptApi.get_transcript(video_id[0], languages=['ru', 'en'])
        return " ".join([t['text'] for t in data])[:15000], None
    except Exception as e: return None, str(e)

def download_video(url, prefix):
    try:
        opts = {'format': 'best[ext=mp4][filesize<15M]/best[filesize<15M]', 'outtmpl': prefix,
                'quiet': True, 'no_warnings': True, 'max_filesize': 15*1024*1024, 'socket_timeout': 10, 'retries': 2}
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
        files = glob.glob(f"{prefix}*"); return files[0] if files else None
    except Exception: return None

async def analyze_video_with_gemini(url, prompt):
    gemini_keys = [k.strip() for k in os.getenv("GEMINI_KEY", "").split(",") if k.strip()]
    if not gemini_keys:
        yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': 'Нет ключа Gemini.'})}\n\n"; return
    yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '⏳ Скачиваем видео...'}, ensure_ascii=False)}\n\n"
    for f in glob.glob("temp_video*"): os.remove(f)
    actual = download_video(url, "temp_video")
    if not actual:
        yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': 'Не удалось скачать видео (или >15МБ).'})}\n\n"; return
    yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '📤 Отправляем в Gemini...'}, ensure_ascii=False)}\n\n"
    try:
        with open(actual, "rb") as f: vdata = base64.b64encode(f.read()).decode('utf-8')
        mime = "video/mp4" if actual.endswith(".mp4") else "video/webm"
        
        for key in gemini_keys:
            api = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(api, json={"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": vdata}}]}]})
                if r.status_code == 200:
                    yield f"data: {json.dumps({'type': 'token', 'provider': 'GEMINI (Видео)', 'text': r.json()['candidates'][0]['content']['parts'][0]['text']}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'provider': 'GEMINI (Видео)', 'total': 0.0})}\n\n"; return
    except Exception: pass
    finally:
        for f in glob.glob("temp_video*"):
            try: os.remove(f)
            except: pass

# ================= ГЕНЕРАЦИЯ =================
async def generate_image_hf(prompt: str):
    hf_token = os.getenv("HUGGINGFACE_TOKEN", "")
    if not hf_token: return b""
    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(3):
            try:
                r = await client.post("https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell", headers={"Authorization": f"Bearer {hf_token}"}, json={"inputs": prompt})
                if r.status_code == 200: return r.content
                if r.status_code == 503: await asyncio.sleep(10); continue
            except Exception: await asyncio.sleep(5)
    return b""

class ImageRequest(BaseModel): prompt: str; image: str

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

# ================= СЖАТИЕ =================
class SummarizeRequest(BaseModel): history: list

@app.post("/summarize")
async def summarize(req: SummarizeRequest):
    if not req.history: return {"summary": "История пуста."}
    text = "\n".join([f"{m['role']}: {m['content']}" for m in req.history[-20:]])
    prompt = f"Сожми диалог в 150 слов. Сохрани все технические детали (языки, фреймворки, имена).\n\n{text}"
    groq_keys = [k.strip() for k in os.getenv("GROQ_KEY", "").split(",") if k.strip()]
    for key in groq_keys:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 500})
                if r.status_code == 200: return {"summary": r.json()["choices"][0]["message"]["content"]}
        except Exception: pass
    return {"error": "Все ключи Groq заняты"}

# ================= КЭШ =================
@app.post("/cache/clear")
async def cache_clear(): return {"deleted": cache.clear()}

@app.get("/cache/stats")
async def cache_stats(): return cache.stats()

# ================= СТРИМИНГ =================
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
            watch_words = ["посмотри", "рассмотри", "что на видео", "что на экране", "визуально"]
            if any(w in prompt.lower() for w in watch_words):
                async for chunk in analyze_video_with_gemini(url, q):
                    yield chunk
                    if '"type": "done"' in chunk:
                        text = json.loads(chunk.split("data: ")[1])["text"]
                        cache.set(check_prompt, text)
                return
            else:
                yield f"data: {json.dumps({'type': 'token', 'provider': 'Система', 'text': '📝 Беру субтитры...'}, ensure_ascii=False)}\n\n"
            
            tr, err = get_youtube_transcript(url)
            if not err: prompt = f"Вопрос: '{q}'\n\nСубтитры:\n{tr}"
            else:
                yield f"data: {json.dumps({'type': 'error', 'provider': 'Система', 'error': f'Ошибка: {err}'})}\n\n"; return

        queue = asyncio.Queue(); tasks = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for p in workers:
                if p.name == "GEMINI" or registry.get(p.name)["status"] != Status.GREEN: continue
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

# ================= ФАБРИКА =================
class JobManager:
    def __init__(self): self.jobs = {}
    def create(self, j_type):
        jid = str(uuid.uuid4()); self.jobs[jid] = {"status": "running", "result": [], "type": j_type}; return jid
    def update(self, jid, **kw):
        if jid in self.jobs: self.jobs[jid].update(kw)
    def get(self, jid): return self.jobs.get(jid)

jobs = JobManager()

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

@app.get("/factory/status/{jid}")
async def factory_status(jid: str):
    j = jobs.get(jid); return j if j else {"status": "not_found"}

@app.get("/ping")
async def ping(): return {"status": "ok"}

# ================= HTML =================
from frontend import HTML
@app.get("/")
async def index(): return HTMLResponse(HTML)

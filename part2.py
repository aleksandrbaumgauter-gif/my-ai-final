import os, sys, subprocess, time, re, base64, asyncio, hashlib, json, sqlite3, glob
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import urlparse, parse_qs
import httpx, uvicorn, yt_dlp
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi

load_dotenv()

PROVIDERS_CONFIG = [
    {"id": "groq", "name": "GROQ (Turbo)", "type": "openai_compat", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "api_key_env": "GROQ_KEY", "max_tokens": 500, "priority": 1, "enabled": True, "role": "worker"},
    {"id": "openrouter", "name": "OPENROUTER (Llama)", "type": "openai_compat", "base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free", "api_key_env": "OPENROUTER_API_KEY", "max_tokens": 500, "priority": 2, "enabled": True, "role": "worker"},
    {"id": "deepseek", "name": "DEEPSEEK", "type": "openai_compat", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key_env": "DEEPSEEK_KEY", "max_tokens": 500, "priority": 3, "enabled": True, "role": "worker"},
    {"id": "gemini", "name": "GEMINI", "type": "gemini_native", "model": "gemini-1.5-flash", "api_key_env": "GEMINI_KEY", "max_tokens": 500, "priority": 4, "enabled": True, "role": "worker"},
    {"id": "mistral", "name": "MISTRAL", "type": "openai_compat", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "api_key_env": "MISTRAL_API_KEY", "max_tokens": 500, "priority": 5, "enabled": True, "role": "worker"}
]

MAX_PARALLEL, LONG_COOLDOWN_SEC, DEFAULT_COOLDOWN = 2, 60.0, 30.0
CACHE_DB, CACHE_TTL_SEC = "cache.db", 24 * 3600

class ErrorKind(str, Enum):
    RATE_LIMIT, AUTH, SERVER, NETWORK, UNKNOWN = "rate_limit", "auth", "server", "network", "unknown"

class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, msg: str = "", retry_after: Optional[float] = None):
        self.kind, self.msg, self.retry_after = kind, msg, retry_after

def classify_http_error(resp: httpx.Response) -> ProviderError:
    if resp.status_code == 429:
        try: ra = float(resp.headers.get("retry-after", 30))
        except ValueError: ra = 30.0
        return ProviderError(ErrorKind.RATE_LIMIT, resp.text[:200], ra)
    if resp.status_code in (401, 403): return ProviderError(ErrorKind.AUTH, resp.text[:200])
    if 500 <= resp.status_code < 600: return ProviderError(ErrorKind.SERVER, resp.text[:200])
    return ProviderError(ErrorKind.UNKNOWN, resp.text[:200])

class Status(str, Enum):
    GREEN, YELLOW, RED, EXHAUSTED = "green", "yellow", "red", "exhausted"

@dataclass
class ProviderState:
    name: str
    status: Status = Status.GREEN
    cooldown_until: float = 0.0
    def is_available(self) -> bool:
        return self.status != Status.EXHAUSTED and self.cooldown_until <= time.monotonic()
    def on_success(self): self.status, self.cooldown_until = Status.GREEN, 0.0
    def on_error(self, kind: ErrorKind, retry_after: Optional[float]):
        if kind == ErrorKind.AUTH: self.status = Status.EXHAUSTED
        elif kind == ErrorKind.RATE_LIMIT:
            delay = retry_after or DEFAULT_COOLDOWN
            self.status = Status.EXHAUSTED if delay > LONG_COOLDOWN_SEC else Status.RED
            self.cooldown_until = time.monotonic() + delay
        else:
            self.status = Status.RED
            self.cooldown_until = time.monotonic() + DEFAULT_COOLDOWN

class StateRegistry:
    def __init__(self): self._states = {}
    def register(self, name): self._states.setdefault(name, ProviderState(name))
    def get(self, name): return self._states[name]

class Cache:
    def __init__(self, path=CACHE_DB, ttl=CACHE_TTL_SEC):
        self.ttl, self.conn = ttl, sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, answer TEXT, created_at INTEGER)")
        self.conn.commit(); self.conn.execute("DELETE FROM answers WHERE created_at < ?", (int(time.time()) - self.ttl,)); self.conn.commit()
    def make_key(self, prompt, model, max_t): return hashlib.sha256(f"{prompt}|{model}|{max_t}".encode()).hexdigest()
    def get(self, key):
        row = self.conn.execute("SELECT answer FROM answers WHERE key = ? AND created_at >= ?", (key, int(time.time()) - self.ttl)).fetchone()
        return row[0] if row else None
    def put(self, key, answer):
        self.conn.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?)", (key, answer, int(time.time()))); self.conn.commit()

class BaseProvider:
    def __init__(self, cfg): 
        self.cfg, self.name, self.role = cfg, cfg["name"], cfg.get("role", "worker")
        keys_str = os.getenv(cfg["api_key_env"], "")
        self.api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        self.current_key_idx = 0
        self.api_key = self.api_keys[0] if self.api_keys else ""

    def switch_key(self):
        if self.current_key_idx + 1 < len(self.api_keys):
            self.current_key_idx += 1
            self.api_key = self.api_keys[self.current_key_idx]
            return True
        return False

    async def _emit_no_key(self, queue): await queue.put({"type": "error", "provider": self.name, "error": "Нет ключа в .env", "kind": ErrorKind.AUTH.value})
    async def _emit_cached(self, queue, cached):
        await queue.put({"type": "ttft", "provider": self.name, "value": 0.0})
        for i in range(0, len(cached), 24): await queue.put({"type": "token", "provider": self.name, "text": cached[i:i+24]})
        await queue.put({"type": "done", "provider": self.name, "total": 0.0, "cached": True})

class OpenAICompatProvider(BaseProvider):
    async def stream(self, client, prompt, queue):
        if not self.api_key: return await self._emit_no_key(queue)
        models_to_try = []
        if self.cfg["id"] == "openrouter":
            models_to_try = ["meta-llama/llama-3.3-70b-instruct:free", "google/gemma-2-9b-it:free", "mistralai/mistral-7b-instruct:free"]
        else:
            models_to_try = [self.cfg["model"]]
        start, first = time.time(), None
        for model_name in models_to_try:
            url = f"{self.cfg['base_url'].rstrip('/')}/chat/completions"
            payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": True}
            try:
                async with client.stream("POST", url, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload) as resp:
                    if resp.status_code != 200:
                        print(f"⚠️ Модель {model_name} выдала ошибку {resp.status_code}, пробуем следующую...")
                        continue
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line[6:] != "[DONE]":
                            text = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                            if text:
                                if not first: first = time.time() - start; await queue.put({"type": "ttft", "provider": self.name, "value": first})
                                await queue.put({"type": "token", "provider": self.name, "text": text})
                await queue.put({"type": "done", "provider": self.name, "total": time.time() - start})
                return
            except Exception as e:
                print(f"⚠️ Ошибка сети для {model_name}: {e}")
                continue
        await queue.put({"type": "error", "provider": self.name, "error": "Все модели перегружены", "kind": ErrorKind.SERVER.value})

class GeminiNativeProvider(BaseProvider):
    async def stream(self, client, prompt, queue):
        if not self.api_key: return await self._emit_no_key(queue)
        url, start, first = f"https://generativelanguage.googleapis.com/v1beta/models/{self.cfg['model']}:streamGenerateContent?alt=sse&key={self.api_key}", time.time(), None
        try:
            async with client.stream("POST", url, json={"contents": [{"parts": [{"text": prompt}]}]}) as resp:
                if resp.status_code != 200: raise classify_http_error(resp)
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        parts = json.loads(line[6:]).get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        if parts and parts[0].get("text"):
                            text = parts[0]["text"]
                            if not first: first = time.time() - start; await queue.put({"type": "ttft", "provider": self.name, "value": first})
                            await queue.put({"type": "token", "provider": self.name, "text": text})
            await queue.put({"type": "done", "provider": self.name, "total": time.time() - start})
        except Exception as e:
            await queue.put({"type": "error", "provider": self.name, "error": str(e), "kind": e.kind.value if isinstance(e, ProviderError) else ErrorKind.NETWORK.value})

def build_providers():
    return [(OpenAICompatProvider if c["type"] == "openai_compat" else GeminiNativeProvider)(c) for c in sorted(PROVIDERS_CONFIG, key=lambda x: x["priority"]) if c.get("enabled", True)]

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
workers, registry, cache = [p for p in build_providers() if p.role == "worker"], StateRegistry(), Cache()
for p in build_providers(): registry.register(p.name)

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
        with yt_dlp.YoutubeDL({'format': 'worst[ext=mp4]/worst', 'outtmpl': out_path_prefix, 'quiet': True, 'no_warnings': True, 'max_filesize': 20 * 1024 * 1024}) as ydl: ydl.download([url])
        files = glob.glob(f"{out_path_prefix}*"); return files[0] if files else None
    except Exception: return None

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
                    tasks 

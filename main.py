import os, glob, json, base64, httpx, asyncio, re, uuid, time
from urllib.parse import urlparse, parse_qs
from enum import Enum
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

load_dotenv()

PROVIDERS_CONFIG = [
    {"id": "groq", "name": "GROQ", "type": "openai_compat", "base_url": "https://api.groq.com/openai/v1", 
     "models": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "llama3-8b-8192"], "api_key_env": "GROQ_KEY", "max_tokens": 1500, "priority": 1},
    {"id": "openrouter", "name": "OPENROUTER", "type": "openai_compat", "base_url": "https://openrouter.ai/api/v1", 
     "models": ["xingchen-agi/xing4.0-29b-a4b:free", "liquid/lfm-3-1.7b:free", "recursive/flint-v1.0:free"], "api_key_env": "OPENROUTER_API_KEY", "max_tokens": 1500, "priority": 2},
    {"id": "deepseek", "name": "DEEPSEEK", "type": "openai_compat", "base_url": "https://api.deepseek.com", 
     "models": ["deepseek-chat"], "api_key_env": "DEEPSEEK_KEY", "max_tokens": 500, "priority": 3},
    {"id": "gemini", "name": "GEMINI", "type": "gemini_native", 
     "models": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"], "api_key_env": "GEMINI_KEY", "max_tokens": 500, "priority": 4},
    {"id": "mistral", "name": "MISTRAL", "type": "openai_compat", "base_url": "https://api.mistral.ai/v1", 
     "models": ["mistral-small-latest", "open-mistral-7b"], "api_key_env": "MISTRAL_API_KEY", "max_tokens": 500, "priority": 5}
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
        self.dead_keys = {}
        self.current_model_index = 0

    def switch_key(self):
        now = time.time()
        self.dead_keys = {k: t for k, t in self.dead_keys.items() if now - t < 30}
        for key in self.api_keys:
            if key not in self.dead_keys:
                self.current_key = key
                return True
        return False

    def switch_model(self):
        if self.current_model_index < len(self.cfg["models"]) - 1:
            self.current_model_index += 1
            return True
        return False

    def mark_key_dead(self):
        if self.current_key: self.dead_keys[self.current_key] = time.time()

class OpenAICompatProvider(BaseProvider):
    async def stream(self, client, prompt, queue):
        if not self.current_key: return await queue.put({"type": "error", "provider": self.name, "error": "Нет ключа"})
        
        while True: # Бесконечный цикл, пока не получим ответ
            model_name = self.cfg["models"][self.current_model_index]
            url = f"{self.cfg['base_url'].rstrip('/')}/chat/completions"
            payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": True, "max_tokens": self.cfg["max_tokens"]}
            try:
                async with client.stream("POST", url, headers={"Authorization": f"Bearer {self.current_key}"}, json=payload) as resp:
                    if resp.status_code == 200:
                        async for line in resp.aiter_lines():
                            if line.startswith("data: ") and line[6:] != "[DONE]":
                                text = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                                if text: await queue.put({"type": "token", "provider": self.name, "text": text})
                        await queue.put({"type": "done", "provider": self.name, "total": 0})
                        return
                    
                    # Если ошибка 429 (лимит) — меняем ключ
                    if resp.status_code in (429, 401, 403):
                        self.mark_key_dead()
                        if self.switch_key(): continue # Пробуем заново с новым ключом
                    
                    # Если ошибка 404 (модель не найдена) — меняем модель
                    if resp.status_code in (404, 400):
                        if self.switch_model(): continue # Пробуем заново с новой моделью
                    
                    # Если ничего не помогло — выходим с ошибкой
                    await queue.put({"type": "error", "provider": self.name, "error": f"Ошибка {resp.status_code}"})
                    return
            except Exception: 
                # При сетевой ошибке пробуем дальше (без смены ключа/модели)
                await queue.put({"type": "error", "provider": self.name, "error": "Сетевая ошибка"})
                return

class GeminiNativeProvider(BaseProvider):
    async def stream(self, client, prompt, queue):
        if not self.current_key: return await queue.put({"type": "error", "provider": self.name, "error": "Нет ключа"})
        
        while True:
            model_name = self.cfg["models"][self.current_model_index]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?alt=sse&key={self.current_key}"
            try:
                async with client.stream("POST", url, json={"contents": [{"parts": [{"text": prompt}]}]}) as resp:
                    if resp.status_code == 200:
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                parts = json.loads(line[6:]).get("candidates", [{}])[0].get("content", {}).get("parts", [])
                                if parts and parts[0].get("text"): await queue.put({"type": "token", "provider": self.name, "text": parts[0]["text"]})
                        await queue.put({"type": "done", "provider": self.name, "total": 0})
                        return
                    
                    if resp.status_code in (429, 401, 403):
                        self.mark_key_dead()
                        if self.switch_key(): continue
                    
                    if resp.status_code in (404, 400):
                        if self.switch_model(): continue
                        
                    await queue.put({"type": "error", "provider": self.name, "error": f"Ошибка {resp.status_code}"})
                    return
            except Exception: 
                await queue.put({"type": "error", "provider": self.name, "error": "Сетевая ошибка"})
                return

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
workers = [(OpenAICompatProvider if c["type"] == "openai_compat" else GeminiNativeProvider)(c) for c in sorted(PROVIDERS_CONFIG, key=lambda x: x["priority"])]
registry = StateRegistry()
for p in workers: registry.register(p.name)

def get_youtube_transcript(url):
    try:
        query = urlparse(url).query
        video_id = parse_qs(query).get("v")
        if not video_id:
            if "youtu.be" in url: video_id = [url.split("/")[-1].split("?")[0]]
            else: return None, "Неверный формат ссылки"
        vid = video_id[0]
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(vid, languages=['ru', 'en'])
            if hasattr(transcript, 'text'): return transcript.text[:15000], None
            else: return " ".join([t.text for t in transcript])[:15000], None
        except AttributeError:
            try:
                tl = YouTubeTranscriptApi.list_transcripts(vid)
                data = tl.find_transcript(['ru', 'en']).fetch()
            except Exception:
                data = YouTubeTranscriptApi.get_transcript(vid, languages=['ru', 'en'])
            return " ".join([t['text'] for t in data])[:15000], None
    except Exception as e: return None, str(e)

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

class JobManager:
    def __init__(self): self.jobs = {}
    def create(self, j_type):
        jid = str(uuid.uuid4()); self.jobs[jid] = {"status": "running", "result": [], "type": j_type}; return jid
    def update(self, jid, **kw):
        if jid in self.jobs: self.jobs[jid].update(kw)
    def get(self, jid): return self.jobs.get(jid)

jobs = JobManager()

def download_video(url, prefix):
    try:
        opts = {'format': 'best[ext=mp4][filesize<15M]/best[filesize<15M]', 'outtmpl': prefix,
                'quiet': True, 'no_warnings': True, 'max_filesize': 15*1024*1024, 'socket_timeout': 10, 'retries': 2}
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
        files = glob.glob(f"{prefix}*"); return files[0] if files else None
    except Exception: return None
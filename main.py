import os, glob, json, base64, httpx, asyncio, re, uuid, time
from urllib.parse import urlparse, parse_qs
from enum import Enum
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
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

# ================= ВИДЕО И ГЕНЕРАЦИЯ =================
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

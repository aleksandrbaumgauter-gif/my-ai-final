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

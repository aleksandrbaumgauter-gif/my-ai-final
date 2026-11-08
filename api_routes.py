import os, json, base64, asyncio, httpx, re, glob
from fastapi import Query, BackgroundTasks
from fastapi.responses import Response, StreamingResponse, HTMLResponse
from pydantic import BaseModel
from main import app, workers, registry, Status, jobs, get_youtube_transcript, generate_image_hf
from cache_db import cache
from pywebpush import webpush

# ВСТАВЬТЕ СЮДА ВАШ ПРИВАТНЫЙ КЛЮЧ!
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

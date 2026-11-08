import os, json, asyncio, time, uuid, base64
from fastapi import Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from part1 import app, workers, registry
from part2 import download_video, generate_image_hf if hasattr(__import__('part2'), 'generate_image_hf') else None
from part3 import generate_image_hf
import httpx

# ==========================================
# 1. МЕНЕДЖЕР ЗАДАЧ (JOBS)
# ==========================================
class JobManager:
    def __init__(self):
        self.jobs = {}  # job_id -> {status, result, type}

    def create(self, job_type: str):
        jid = str(uuid.uuid4())
        self.jobs[jid] = {"status": "running", "result": [], "type": job_type, "created": time.time()}
        return jid

    def update(self, jid, **kwargs):
        if jid in self.jobs:
            self.jobs[jid].update(kwargs)

    def get(self, jid):
        return self.jobs.get(jid)

jobs = JobManager()

# ==========================================
# 2. ПИНГ (ЧТОБЫ СЕРВЕР НЕ СПАЛ)
# ==========================================
LAST_ACTIVITY = time.time()

@app.get("/ping")
async def ping():
    global LAST_ACTIVITY
    LAST_ACTIVITY = time.time()
    return {"status": "ok", "time": LAST_ACTIVITY}

# ==========================================
# 3. ФОНОВАЯ ФАБРИКА КАРТИНОК
# ==========================================
@app.post("/factory/start")
async def factory_start(background_tasks: BackgroundTasks, prompt: str = Query(...), count: int = Query(3)):
    jid = jobs.create("factory")
    background_tasks.add_task(run_factory_job, jid, prompt, count)
    return {"job_id": jid}

async def run_factory_job(jid, prompt, count):
    global LAST_ACTIVITY
    images = []
    for i in range(count):
        LAST_ACTIVITY = time.time() # Пинаем сервер изнутри
        img_bytes = await generate_image_hf(f"{prompt}, scene {i+1}, cinematic lighting")
        
        if img_bytes:
            b64_img = base64.b64encode(img_bytes).decode('utf-8')
            images.append(b64_img)
            jobs.update(jid, result=images.copy())
        
        # Искусственная задержка, чтобы не превысить лимиты API
        await asyncio.sleep(2)
        
    jobs.update(jid, status="done")
    # Здесь в будущем можно вызвать отправку Web Push!

@app.get("/factory/status/{jid}")
async def factory_status(jid: str):
    job = jobs.get(jid)
    if not job:
        return {"status": "not_found"}
    return job

# ==========================================
# 4. ФОНОВЫЙ АНАЛИЗ ВИДЕО
# ==========================================
@app.post("/video/start")
async def video_start(background_tasks: BackgroundTasks, url: str = Query(...), prompt: str = Query("")):
    jid = jobs.create("video")
    background_tasks.add_task(run_video_job, jid, url, prompt)
    return {"job_id": jid}

async def run_video_job(jid, url, prompt):
    global LAST_ACTIVITY
    # 1. Скачиваем
    jobs.update(jid, status="downloading")
    actual_file = download_video(url, f"temp_{jid}")
    if not actual_file:
        jobs.update(jid, status="error", result=["Не удалось скачать видео"])
        return

    # 2. Отправляем в Gemini
    jobs.update(jid, status="analyzing")
    gemini_key = os.getenv("GEMINI_KEY", "")
    if not gemini_key:
        jobs.update(jid, status="error", result=["Нет ключа Gemini"])
        return

    try:
        with open(actual_file, "rb") as f:
            video_data = base64.b64encode(f.read()).decode('utf-8')
        
        mime = "video/mp4" if actual_file.endswith(".mp4") else "video/webm"
        url_api = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url_api, json={"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": video_data}}]}]})
            if resp.status_code == 200:
                text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                jobs.update(jid, status="done", result=[text])
            else:
                jobs.update(jid, status="error", result=[f"Ошибка Gemini: {resp.status_code}"])
    except Exception as e:
        jobs.update(jid, status="error", result=[str(e)])
    finally:
        if os.path.exists(actual_file):
            os.remove(actual_file)
import os
import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

async def fetch_page_text(url: str):
    """Парсит текст с сайта для глубокого анализа"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.decompose()
                text = soup.get_text(separator=' ', strip=True)
                return text[:4000]
    except Exception:
        pass
    return "Не удалось прочитать текст со страницы."

async def universal_search(query: str, deep_read: bool = False):
    """Умный поиск: Google -> DuckDuckGo -> Парсинг сайта"""
    google_key = os.getenv("GOOGLE_API_KEY")
    cse_id = os.getenv("GOOGLE_CSE_ID")
    results_data = []
    
    # 1. Пробуем Google
    if google_key and cse_id:
        try:
            url = "https://www.googleapis.com/customsearch/v1"
            params = {"key": google_key, "cx": cse_id, "q": query, "num": 3}
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
                if r.status_code == 200:
                    for item in r.json().get("items", []):
                        results_data.append({"title": item.get('title'), "snippet": item.get('snippet'), "url": item.get('link')})
        except Exception: pass

    # 2. Если Google не дал результатов — DuckDuckGo
    if not results_data:
        try:
            # Добавлен region для лучшего поиска на русском
            ddg_results = DDGS().text(query, max_results=3, region='ru-ru')
            for r in ddg_results:
                results_data.append({"title": r.get('title'), "snippet": r.get('body'), "url": r.get('href')})
        except Exception: pass

    if not results_data:
        return "Ничего не найдено."

    # 3. Глубокое чтение (парсинг первого сайта)
    deep_text = ""
    if deep_read and results_data:
        first_url = results_data[0]["url"]
        deep_text = await fetch_page_text(first_url)
        deep_text = f"\n\n📖 **[Детальный текст с сайта]**\n{deep_text}\n"

    # Формируем ответ
    final_text = f"🌐 **Результаты поиска по запросу: «{query}»**\n\n"
    for i, res in enumerate(results_data):
        final_text += f"**[{i+1}] {res['title']}**\n{res['snippet']}\n🔗 {res['url']}\n\n"
    
    return final_text + deep_text
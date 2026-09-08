from fastapi import APIRouter, Body
import logging
logger = logging.getLogger(__name__)
import requests
from app.core.config import settings
from app.services.ai import call_ai_intelligence

router = APIRouter(prefix="/intelligence", tags=["Intelligence"])

@router.post("/ask")
async def ask(data: dict = Body(...)):
    question = (data.get("question") or "").strip()

    if not question:
        return {"error": "Question is required"}


    search_results = intelligence_search(question)

    prompt = f"""
You are OS AI Intelligence.

Answer with:
1. Direct answer
2. Evidence
3. Sources
4. Confidence

Be precise, analytical and globally aware, with strong African context.
Search evidence:\n{chr(10).join([x["title"] + " | " + x["url"] + "\n" + x["evidence"] for x in search_results])}

Question:
{question}
"""

    messages = [{"role": "user", "content": prompt}]
    content, model = await call_ai_intelligence(messages)

    return {
        "answer": content,
        "model": model,
        "evidence": [x["evidence"] for x in search_results],
        "sources": [{"title": x["title"], "url": x["url"]} for x in search_results],
        "confidence": "standard",
    }


def intelligence_search(query):
    if not settings.TAVILY_API_KEY:
        return []
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.TAVILY_API_KEY, "query": query, "max_results": 5},
            timeout=15,
        )
        if r.status_code == 200:
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "evidence": x.get("content", "")}
                for x in r.json().get("results", [])
            ]
    except Exception as e:
        logger.error(f"Intelligence search error: {e}")
    return []

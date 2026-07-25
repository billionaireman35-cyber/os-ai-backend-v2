from fastapi import APIRouter, Request
import requests
from app.core.config import settings
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/news")
async def get_news():
    if not settings.NEWS_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"category": "business", "language": "en", "pageSize": 10, "apiKey": settings.NEWS_API_KEY},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("articles", [])
    except Exception as e:
        logger.error(f"News error: {e}")
    return []

@router.get("/search")
async def search_tokens(request: Request):
    query = request.query_params.get("q", "").strip()
    if len(query) < 2:
        return {"results": []}
    results = []
    if settings.COINGECKO_KEY:
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": query},
                headers={"x-cg-demo-api-key": settings.COINGECKO_KEY},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                for coin in data.get("coins", [])[:10]:
                    results.append({
                        "type": "token",
                        "name": coin.get("name", ""),
                        "symbol": coin.get("symbol", "").upper(),
                        "id": coin.get("id", "")
                    })
        except:
            pass
    return {"results": results}

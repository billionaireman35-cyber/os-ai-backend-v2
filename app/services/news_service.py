import requests
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

def get_crypto_news(query: str = "crypto", limit: int = 10):
    """Fetch crypto news from GNews or NewsAPI."""
    # Try GNews first
    if settings.GNEWS_API_KEY:
        try:
            url = "https://gnews.io/api/v4/search"
            params = {
                "q": query,
                "token": settings.GNEWS_API_KEY,
                "max": limit,
                "lang": "en"
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                articles = data.get("articles", [])
                return [
                    {
                        "title": art.get("title"),
                        "description": art.get("description"),
                        "url": art.get("url"),
                        "image": art.get("image"),
                        "publishedAt": art.get("publishedAt"),
                        "source": art.get("source", {}).get("name", "GNews")
                    }
                    for art in articles
                ]
        except Exception as e:
            logger.error(f"GNews error: {e}")

    # Fallback to NewsAPI
    if settings.NEWS_API_KEY:
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": "cryptocurrency OR bitcoin OR ethereum",
                "apiKey": settings.NEWS_API_KEY,
                "pageSize": limit,
                "language": "en",
                "sortBy": "publishedAt"
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                articles = data.get("articles", [])
                return [
                    {
                        "title": art.get("title"),
                        "description": art.get("description"),
                        "url": art.get("url"),
                        "image": art.get("urlToImage"),
                        "publishedAt": art.get("publishedAt"),
                        "source": art.get("source", {}).get("name", "NewsAPI")
                    }
                    for art in articles if art.get("title")
                ]
        except Exception as e:
            logger.error(f"NewsAPI error: {e}")

    logger.warning("No news API keys configured")
    return []

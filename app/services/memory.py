import uuid
import json
import requests
from app.core.database import get_db
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def get_embedding(text: str) -> list:
    if settings.OPENAI_API_KEY:
        try:
            resp = requests.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={"model": settings.EMBEDDING_MODEL, "input": text},
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()["data"][0]["embedding"]
        except Exception as e:
            logger.error(f"OpenAI embedding error: {e}")
    # Fallback: random vector
    import random
    return [random.uniform(-1, 1) for _ in range(1536)]

def store_memory(user_id: str, content: str, query: str) -> None:
    if not user_id or not content:
        return
    try:
        embedding = get_embedding(content)
        memory_id = f"mem_{uuid.uuid4().hex[:8]}"
        domain = classify_domain(query)
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO memories (id, user_id, content, query, domain, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (memory_id, user_id, content[:1000], query[:500], domain, embedding))
                conn.commit()
    except Exception as e:
        logger.error(f"Memory store error: {e}")

def get_memories(user_id: str, query: str, limit: int = 5) -> str:
    if not user_id:
        return ""
    try:
        query_embedding = get_embedding(query)
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    SELECT content, query
                    FROM memories
                    WHERE user_id = %s
                    ORDER BY embedding <-> %s::vector
                    LIMIT %s
                """, (user_id, query_embedding, limit))
                rows = c.fetchall()
                if rows:
                    memories = [f"User previously asked: {row[1]} → AI replied: {row[0][:200]}..." for row in rows]
                    return "\n\n".join(memories)
    except Exception as e:
        logger.error(f"Memory retrieval error: {e}")
    return ""

def classify_domain(query: str) -> str:
    import re
    domains = {
        r'def |class |import |docker|kubernetes|aws|api|sql': 'coding',
        r'stock|trading|crypto|bitcoin|forex|markets': 'finance',
        r'prove|theorem|integral|derivative|matrix': 'math',
        r'quantum|physics|chemistry|biology|medicine': 'science',
        r'un|wto|imf|world bank|policy|election': 'geopolitics',
        r'painting|sculpture|design|music|composition': 'arts',
        r'recipe|cook|cuisine|nutrition|bake': 'food',
        r'god|religion|faith|prayer|church|mosque': 'religion',
        r'travel|hotel|flight|vacation|tourism': 'travel',
    }
    for pattern, domain in domains.items():
        if re.search(pattern, query.lower()):
            return domain
    return 'general'

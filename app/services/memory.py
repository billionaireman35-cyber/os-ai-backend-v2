import logging
import uuid
from app.core.database import get_db

logger = logging.getLogger(__name__)

def store_memory(user_id: str, content: str, query: str, domain: str = "general", importance: int = 1):
    try:
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO memories (id, user_id, content, query, domain, importance)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (str(uuid.uuid4()), user_id, content, query, domain, importance))
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to store memory: {e}")

def get_memories(user_id: str, query: str, limit: int = 5) -> str:
    try:
        with get_db() as conn:
            with conn.cursor() as c:
                # Fallback: just get recent memories (no vector search to avoid errors)
                c.execute("""
                    SELECT content FROM memories
                    WHERE user_id = %s
                    ORDER BY created DESC
                    LIMIT %s
                """, (user_id, limit))
                rows = c.fetchall()
                if rows:
                    return "\n".join([row[0] for row in rows])
                return ""
    except Exception as e:
        logger.error(f"Memory retrieval error: {e}")
        return ""

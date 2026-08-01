from fastapi.responses import JSONResponse, StreamingResponse
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from app.models.schemas import ChatRequest
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.ai import call_ai_model, moderate_content, build_system_prompt, search_web
from app.services.memory import get_memories, store_memory
from app.services.transaction_parser import parse_transaction_intent
from app.tasks.burn_worker import process_burn_task
from app.core.config import settings
import uuid, logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/")
async def chat_endpoint(req: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    user = get_current_user(request)
    model = req.model or None

    user_msg = None
    for m in reversed(req.messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    if not user_msg:
        raise HTTPException(400, "No message content")
    
    tx_intent = parse_transaction_intent(user_msg)
    if tx_intent:
        return {
            "type": "transaction",
            "tx_intent": tx_intent,
            "message": f"I detected you want to {tx_intent['action']} {tx_intent['amount']} {tx_intent['token']} on {tx_intent['chain']}. Please confirm."
        }
    
    is_flagged, reason, _ = moderate_content(user_msg)
    if is_flagged:
        raise HTTPException(400, f"Message blocked: {reason}")
    
    chat_id = req.chat_id or f"chat_{uuid.uuid4().hex[:8]}"
    
    if not user:
        return {
            "content": "Hello! I'm OS AI. Please sign up to access my full capabilities, including long-term memory and transaction support. 🚀",
            "chat_id": chat_id,
            "requires_auth": True
        }
    
    user_id = user["id"]
    close_balance = user.get("close_balance", 0)
    
    if close_balance < settings.BURN_PER_MESSAGE:
        return {
            "content": "Insufficient CLOSE. Please top up to continue using the AI.",
            "requires_purchase": True,
            "close_balance": close_balance
        }
    
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE users SET close_balance = close_balance - %s WHERE id = %s", (settings.BURN_PER_MESSAGE, user_id))
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, "burn", settings.BURN_PER_MESSAGE, "pending"))
            c.execute("""
                INSERT INTO chats (id, user_id, title) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET updated = NOW()
            """, (chat_id, user_id, user_msg[:60]))
            c.execute("""
                INSERT INTO chat_messages (id, chat_id, user_id, role, content)
                VALUES (%s, %s, %s, %s, %s)
            """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "user", user_msg))
            conn.commit()
    
    memory_context = get_memories(user_id, user_msg, settings.MEMORY_RETRIEVAL_LIMIT)
    web_results = ""
    if any(kw in user_msg.lower() for kw in ["latest", "today", "news", "current", "recent"]):
        web_results = search_web(user_msg)
    
    system_prompt = build_system_prompt(user_msg, user, memory_context, web_results)
    messages_for_ai = [{"role": "system", "content": system_prompt}] + req.messages
    
    response, model = call_ai_model(messages_for_ai, user_id)
    
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO chat_messages (id, chat_id, user_id, role, content, model, close_burned)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "assistant", response, model, settings.BURN_PER_MESSAGE))
            conn.commit()
    
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE close_transactions
                SET status = 'completed', tx_hash = 'pending'
                WHERE user_id = %s AND type = 'burn' AND status = 'pending'
                ORDER BY created DESC LIMIT 1
            """, (user_id,))
            conn.commit()
    
    new_balance = close_balance - settings.BURN_PER_MESSAGE
    
    return {
        "content": response,
        "chat_id": chat_id,
        "model": model,
        "close_balance": new_balance,
        "close_burned": settings.BURN_PER_MESSAGE,
        "memory_used": bool(memory_context)
    }

@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    """Stream AI response token by token."""
    user = get_current_user(request)
    user_msg = None
    for m in reversed(req.messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    if not user_msg:
        raise HTTPException(400, "No message content")

    # Check for transaction intent (non‑streaming)
    tx_intent = parse_transaction_intent(user_msg)
    if tx_intent:
        return JSONResponse(
            status_code=200,
            content={
                "type": "transaction",
                "tx_intent": tx_intent,
                "message": f"I detected you want to {tx_intent['action']} {tx_intent['amount']} {tx_intent['token']} on {tx_intent['chain']}. Please confirm."
            }
        )

    # Moderate content
    is_flagged, reason, _ = moderate_content(user_msg)
    if is_flagged:
        raise HTTPException(400, f"Message blocked: {reason}")

    chat_id = req.chat_id or f"chat_{uuid.uuid4().hex[:8]}"

    # Guest flow
    if not user:
        return JSONResponse(
            status_code=200,
            content={
                "content": "Hello! I'm OS AI. Please sign up to access my full capabilities.",
                "chat_id": chat_id,
                "requires_auth": True
            }
        )

    user_id = user["id"]
    close_balance = user.get("close_balance", 0)

    # Check balance
    if close_balance < settings.BURN_PER_MESSAGE:
        return JSONResponse(
            status_code=200,
            content={
                "content": "Insufficient CLOSE. Please top up to continue.",
                "requires_purchase": True,
                "close_balance": close_balance
            }
        )

    # Deduct CLOSE optimistically and store user message
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE users SET close_balance = close_balance - %s WHERE id = %s", (settings.BURN_PER_MESSAGE, user_id))
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, "burn", settings.BURN_PER_MESSAGE, "pending"))
            c.execute("""
                INSERT INTO chats (id, user_id, title) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET updated = NOW()
            """, (chat_id, user_id, user_msg[:60]))
            c.execute("""
                INSERT INTO chat_messages (id, chat_id, user_id, role, content)
                VALUES (%s, %s, %s, %s, %s)
            """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "user", user_msg))
            conn.commit()

    # Retrieve memory and web results
    memory_context = get_memories(user_id, user_msg, settings.MEMORY_RETRIEVAL_LIMIT)
    web_results = ""
    if any(kw in user_msg.lower() for kw in ["latest", "today", "news", "current", "recent"]):
        web_results = search_web(user_msg)

    system_prompt = build_system_prompt(user_msg, user, memory_context, web_results)
    messages_for_ai = [{"role": "system", "content": system_prompt}] + req.messages

    tier = user.get("stake_tier", "guest")
    model = req.model or None

    # Define the generator that yields SSE events
    from fastapi.responses import StreamingResponse
    import json

    async def generate():
        accumulated = []
        async for chunk in call_ai_model_stream(messages_for_ai, user_id, model, tier):
            accumulated.append(chunk)
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        # After stream ends, store complete message in DB
        full_response = "".join(accumulated)
        if full_response:
            with get_db() as conn:
                with conn.cursor() as c:
                    c.execute("""
                        INSERT INTO chat_messages (id, chat_id, user_id, role, content, model, close_burned)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "assistant", full_response, "streamed", settings.BURN_PER_MESSAGE))
                    conn.commit()
            # Update burn transaction status
            with get_db() as conn:
                with conn.cursor() as c:
                    c.execute("""
                        UPDATE close_transactions
                        SET status = 'completed', tx_hash = 'streamed'
                        WHERE user_id = %s AND type = 'burn' AND status = 'pending'
                        ORDER BY created DESC LIMIT 1
                    """, (user_id,))
                    conn.commit()
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@router.get("/chats")
async def get_chats(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, title, created, updated
                FROM chats
                WHERE user_id = %s
                ORDER BY updated DESC
            """, (user["id"],))
            rows = c.fetchall()
            return [
                {
                    "id": row[0],
                    "title": row[1],
                    "created": row[2].isoformat(),
                    "updated": row[3].isoformat()
                }
                for row in rows
            ]

@router.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT user_id FROM chats WHERE id = %s", (chat_id,))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Chat not found")
            if row[0] != user["id"]:
                raise HTTPException(403, "Access denied")
            c.execute("""
                SELECT id, role, content, created
                FROM chat_messages
                WHERE chat_id = %s
                ORDER BY created ASC
            """, (chat_id,))
            rows = c.fetchall()
            return [
                {
                    "id": row[0],
                    "role": row[1],
                    "content": row[2],
                    "created": row[3].isoformat()
                }
                for row in rows
            ]

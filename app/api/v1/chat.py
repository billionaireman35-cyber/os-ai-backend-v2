from fastapi.responses import JSONResponse, StreamingResponse
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from app.models.schemas import ChatRequest
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.ai import call_ai_model, moderate_content, build_system_prompt, search_web, call_ai_model_stream
from app.services.memory import get_memories, store_memory
from app.services.transaction_parser import parse_transaction_intent
from app.tasks.burn_worker import process_burn_task
from app.services.blockchain import burn_close, get_effective_burn_amount
from app.core.config import settings
import uuid, logging, json, traceback

router = APIRouter()
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Non‑streaming chat endpoint
# ------------------------------------------------------------------------------
@router.post("/")
async def chat_endpoint(
    req: ChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user)
):
    try:
        model = req.model or None
        user_msg = None
        for m in reversed(req.messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break
        if not user_msg:
            raise HTTPException(400, "No message content")

        # Transaction intent
        tx_intent = parse_transaction_intent(user_msg)
        if tx_intent:
            return {
                "type": "transaction",
                "tx_intent": tx_intent,
                "message": f"I detected you want to {tx_intent['action']} {tx_intent['amount']} {tx_intent['token']} on {tx_intent['chain']}. Please confirm."
            }

        # Moderation
        is_flagged, reason, _ = moderate_content(user_msg)
        if is_flagged:
            raise HTTPException(400, f"Message blocked: {reason}")

        chat_id = req.chat_id or f"chat_{uuid.uuid4().hex[:8]}"

        # Guest flow
        if not user:
            return {
                "content": "Hello! I'm OS AI. Please sign up to access my full capabilities.",
                "chat_id": chat_id,
                "requires_auth": True
            }

        user_id = user["id"]
        wallet_address = user.get("wallet_address")
        effective_burn = get_effective_burn_amount(wallet_address, settings.BURN_PER_MESSAGE)
        burn_tx_id = str(uuid.uuid4())

        # Atomic balance check + deduct
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute(
                    "UPDATE users SET close_balance = close_balance - %s WHERE id = %s AND close_balance >= %s",
                    (effective_burn, user_id, effective_burn)
                )
                if c.rowcount == 0:
                    return JSONResponse(
                        status_code=402,
                        content={
                            "content": "Insufficient CLOSE balance. Please top up.",
                            "requires_purchase": True,
                            "close_balance": user.get("close_balance", 0)
                        }
                    )
                c.execute("""
                    INSERT INTO close_transactions (id, user_id, type, amount, status)
                    VALUES (%s, %s, %s, %s, %s)
                """, (burn_tx_id, user_id, "burn", effective_burn, "pending"))
                c.execute("""
                    INSERT INTO chats (id, user_id, title) VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET updated = NOW()
                """, (chat_id, user_id, user_msg[:60]))
                c.execute("""
                    INSERT INTO chat_messages (id, chat_id, user_id, role, content)
                    VALUES (%s, %s, %s, %s, %s)
                """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "user", user_msg))
                conn.commit()

        # Memory and web
        memory_context = ""
        try:
            memory_context = get_memories(user_id, user_msg, settings.MEMORY_RETRIEVAL_LIMIT)
        except Exception as e:
            logger.error(f"Memory retrieval failed: {e}")

        web_results = ""
        if any(kw in user_msg.lower() for kw in ["latest", "today", "news", "current", "recent"]):
            try:
                web_results = search_web(user_msg)
            except Exception as e:
                logger.error(f"Web search failed: {e}")

        system_prompt = build_system_prompt(user_msg, user, memory_context, web_results)
        messages_for_ai = [{"role": "system", "content": system_prompt}] + req.messages

        ai_success = False
        response = ""
        model_used = "fallback"
        try:
            response, model_used = call_ai_model(messages_for_ai, user_id)
            ai_success = True
        except Exception as e:
            logger.error(f"AI call failed: {e}\n{traceback.format_exc()}")
            response = "I'm sorry, I encountered an error. Please try again later."
            model_used = "error"

        # Finalize transaction
        with get_db() as conn:
            with conn.cursor() as c:
                if ai_success:
                    c.execute("""
                        INSERT INTO chat_messages (id, chat_id, user_id, role, content, model, close_burned)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "assistant", response, model_used, effective_burn))
                    try:
                        tx_hash = burn_close(effective_burn)
                        c.execute("""
                            UPDATE close_transactions SET status = 'completed', tx_hash = %s
                            WHERE id = %s
                        """, (tx_hash, burn_tx_id))
                    except Exception as e:
                        logger.error(f"On-chain burn failed: {e}")
                        c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (effective_burn, user_id))
                        c.execute("""
                            UPDATE close_transactions SET status = 'failed', tx_hash = 'burn_error'
                            WHERE id = %s
                        """, (burn_tx_id,))
                else:
                    c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (effective_burn, user_id))
                    c.execute("""
                        UPDATE close_transactions SET status = 'failed'
                        WHERE id = %s
                    """, (burn_tx_id,))

                c.execute("SELECT close_balance FROM users WHERE id = %s", (user_id,))
                new_balance = c.fetchone()[0]
                conn.commit()

        return {
            "content": response,
            "chat_id": chat_id,
            "model": model_used,
            "close_balance": new_balance,
            "close_burned": effective_burn if ai_success else 0,
            "memory_used": bool(memory_context),
            "success": ai_success
        }
    except Exception as e:
        error_detail = str(e) + "\n" + traceback.format_exc()
        logger.error(f"Unhandled exception: {error_detail}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "trace": traceback.format_exc().split("\n")}
        )

# ------------------------------------------------------------------------------
# Streaming chat endpoint (fully functional)
# ------------------------------------------------------------------------------
@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user)
):
    try:
        user_msg = None
        for m in reversed(req.messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break
        if not user_msg:
            raise HTTPException(400, "No message content")

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

        is_flagged, reason, _ = moderate_content(user_msg)
        if is_flagged:
            raise HTTPException(400, f"Message blocked: {reason}")

        chat_id = req.chat_id or f"chat_{uuid.uuid4().hex[:8]}"

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
        wallet_address = user.get("wallet_address")
        effective_burn = get_effective_burn_amount(wallet_address, settings.BURN_PER_MESSAGE)
        burn_tx_id = str(uuid.uuid4())

        # Atomic balance check + deduct
        with get_db() as conn:
            with conn.cursor() as c:
                c.execute(
                    "UPDATE users SET close_balance = close_balance - %s WHERE id = %s AND close_balance >= %s",
                    (effective_burn, user_id, effective_burn)
                )
                if c.rowcount == 0:
                    return JSONResponse(
                        status_code=402,
                        content={
                            "content": "Insufficient CLOSE balance. Please top up.",
                            "requires_purchase": True,
                            "close_balance": user.get("close_balance", 0)
                        }
                    )
                c.execute("""
                    INSERT INTO close_transactions (id, user_id, type, amount, status)
                    VALUES (%s, %s, %s, %s, %s)
                """, (burn_tx_id, user_id, "burn", effective_burn, "pending"))
                c.execute("""
                    INSERT INTO chats (id, user_id, title) VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET updated = NOW()
                """, (chat_id, user_id, user_msg[:60]))
                c.execute("""
                    INSERT INTO chat_messages (id, chat_id, user_id, role, content)
                    VALUES (%s, %s, %s, %s, %s)
                """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "user", user_msg))
                conn.commit()

        # Memory and web
        memory_context = ""
        try:
            memory_context = get_memories(user_id, user_msg, settings.MEMORY_RETRIEVAL_LIMIT)
        except Exception as e:
            logger.error(f"Memory retrieval failed: {e}")

        web_results = ""
        if any(kw in user_msg.lower() for kw in ["latest", "today", "news", "current", "recent"]):
            try:
                web_results = search_web(user_msg)
            except Exception as e:
                logger.error(f"Web search failed: {e}")

        system_prompt = build_system_prompt(user_msg, user, memory_context, web_results)
        messages_for_ai = [{"role": "system", "content": system_prompt}] + req.messages

        tier = user.get("stake_tier", "guest")
        model = req.model or None
        model_store = []

        async def generate():
            accumulated = []
            ai_success = False
            try:
                async for chunk in call_ai_model_stream(messages_for_ai, user_id, model, tier, model_store):
                    accumulated.append(chunk)
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
                ai_success = True
            except Exception as e:
                logger.error(f"Streaming AI call failed: {e}\n{traceback.format_exc()}")
                error_msg = "I'm sorry, I encountered an error. Please try again."
                yield f"data: {json.dumps({'content': error_msg})}\n\n"
                accumulated.append(error_msg)
                ai_success = False
            finally:
                full_response = "".join(accumulated)
                model_used = model_store[0] if model_store else "streamed"
                with get_db() as conn:
                    with conn.cursor() as c:
                        if ai_success and full_response:
                            c.execute("""
                                INSERT INTO chat_messages (id, chat_id, user_id, role, content, model, close_burned)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """, (f"msg_{uuid.uuid4().hex[:8]}", chat_id, user_id, "assistant", full_response, model_used, effective_burn))
                            try:
                                tx_hash = burn_close(effective_burn)
                                c.execute("""
                                    UPDATE close_transactions SET status = 'completed', tx_hash = %s
                                    WHERE id = %s
                                """, (tx_hash, burn_tx_id))
                            except Exception as e:
                                logger.error(f"On-chain burn failed: {e}")
                                c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (effective_burn, user_id))
                                c.execute("""
                                    UPDATE close_transactions SET status = 'failed', tx_hash = 'burn_error'
                                    WHERE id = %s
                                """, (burn_tx_id,))
                        else:
                            c.execute("UPDATE users SET close_balance = close_balance + %s WHERE id = %s", (effective_burn, user_id))
                            c.execute("""
                                UPDATE close_transactions SET status = 'failed'
                                WHERE id = %s
                            """, (burn_tx_id,))
                        conn.commit()
                yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")
    except Exception as e:
        error_detail = str(e) + "\n" + traceback.format_exc()
        logger.error(f"Unhandled exception in chat_stream: {error_detail}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "trace": traceback.format_exc().split("\n")}
        )

# ------------------------------------------------------------------------------
# Chat history endpoints (unchanged)
# ------------------------------------------------------------------------------
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

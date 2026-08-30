from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, Body
from app.models.schemas import ChatRequest
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.ai import (
    call_ai_model, moderate_content, build_system_prompt, search_web,
    call_ai_model_stream, extract_text, build_vision_content, VISION_MODELS,
    DEFAULT_MODELS, TIER_MODEL_ACCESS, DAILY_MESSAGE_LIMITS,
)
from app.services.memory import get_memories, store_memory
from app.services.transaction_parser import parse_transaction_intent
from app.tasks.burn_worker import process_burn_task
from app.services.blockchain import burn_close, get_effective_burn_amount
from app.core.config import settings
import uuid, logging, json, traceback

router = APIRouter()
logger = logging.getLogger(__name__)

# Fraction of each chat-message burn redirected to fund staking yield
# instead of being destroyed. 15% -> staking_treasury_funding (swept to
# the staking treasury in a batch later), 85% -> actually burned via
# burn_close(). See staking_treasury_funding table / founder-suite sweep
# endpoint for the other half of this mechanism.
STAKING_FUNDING_SPLIT = 0.15


def burn_with_staking_split(effective_burn: int, chat_message_id: str, conn_cursor) -> str:
    """Splits effective_burn 85/15: burns the 85% portion on-chain via
    burn_close(), and records the 15% portion as an unswept row in
    staking_treasury_funding (using the already-open cursor, same
    transaction as the rest of the finalize step - if the DB commit
    rolls back, the funding row rolls back with it). Returns the burn
    tx_hash from the actually-burned portion; the 15% isn't sent
    anywhere yet, just tracked for the next batch sweep."""
    staking_portion = int(effective_burn * STAKING_FUNDING_SPLIT)
    burn_portion = effective_burn - staking_portion

    tx_hash = burn_close(burn_portion)

    if staking_portion > 0:
        conn_cursor.execute("""
            INSERT INTO staking_treasury_funding (id, amount, source, chat_message_id)
            VALUES (%s, %s, %s, %s)
        """, (str(uuid.uuid4()), staking_portion, 'chat_burn_split', chat_message_id))

    return tx_hash


def check_daily_message_limit(user_id, tier: str, conn_cursor) -> None:
    """Raises HTTPException(429) if the user has hit their tier's daily
    message cap. Counts assistant messages already recorded today (UTC)
    in chat_messages - reuses the existing insert-per-response pattern,
    no new table. A None limit (platinum/founder) always passes without
    even querying, since there's nothing to enforce. A None user_id
    (no account / anonymous request) also skips the check entirely -
    there's no user_id to group chat_messages by, and unauthenticated
    access is presumably already gated elsewhere (get_current_user).
    """
    if user_id is None:
        return

    limit = DAILY_MESSAGE_LIMITS.get(tier, DAILY_MESSAGE_LIMITS["guest"])
    if limit is None:
        return

    conn_cursor.execute("""
        SELECT COUNT(*) FROM chat_messages
        WHERE user_id = %s AND role = \'assistant\'
        AND created >= date_trunc(\'day\', NOW() AT TIME ZONE \'UTC\')
    """, (user_id,))
    count_today = conn_cursor.fetchone()[0]

    if count_today >= limit:
        raise HTTPException(
            429,
            f"Daily message limit reached ({limit}/day for your current tier). "
            f"Stake more CLOSE to raise your limit, or wait until tomorrow (UTC)."
        )


@router.get("/usage")
async def get_usage(user=Depends(get_current_user)):
    """Today's (UTC) assistant-message count, tier, and daily limit -
    powers the usage bar in the chat UI. Uses the identical counting
    query as check_daily_message_limit so the displayed number can never
    drift from what actually gates sending - if that query ever changes,
    change it in both places together.
    """
    if not user:
        raise HTTPException(401, "Authentication required")

    tier = (user or {}).get("stake_tier", "guest")
    limit = DAILY_MESSAGE_LIMITS.get(tier, DAILY_MESSAGE_LIMITS["guest"])

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT COUNT(*) FROM chat_messages
                WHERE user_id = %s AND role = \'assistant\'
                AND created >= date_trunc(\'day\', NOW() AT TIME ZONE \'UTC\')
            """, (user["id"],))
            count_today = c.fetchone()[0]

    return {
        "tier": tier,
        "used": count_today,
        "limit": limit,
    }

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
                user_msg = extract_text(m.get("content", ""))
                break
        if not user_msg:
            raise HTTPException(400, "No message content")

        tier = (user or {}).get("stake_tier", "guest")
        with get_db() as _limit_conn:
            with _limit_conn.cursor() as _limit_c:
                check_daily_message_limit((user or {}).get('id'), tier, _limit_c)

        if req.images:
            tier_for_vision = tier
            allowed = TIER_MODEL_ACCESS.get(tier_for_vision, TIER_MODEL_ACCESS["guest"])
            vision_model = model if model in allowed and model in VISION_MODELS else next(
                (m2 for m2 in allowed if m2 in VISION_MODELS), None
            )
            if not vision_model:
                raise HTTPException(400, "Image messages require a vision-capable model, and none is available on your current tier.")
            model = vision_model

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
        if any(kw in user_msg.lower() for kw in [
            "latest", "today", "news", "current", "recent", "now", "who is",
            "who's", "president", "prime minister", "ceo", "governor", "election",
            "price of", "exchange rate", "stock price", "score", "won", "winner",
            "this year", "this week", "right now", "still", "update", "happened"
        ]):
            try:
                web_results = search_web(user_msg)
            except Exception as e:
                logger.error(f"Web search failed: {e}")

        system_prompt = build_system_prompt(user_msg, user, memory_context, web_results)
        messages_for_ai = [{"role": "system", "content": system_prompt}] + req.messages

        # If images were attached, replace the last (current) user message's
        # content with a multimodal block list, so the model actually sees
        # the image(s). Only the current turn carries images - prior turns
        # in req.messages keep whatever content they already had.
        if req.images:
            for m in reversed(messages_for_ai):
                if m.get("role") == "user":
                    m["content"] = build_vision_content(user_msg, req.images)
                    break

        ai_success = False
        response = ""
        model_used = "fallback"
        try:
            response, model_used = call_ai_model(messages_for_ai, user_id, model, tier=(user or {}).get("stake_tier", "guest"))
            ai_success = True
        except Exception as e:
            logger.error(f"AI call failed: {e}\n{traceback.format_exc()}")
            response = "I'm sorry, I encountered an error. Please try again later."
            model_used = "error"

        # Finalize transaction
        with get_db() as conn:
            with conn.cursor() as c:
                if ai_success:
                    assistant_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
                    c.execute("""
                        INSERT INTO chat_messages (id, chat_id, user_id, role, content, model, close_burned)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (assistant_msg_id, chat_id, user_id, "assistant", response, model_used, effective_burn))
                    try:
                        tx_hash = burn_with_staking_split(effective_burn, assistant_msg_id, c)
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
                user_msg = extract_text(m.get("content", ""))
                break
        if not user_msg:
            raise HTTPException(400, "No message content")

        stream_model = req.model or None
        tier = (user or {}).get("stake_tier", "guest")
        with get_db() as _limit_conn:
            with _limit_conn.cursor() as _limit_c:
                check_daily_message_limit((user or {}).get('id'), tier, _limit_c)

        if req.images:
            tier_for_vision = tier
            allowed = TIER_MODEL_ACCESS.get(tier_for_vision, TIER_MODEL_ACCESS["guest"])
            vision_model = stream_model if stream_model in allowed and stream_model in VISION_MODELS else next(
                (m2 for m2 in allowed if m2 in VISION_MODELS), None
            )
            if not vision_model:
                raise HTTPException(400, "Image messages require a vision-capable model, and none is available on your current tier.")
            stream_model = vision_model

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
        if any(kw in user_msg.lower() for kw in [
            "latest", "today", "news", "current", "recent", "now", "who is",
            "who's", "president", "prime minister", "ceo", "governor", "election",
            "price of", "exchange rate", "stock price", "score", "won", "winner",
            "this year", "this week", "right now", "still", "update", "happened"
        ]):
            try:
                web_results = search_web(user_msg)
            except Exception as e:
                logger.error(f"Web search failed: {e}")

        system_prompt = build_system_prompt(user_msg, user, memory_context, web_results)
        messages_for_ai = [{"role": "system", "content": system_prompt}] + req.messages

        tier = user.get("stake_tier", "guest")
        model = stream_model
        model_store = []

        # If images were attached, replace the last (current) user message's
        # content with a multimodal block list, so the model actually sees
        # the image(s). Only the current turn carries images - prior turns
        # in req.messages keep whatever content they already had.
        if req.images:
            for m in reversed(messages_for_ai):
                if m.get("role") == "user":
                    m["content"] = build_vision_content(user_msg, req.images)
                    break

        DOCUMENT_MARKER = "```generate-document"

        async def generate():
            accumulated = []
            ai_success = False
            mode_decided = False
            is_document_response = False
            prefix_buffer = ""

            try:
                async for chunk in call_ai_model_stream(messages_for_ai, user_id, model, tier, model_store):
                    accumulated.append(chunk)

                    if not mode_decided:
                        prefix_buffer += chunk
                        stripped = prefix_buffer.strip()
                        if not stripped:
                            pass  # only whitespace so far - still ambiguous, keep buffering
                        elif stripped.startswith(DOCUMENT_MARKER):
                            if len(stripped) >= len(DOCUMENT_MARKER):
                                mode_decided = True
                                is_document_response = True
                                yield f"data: {json.dumps({'status': 'generating_document'})}\n\n"
                            # else: matches so far but too short to be sure yet - keep buffering
                        elif DOCUMENT_MARKER.startswith(stripped):
                            pass  # still ambiguous (short prefix could still become the marker)
                        else:
                            mode_decided = True
                            is_document_response = False
                            yield f"data: {json.dumps({'content': prefix_buffer})}\n\n"
                    elif not is_document_response:
                        yield f"data: {json.dumps({'content': chunk})}\n\n"
                    # else: document mode - stay silent, just accumulate

                ai_success = True

                if not mode_decided:
                    mode_decided = True
                    is_document_response = False
                    if prefix_buffer:
                        yield f"data: {json.dumps({'content': prefix_buffer})}\n\n"

            except Exception as e:
                logger.error(f"Streaming AI call failed: {e}\n{traceback.format_exc()}")
                error_msg = "I'm sorry, I encountered an error. Please try again."
                if not mode_decided or not is_document_response:
                    yield f"data: {json.dumps({'content': error_msg})}\n\n"
                accumulated.append(error_msg)
                ai_success = False
            finally:
                full_response = "".join(accumulated)
                model_used = model_store[0] if model_store else "streamed"

                stored_content = full_response
                document_event = None

                if is_document_response and full_response:
                    from app.services.document_service import parse_document_block, generate_document, DocumentParseError
                    try:
                        parsed = parse_document_block(full_response)
                        file_bytes = generate_document(parsed["format"], parsed["content"])
                        doc_id = str(uuid.uuid4())
                        with get_db() as _doc_conn:
                            with _doc_conn.cursor() as _doc_c:
                                _doc_c.execute("""
                                    INSERT INTO generated_documents (id, user_id, chat_id, filename, format, file_bytes)
                                    VALUES (%s, %s, %s, %s, %s, %s)
                                """, (doc_id, user_id, chat_id, parsed["filename"], parsed["format"], file_bytes))
                                _doc_conn.commit()
                        stored_content = f"[document:{doc_id}:{parsed['filename']}.{parsed['format']}] Generated **{parsed['filename']}.{parsed['format']}**"
                        document_event = {"document_id": doc_id, "filename": f"{parsed['filename']}.{parsed['format']}", "format": parsed["format"]}
                    except DocumentParseError as e:
                        logger.error(f"Document parse failed, falling back to raw text: {e}")
                        stored_content = full_response
                    except Exception as e:
                        logger.error(f"Document generation failed: {e}\n{traceback.format_exc()}")
                        stored_content = "\u26a0\ufe0f I tried to generate a document but hit an error. Please try again."
                        document_event = {"error": True}

                with get_db() as conn:
                    with conn.cursor() as c:
                        if ai_success and full_response:
                            assistant_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
                            c.execute("""
                                INSERT INTO chat_messages (id, chat_id, user_id, role, content, model, close_burned)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """, (assistant_msg_id, chat_id, user_id, "assistant", stored_content, model_used, effective_burn))
                            try:
                                tx_hash = burn_with_staking_split(effective_burn, assistant_msg_id, c)
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

                if document_event and not document_event.get("error"):
                    yield f"data: {json.dumps({'document_ready': document_event})}\n\n"
                elif document_event and document_event.get("error"):
                    yield f"data: {json.dumps({'content': stored_content})}\n\n"

                # model_used carries a "(truncated)" suffix when the
                # provider stopped generating due to hitting max_tokens
                # (finish_reason == "length"), set in ai.py's streaming
                # blocks. Surface this distinctly from a normal completion
                # so the frontend can tell the two apart, same as the
                # existing dropped-connection handling.
                if "(truncated)" in model_used:
                    yield f"data: {json.dumps({'truncated': True})}\n\n"

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
@router.get("/documents/{document_id}")
async def download_document(document_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id, filename, format, file_bytes FROM generated_documents WHERE id = %s",
                (document_id,)
            )
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Document not found")
            owner_id, filename, fmt, file_bytes = row
            if str(owner_id) != str(user["id"]):
                raise HTTPException(403, "Access denied")

    from app.services.document_service import MIME_TYPES
    mime = MIME_TYPES.get(fmt, "application/octet-stream")
    return Response(
        content=bytes(file_bytes),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}.{fmt}"'}
    )


@router.post("/topup")
async def topup_chat_balance(
    tx_hash: str = Body(..., embed=True, description="Tx hash of a CLOSE payment to the chat treasury address"),
    user=Depends(get_current_user)
):
    """
    Credits close_balance (the internal chat-message allowance) based on a
    verified real CLOSE payment to the chat treasury. 1:1 credit - send 1000
    CLOSE, get 1000 close_balance to spend on messages. See
    chat_topup_service.py for the on-chain verification.
    """
    if not user:
        raise HTTPException(401, "Authentication required")

    from app.services.chat_topup_service import verify_and_credit_chat_topup
    try:
        result = verify_and_credit_chat_topup(user["id"], tx_hash)
    except ValueError as e:
        raise HTTPException(402, str(e))

    return result


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

@router.post("/messages/{message_id}/report")
async def report_message(message_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")

    reason = body.get("reason")
    details = body.get("details", "")
    if not reason:
        raise HTTPException(400, "A reason is required")

    report_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM chat_messages WHERE id = %s", (message_id,))
            if not c.fetchone():
                raise HTTPException(404, "Message not found")
            c.execute("""
                INSERT INTO message_reports (id, message_id, user_id, reason, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (report_id, message_id, user["id"], reason, details))
            conn.commit()

    return {"success": True, "report_id": report_id}

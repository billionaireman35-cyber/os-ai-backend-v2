from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import StreamingResponse
from app.core.database import get_db
from app.core.config import settings
from app.services.ai import call_ai_model, call_ai_model_stream, TIER_MODEL_ACCESS, DEFAULT_MODELS
from app.services.blockchain import get_effective_burn_amount
import bcrypt, uuid, json, logging, time

router = APIRouter(prefix="/v1", tags=["OS AI API"])
logger = logging.getLogger(__name__)

GATEWAY_MIN_TIERS = {"gold", "platinum"}  # matches Foundry's Gold+ gate


async def get_user_from_api_key(request: Request) -> dict:
    """
    Authenticates a request using an OS AI API key
    (Authorization: Bearer PREFIX_secret...) instead of a session token.
    Returns the owning user's record plus api_key_id, so callers can
    meter usage against the specific key. Also enforces the same Gold+
    tier requirement as Foundry's UI gate - previously that gate was
    frontend-only (create_api_key had no server-side tier check), so a
    bronze user with a key obtained before this fix could still call the
    gateway; this check closes that gap going forward.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing API key")
    api_key = auth[7:]
    if "_" not in api_key:
        raise HTTPException(401, "Invalid API key format")
    prefix = api_key.split("_")[0]

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, user_id, key_hash, is_active, scopes
                FROM api_keys WHERE prefix = %s
            """, (prefix,))
            row = c.fetchone()
            if not row:
                raise HTTPException(401, "Invalid API key")
            key_id, user_id, key_hash, is_active, scopes = row
            if not is_active:
                raise HTTPException(401, "API key has been revoked")
            if not bcrypt.checkpw(api_key.encode(), key_hash.encode()):
                raise HTTPException(401, "Invalid API key")

            c.execute("""
                SELECT id, email, name, close_balance, close_staked, stake_tier, wallet_address, is_founder
                FROM users WHERE id = %s
            """, (user_id,))
            urow = c.fetchone()
            if not urow:
                raise HTTPException(401, "API key owner not found")

            c.execute("UPDATE api_keys SET last_used = NOW() WHERE id = %s", (key_id,))
            conn.commit()

    tier = urow[5] or "bronze"
    is_founder = urow[7]
    if tier not in GATEWAY_MIN_TIERS and not is_founder:
        raise HTTPException(403, "OS AI API access requires Gold tier or higher (10,000+ CLOSE staked).")

    return {
        "id": urow[0], "email": urow[1], "name": urow[2],
        "close_balance": urow[3] or 0, "close_staked": urow[4] or 0,
        "stake_tier": tier, "wallet_address": urow[6],
        "is_founder": is_founder, "api_key_id": key_id, "scopes": scopes,
    }


def _burn_for_api_call(user_id: str, effective_burn: int) -> bool:
    """Same atomic check+deduct pattern used in chat.py. Returns False
    (balance untouched) if the user can't afford this call."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET close_balance = close_balance - %s WHERE id = %s AND close_balance >= %s",
                (effective_burn, user_id, effective_burn)
            )
            success = c.rowcount > 0
            if success:
                c.execute("""
                    INSERT INTO close_transactions (id, user_id, type, amount, status)
                    VALUES (%s, %s, %s, %s, %s)
                """, (str(uuid.uuid4()), user_id, "api_burn", effective_burn, "completed"))
            conn.commit()
            return success


@router.post("/chat/completions")
async def api_chat_completions(
    body: dict = Body(...),
    user=Depends(get_user_from_api_key),
):
    """
    OpenAI-compatible chat completion endpoint, authenticated by an OS AI
    API key rather than a session token. Internally routes to the same
    call_ai_model/call_ai_model_stream the main chat product uses - same
    models, same tier access rules, same CLOSE billing per call. This is
    the developer-facing front door; the chat UI is the consumer-facing
    one, both ending up in the same place underneath.
    """
    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        raise HTTPException(400, "messages array is required")

    tier = user.get("stake_tier", "bronze")
    requested_model = body.get("model")
    allowed = TIER_MODEL_ACCESS.get(tier, TIER_MODEL_ACCESS["bronze"])
    model = requested_model if requested_model in allowed else DEFAULT_MODELS.get(tier, DEFAULT_MODELS["bronze"])

    wallet_address = user.get("wallet_address")
    effective_burn = get_effective_burn_amount(wallet_address, settings.BURN_PER_MESSAGE)

    if not _burn_for_api_call(user["id"], effective_burn):
        raise HTTPException(402, "Insufficient CLOSE balance. Top up in OS Vault.")

    stream = bool(body.get("stream", False))

    if not stream:
        try:
            content, model_used = call_ai_model(messages, user["id"], model, tier=tier)
        except Exception as e:
            logger.error(f"API gateway call failed: {e}")
            raise HTTPException(500, "Model call failed")
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_used,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "close_burned": effective_burn,
        }

    async def generate():
        model_store = []
        async for chunk in call_ai_model_stream(messages, user["id"], model, tier, model_store):
            payload = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_store[0] if model_store else model,
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/models")
async def list_models(user=Depends(get_user_from_api_key)):
    tier = user.get("stake_tier", "bronze")
    allowed = TIER_MODEL_ACCESS.get(tier, TIER_MODEL_ACCESS["bronze"])
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in allowed]}

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.config import settings
from app.services.sandbox import run_sandbox_task
from app.services.blockchain import get_effective_burn_amount
from app.api.v1.chat import burn_with_staking_split
import uuid, logging, traceback

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".csv", ".xlsx", ".xls", ".json", ".xml",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".txt", ".md", ".py",
}


@router.post("/analyze")
async def sandbox_analyze(
    prompt: str = Form(...),
    file: UploadFile = File(None),
    user=Depends(get_current_user),
):
    if not user:
        raise HTTPException(401, "Authentication required")

    if file is not None:
        ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: {ext or 'unknown'}")

    user_id = user["id"]
    wallet_address = user.get("wallet_address")
    effective_burn = get_effective_burn_amount(wallet_address, settings.SANDBOX_BURN_AMOUNT)
    burn_tx_id = str(uuid.uuid4())

    # Atomic balance check + deduct - same pattern as chat.py
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
                        "content": "Insufficient CLOSE balance for a sandbox run. Please top up.",
                        "requires_purchase": True,
                        "close_balance": user.get("close_balance", 0)
                    }
                )
            c.execute("""
                INSERT INTO close_transactions (id, user_id, type, amount, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (burn_tx_id, user_id, "sandbox_burn", effective_burn, "pending"))
            conn.commit()

    file_bytes = await file.read() if file is not None else None
    filename = file.filename if file is not None else None

    success = False
    result = {"response_text": "", "files": [], "container_id": None}
    try:
        result = await run_sandbox_task(prompt, file_bytes, filename)
        success = True
    except Exception as e:
        logger.error(f"Sandbox task failed: {e}\n{traceback.format_exc()}")
        result["response_text"] = "The sandbox run failed. Please try again."

    # Finalize - burn on success, refund on failure (same pattern as chat.py)
    with get_db() as conn:
        with conn.cursor() as c:
            if success:
                try:
                    tx_hash = burn_with_staking_split(effective_burn, burn_tx_id, c)
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
        "response_text": result["response_text"],
        "files": result["files"],
        "container_id": result["container_id"],
        "close_balance": new_balance,
        "close_burned": effective_burn if success else 0,
        "success": success,
    }

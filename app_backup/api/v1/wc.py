from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.core.database import get_db
import uuid
import json

router = APIRouter(prefix="/wc", tags=["WalletConnect"])

@router.post("/session")
async def create_session(req: dict, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    # Store session topic and metadata
    topic = req.get("topic")
    dapp_name = req.get("dapp_name")
    dapp_url = req.get("dapp_url")
    # ... store in db
    return {"message": "Session created"}

@router.delete("/session/{topic}")
async def delete_session(topic: str, user=Depends(get_current_user)):
    # delete
    return {"message": "Session deleted"}
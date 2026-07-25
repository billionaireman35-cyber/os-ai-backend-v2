from pydantic import BaseModel, EmailStr
from typing import Optional, List

class SendCodeRequest(BaseModel):
    email: str
    purpose: str = "verification"

class VerifyCodeRequest(BaseModel):
    email: str
    code: str
    purpose: str = "verification"

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    verification_code: str
    fingerprint: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str
    fingerprint: Optional[str] = None

class ChatRequest(BaseModel):
    messages: List[dict]
    chat_id: Optional[str] = None
    model: Optional[str] = None  # new

class SendRequest(BaseModel):
    to: str
    amount: float
    token: str
    chain: str = "polygon"
    signed_tx: str

class SwapQuoteRequest(BaseModel):
    from_token: str
    to_token: str
    amount: float
    chain: str = "polygon"
    slippage: float = 0.5

class BridgeQuoteRequest(BaseModel):
    from_chain: str
    to_chain: str
    token: str
    amount: float

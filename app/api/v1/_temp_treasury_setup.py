"""
TEMPORARY, one-time endpoint to generate the staking treasury wallet.
Gated by FOUNDER_KEY. Call once, save the response values as Render env
vars (STAKING_TREASURY_ADDRESS, STAKING_TREASURY_ENCRYPTED_KEY), then
DELETE THIS FILE and its router registration in the very next commit.
Never leave this endpoint live longer than necessary - it returns key
material, even though encrypted.
"""
from fastapi import APIRouter, HTTPException, Body
from app.core.config import settings
from app.services.wallet_service import _encrypt_private_key
from eth_utils import keccak, to_checksum_address
from ecdsa import SigningKey, SECP256k1
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/generate-treasury-wallet")
async def generate_treasury_wallet(
    founder_key: str = Body(..., embed=True),
    password: str = Body(..., embed=True, description="Password to encrypt the new wallet's private key")
):
    if founder_key != settings.FOUNDER_KEY:
        raise HTTPException(403, {
            "error": "Invalid founder key",
            "received_length": len(founder_key),
            "expected_length": len(settings.FOUNDER_KEY),
            "received_first_last": f"{founder_key[0]}...{founder_key[-1]}" if founder_key else "(empty)",
            "expected_first_last": f"{settings.FOUNDER_KEY[0]}...{settings.FOUNDER_KEY[-1]}" if settings.FOUNDER_KEY else "(empty)",
        })
    if len(password) < 12:
        raise HTTPException(400, "Use a password of at least 12 characters - this protects real treasury funds")

    sk = SigningKey.generate(curve=SECP256k1)
    private_key_hex = sk.to_string().hex()
    public_key_bytes = sk.get_verifying_key().to_string()
    address = to_checksum_address("0x" + keccak(public_key_bytes).hex()[-40:])
    encrypted_key = _encrypt_private_key(private_key_hex, password)

    logger.info(f"Generated a new standalone treasury wallet: {address} (key material not logged)")

    return {
        "address": address,
        "encrypted_key": encrypted_key,
        "instructions": "Save 'address' as STAKING_TREASURY_ADDRESS and 'encrypted_key' as STAKING_TREASURY_ENCRYPTED_KEY in Render env vars. Save your password separately (you already know it) as STAKING_TREASURY_PASSWORD. Then remove this endpoint entirely.",
    }

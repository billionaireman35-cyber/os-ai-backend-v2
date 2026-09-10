"""
Central wallet identity and authorization service.

This module is the single source of truth for resolving a user's wallet
before any financial operation attempts to sign or access wallet material.

Rules:
- wallet_id is the preferred stable identity.
- wallet_address may be supplied for backwards-compatible callers.
- A wallet must belong to the authenticated user.
- Primary wallet resolution is explicit through users.wallet_address.
- Connected wallets are never considered backend-signable.
- Connected wallets must have no encrypted private key.
- Custodial wallets must have encrypted key material before backend signing.
- The resolved wallet address is returned from the database, never trusted
  blindly from the caller.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from eth_utils import is_address, to_checksum_address

from app.core.database import get_db


def _normalize_address(address: str) -> str:
    if not address or not isinstance(address, str):
        raise ValueError("Wallet address is required")

    if not is_address(address):
        raise ValueError("Invalid wallet address")

    return to_checksum_address(address)


def _normalize_wallet_id(wallet_id: str) -> str:
    if not wallet_id:
        raise ValueError("Wallet ID is required")

    try:
        return str(UUID(str(wallet_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid wallet ID")


def resolve_wallet_identity(
    user_id: str,
    wallet_id: Optional[str] = None,
    wallet_address: Optional[str] = None,
    require_signing: bool = False,
) -> dict:
    """
    Resolve exactly one wallet owned by user_id.

    Identity precedence:
      1. wallet_id, when supplied.
      2. wallet_address, when supplied.
      3. explicit primary wallet resolution via users.wallet_address.

    The caller-provided address is never used as the authoritative identity;
    the address returned by os_wallets is authoritative.

    Returns:
      {
        "id": str,
        "user_id": str,
        "chain": str,
        "address": str,
        "label": str,
        "wallet_type": "custodial" | "connected",
        "is_active": bool,
        "can_sign": bool,
        "is_primary": bool,
      }

    Raises ValueError on missing, invalid, foreign, inactive, or
    non-signable wallet selection.
    """
    if not user_id:
        raise ValueError("User identity is required")

    normalized_address = (
        _normalize_address(wallet_address) if wallet_address else None
    )
    normalized_wallet_id = (
        _normalize_wallet_id(wallet_id) if wallet_id else None
    )

    with get_db() as conn:
        with conn.cursor() as c:
            if normalized_wallet_id:
                c.execute(
                    """
                    SELECT
                        ow.id,
                        ow.user_id,
                        ow.chain,
                        ow.address,
                        ow.label,
                        ow.wallet_type,
                        ow.is_active,
                        ow.encrypted_key,
                        u.wallet_address
                    FROM os_wallets ow
                    JOIN users u ON u.id = ow.user_id
                    WHERE ow.id = %s
                      AND ow.user_id = %s
                    """,
                    (normalized_wallet_id, user_id),
                )

            elif normalized_address:
                c.execute(
                    """
                    SELECT
                        ow.id,
                        ow.user_id,
                        ow.chain,
                        ow.address,
                        ow.label,
                        ow.wallet_type,
                        ow.is_active,
                        ow.encrypted_key,
                        u.wallet_address
                    FROM os_wallets ow
                    JOIN users u ON u.id = ow.user_id
                    WHERE ow.user_id = %s
                      AND LOWER(ow.address) = LOWER(%s)
                    """,
                    (user_id, normalized_address),
                )

            else:
                c.execute(
                    """
                    SELECT
                        ow.id,
                        ow.user_id,
                        ow.chain,
                        ow.address,
                        ow.label,
                        ow.wallet_type,
                        ow.is_active,
                        ow.encrypted_key,
                        u.wallet_address
                    FROM os_wallets ow
                    JOIN users u ON u.id = ow.user_id
                    WHERE ow.user_id = %s
                      AND LOWER(ow.address) = LOWER(u.wallet_address)
                    """,
                    (user_id,),
                )

            row = c.fetchone()

    if not row:
        if normalized_wallet_id:
            raise ValueError("Wallet not found or not owned by this user")
        if normalized_address:
            raise ValueError("Wallet not found or not owned by this user")
        raise ValueError("Primary wallet not found")

    (
        row_id,
        row_user_id,
        chain,
        address,
        label,
        wallet_type,
        is_active,
        encrypted_key,
        primary_address,
    ) = row

    if str(row_user_id) != str(user_id):
        raise ValueError("Wallet ownership verification failed")

    if not is_active:
        raise ValueError("Wallet is inactive")

    wallet_type = (wallet_type or "custodial").lower()

    if wallet_type not in {"custodial", "connected"}:
        raise ValueError("Unsupported wallet type")

    authoritative_address = to_checksum_address(address)

    if normalized_address and authoritative_address.lower() != normalized_address.lower():
        raise ValueError("Wallet ID and wallet address do not match")

    is_primary = bool(
        primary_address
        and authoritative_address.lower() == primary_address.lower()
    )

    can_sign = wallet_type == "custodial" and bool(encrypted_key)

    if wallet_type == "connected" and encrypted_key:
        raise ValueError(
            "Connected wallet has backend key material; signing is blocked"
        )

    if require_signing and not can_sign:
        if wallet_type == "connected":
            raise ValueError(
                "Connected wallets must sign externally; backend signing is not supported"
            )
        raise ValueError("Wallet is not configured for backend signing")

    return {
        "id": str(row_id),
        "user_id": str(row_user_id),
        "chain": chain,
        "address": authoritative_address,
        "label": label,
        "wallet_type": wallet_type,
        "is_active": bool(is_active),
        "can_sign": can_sign,
        "is_primary": is_primary,
    }


def require_signing_wallet(
    user_id: str,
    wallet_id: Optional[str] = None,
    wallet_address: Optional[str] = None,
) -> dict:
    """Resolve a wallet and require that this backend may sign for it."""
    return resolve_wallet_identity(
        user_id=user_id,
        wallet_id=wallet_id,
        wallet_address=wallet_address,
        require_signing=True,
    )

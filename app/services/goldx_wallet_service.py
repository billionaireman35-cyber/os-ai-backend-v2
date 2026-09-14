import base64
from enum import Enum

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes
from Crypto.Hash import SHA256
from eth_utils import keccak, to_checksum_address
from ecdsa import SigningKey, SECP256k1

from app.core.database import get_db


SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


class GoldxWalletPurpose(str, Enum):
    TREASURY = "treasury"
    REVENUE = "revenue"
    DISTRIBUTION = "distribution"
    RELAYER = "relayer"


SUPPORTED_CHAINS = frozenset({
    "polygon",
    "ethereum",
    "bsc",
    "arbitrum",
    "base",
})


def _derive_key(secret: str, salt: bytes) -> bytes:
    if not secret:
        raise ValueError("Goldx wallet encryption secret is required")
    return PBKDF2(secret, salt, dkLen=32, count=600000, hmac_hash_module=SHA256)


def _validate_private_key(private_key_hex: str) -> str:
    key = private_key_hex.lower().removeprefix("0x")

    if len(key) != 64:
        raise ValueError("Invalid private key length")

    try:
        key_int = int(key, 16)
    except ValueError as exc:
        raise ValueError("Invalid private key encoding") from exc

    if not (0 < key_int < SECP256K1_N):
        raise ValueError("Private key out of range")

    return key


def _address_from_private_key(private_key_hex: str) -> str:
    key = _validate_private_key(private_key_hex)
    signing_key = SigningKey.from_string(bytes.fromhex(key), curve=SECP256k1)
    public_key = signing_key.get_verifying_key().to_string()
    return to_checksum_address("0x" + keccak(public_key)[-20:].hex())


def encrypt_company_private_key(private_key_hex: str, secret: str) -> str:
    key_hex = _validate_private_key(private_key_hex)

    salt = get_random_bytes(16)
    derived_key = _derive_key(secret, salt)
    nonce = get_random_bytes(12)

    cipher = AES.new(derived_key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(key_hex.encode())

    return base64.b64encode(
        salt + nonce + tag + ciphertext
    ).decode()


def decrypt_company_private_key(encrypted_b64: str, secret: str) -> str:
    if not encrypted_b64:
        raise ValueError("Encrypted Goldx private key is empty")

    try:
        raw = base64.b64decode(encrypted_b64)
    except Exception as exc:
        raise ValueError("Invalid encrypted Goldx private key") from exc

    if len(raw) < 44:
        raise ValueError("Encrypted Goldx private key is too short")

    salt = raw[:16]
    nonce = raw[16:28]
    tag = raw[28:44]
    ciphertext = raw[44:]

    derived_key = _derive_key(secret, salt)
    cipher = AES.new(derived_key, AES.MODE_GCM, nonce=nonce)

    try:
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except Exception as exc:
        raise ValueError("Goldx private key decryption failed") from exc

    try:
        return _validate_private_key(plaintext.decode())
    except Exception as exc:
        raise ValueError("Decrypted Goldx private key is invalid") from exc


def register_company_wallet(
    *,
    purpose: GoldxWalletPurpose | str,
    chain: str,
    address: str,
    private_key_hex: str,
    encryption_secret: str,
) -> str:
    purpose_value = (
        purpose.value if isinstance(purpose, GoldxWalletPurpose) else str(purpose)
    )

    if purpose_value not in {item.value for item in GoldxWalletPurpose}:
        raise ValueError("Unsupported Goldx wallet purpose")

    chain = chain.lower()
    if chain not in SUPPORTED_CHAINS:
        raise ValueError("Unsupported Goldx wallet chain")

    checksum_address = to_checksum_address(address)
    derived_address = _address_from_private_key(private_key_hex)

    if checksum_address != derived_address:
        raise ValueError("Private key does not match Goldx wallet address")

    encrypted_key = encrypt_company_private_key(
        private_key_hex,
        encryption_secret,
    )

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO goldx_company_wallets (
                    purpose,
                    chain,
                    address,
                    encrypted_key
                )
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    purpose_value,
                    chain,
                    checksum_address,
                    encrypted_key,
                ),
            )
            wallet_id = str(c.fetchone()[0])
            conn.commit()

    return wallet_id


def get_company_wallet(
    *,
    purpose: GoldxWalletPurpose | str,
    chain: str,
) -> dict:
    purpose_value = (
        purpose.value if isinstance(purpose, GoldxWalletPurpose) else str(purpose)
    )

    chain = chain.lower()

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT id, purpose, chain, address, is_active
                FROM goldx_company_wallets
                WHERE purpose = %s
                  AND chain = %s
                  AND is_active = TRUE
                LIMIT 1
                """,
                (purpose_value, chain),
            )
            row = c.fetchone()

    if not row:
        raise ValueError(
            f"No active Goldx {purpose_value} wallet configured for {chain}"
        )

    return {
        "id": str(row[0]),
        "purpose": row[1],
        "chain": row[2],
        "address": row[3],
        "is_active": row[4],
    }


def get_company_private_key(
    *,
    purpose: GoldxWalletPurpose | str,
    chain: str,
    encryption_secret: str,
) -> str:
    purpose_value = (
        purpose.value if isinstance(purpose, GoldxWalletPurpose) else str(purpose)
    )
    chain = chain.lower()

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT address, encrypted_key
                FROM goldx_company_wallets
                WHERE purpose = %s
                  AND chain = %s
                  AND is_active = TRUE
                LIMIT 1
                """,
                (purpose_value, chain),
            )
            row = c.fetchone()

    if not row:
        raise ValueError(
            f"No active Goldx {purpose_value} wallet configured for {chain}"
        )

    stored_address, encrypted_key = row
    private_key = decrypt_company_private_key(encrypted_key, encryption_secret)

    derived_address = _address_from_private_key(private_key)
    if to_checksum_address(stored_address) != derived_address:
        raise ValueError("Goldx wallet key does not match stored address")

    return private_key

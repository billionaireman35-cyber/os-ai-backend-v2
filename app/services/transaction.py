import requests
import hashlib
from Crypto.Hash import keccak
from ecdsa import SigningKey, SECP256k1
from ecdsa.util import sigencode_der, sigdecode_der
from ecdsa.numbertheory import inverse_mod, square_root_mod_prime
from ecdsa.ellipticcurve import Point, INFINITY
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = SECP256K1_N // 2

def hex_to_bytes(h: str) -> bytes:
    h = h[2:] if h.startswith("0x") else h
    if not h:
        return b""
    if len(h) % 2 != 0:
        h = "0" + h
    return bytes.fromhex(h)

def _rlp_encode_length(length: int, offset: int) -> bytes:
    if length < 56:
        return bytes([offset + length])
    else:
        length_bytes = length.to_bytes((length.bit_length() + 7) // 8, 'big')
        return bytes([offset + 55 + len(length_bytes)]) + length_bytes

def rlp_encode(obj):
    if isinstance(obj, int):
        if obj == 0:
            return b'\x80'
        if 0 < obj < 0x80:
            return bytes([obj])
        obj_bytes = obj.to_bytes((obj.bit_length() + 7) // 8, 'big')
        return _rlp_encode_length(len(obj_bytes), 0x80) + obj_bytes
    elif isinstance(obj, bytes):
        if len(obj) == 1 and obj[0] < 0x80:
            return obj
        else:
            return _rlp_encode_length(len(obj), 0x80) + obj
    elif isinstance(obj, str):
        return rlp_encode(obj.encode('utf-8'))
    elif isinstance(obj, list):
        encoded_items = b''.join(rlp_encode(item) for item in obj)
        return _rlp_encode_length(len(encoded_items), 0xc0) + encoded_items
    else:
        raise TypeError(f"Unsupported type for RLP: {type(obj)}")

def _rpc_call(chain: str, method: str, params: list) -> dict:
    rpc_url = settings.get_rpc_url(chain)
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    }
    response = requests.post(rpc_url, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()

def _get_nonce(chain: str, address: str) -> int:
    result = _rpc_call(chain, "eth_getTransactionCount", [address, "pending"])
    return int(result.get("result", "0x0"), 16)

def _get_gas_price(chain: str) -> int:
    result = _rpc_call(chain, "eth_gasPrice", [])
    return int(result.get("result", "0x0"), 16)

def _estimate_gas(chain: str, tx_dict: dict) -> int:
    try:
        result = _rpc_call(chain, "eth_estimateGas", [tx_dict])
        return int(result.get("result", "0x0"), 16)
    except Exception:
        return 0

def _keccak256(data: bytes) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()

def _recover_public_key_from_signature(msg_hash: bytes, r: int, s: int, recid: int):
    curve = SECP256k1.curve
    G = SECP256k1.generator
    order = SECP256k1.order

    x = r
    p = curve.p()
    y_sq = (pow(x, 3, p) + 7) % p
    y = square_root_mod_prime(y_sq, p)
    if recid == 1:
        if y % 2 == 0:
            y = p - y
    else:
        if y % 2 != 0:
            y = p - y
    R = Point(curve, x, y)
    R_aff = R.to_affine()
    G_aff = G.to_affine()

    h_int = int.from_bytes(msg_hash, 'big')
    sR = s * R_aff
    hG = h_int * G_aff
    diff = sR - hG
    r_inv = inverse_mod(r, order)
    Q = r_inv * diff

    if Q == INFINITY:
        raise ValueError("Recovery failed: point at infinity")
    pubkey_bytes = b'\x04' + Q.x().to_bytes(32, 'big') + Q.y().to_bytes(32, 'big')
    from ecdsa import VerifyingKey
    vk = VerifyingKey.from_string(pubkey_bytes, curve=SECP256k1)
    return vk

def sign_transaction(
    chain: str,
    from_address: str,
    to_address: str,
    value_wei: int,
    private_key_hex: str,
    data: str = "0x",
    gas_limit: int = None,
    gas_price: int = None,
    nonce: int = None
) -> str:
    chain_ids = {
        "polygon": 137,
        "ethereum": 1,
        "bsc": 56,
        "arbitrum": 42161,
        "base": 8453,
    }
    if chain not in chain_ids:
        raise ValueError(f"Unsupported chain: {chain}")
    chain_id = chain_ids[chain]

    to_bytes = hex_to_bytes(to_address)
    data_bytes = hex_to_bytes(data)
    if len(to_bytes) != 20:
        raise ValueError(f"Invalid to_address: expected 20 bytes, got {len(to_bytes)}")

    if nonce is None:
        nonce = _get_nonce(chain, from_address)
    if gas_price is None:
        gas_price = _get_gas_price(chain)

    # Determine gas limit
    if gas_limit is None:
        tx_dict = {
            "from": from_address,
            "to": to_address,
            "value": hex(value_wei),
            "data": data,
        }
        estimated = _estimate_gas(chain, tx_dict)
        if estimated > 0:
            gas_limit = int(estimated * 1.2)
        else:
            # Fallback when eth_estimateGas itself fails/reverts (can happen
            # for contract calls needing prior approval, or certain RPC
            # nodes rejecting the simulation): native transfer = 21000,
            # contract interaction = 250000 (generous general-purpose
            # ceiling - a DEX router swap alone needs 76770+ in practice,
            # per the KyberSwap gas-shortfall this fixed on 2026-08-20).
            gas_limit = 21000 if data == "0x" else 250000

    # Ensure minimum gas
    min_gas = 21000
    if gas_limit < min_gas:
        gas_limit = min_gas

    tx = [
        nonce,
        gas_price,
        gas_limit,
        to_bytes,
        value_wei,
        data_bytes,
        chain_id,
        0,
        0,
    ]
    encoded = rlp_encode(tx)
    tx_hash = _keccak256(encoded)

    sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
    signature_der = sk.sign_digest_deterministic(tx_hash, hashfunc=hashlib.sha256, sigencode=sigencode_der)
    r, s = sigdecode_der(signature_der, 0)

    if s > SECP256K1_HALF_N:
        s = SECP256K1_N - s

    recid = None
    for candidate_recid in (0, 1):
        try:
            vk = _recover_public_key_from_signature(tx_hash, r, s, candidate_recid)
            pubkey_bytes = vk.to_string()
            addr_hash = _keccak256(pubkey_bytes)
            recovered_address = "0x" + addr_hash[-20:].hex()
            if recovered_address.lower() == from_address.lower():
                recid = candidate_recid
                break
        except Exception:
            continue
    if recid is None:
        pubkey_bytes = sk.get_verifying_key().to_string()
        y = int.from_bytes(pubkey_bytes[32:], 'big')
        recid = 1 if (y & 1) else 0

    v = chain_id * 2 + 35 + recid

    signed_tx = [
        nonce,
        gas_price,
        gas_limit,
        to_bytes,
        value_wei,
        data_bytes,
        v,
        r,
        s,
    ]
    signed_encoded = rlp_encode(signed_tx)
    return "0x" + signed_encoded.hex()

def broadcast_transaction(chain: str, signed_tx_hex: str) -> str:
    if not signed_tx_hex.startswith("0x"):
        signed_tx_hex = "0x" + signed_tx_hex
    result = _rpc_call(chain, "eth_sendRawTransaction", [signed_tx_hex])
    tx_hash = result.get("result")
    if not tx_hash:
        raise Exception("Broadcast failed: " + str(result))
    return tx_hash

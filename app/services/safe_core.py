"""Shared Safe contract mechanics for Customer and Enterprise Safe."""

from eth_utils import to_checksum_address

from app.core.config import settings, get_safe_singleton, get_safe_proxy_factory


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

SAFE_SETUP_ABI = [{
    "inputs": [
        {"name": "_owners", "type": "address[]"},
        {"name": "_threshold", "type": "uint256"},
        {"name": "to", "type": "address"},
        {"name": "data", "type": "bytes"},
        {"name": "fallbackHandler", "type": "address"},
        {"name": "paymentToken", "type": "address"},
        {"name": "payment", "type": "uint256"},
        {"name": "paymentReceiver", "type": "address"},
    ],
    "name": "setup",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function",
}]

SAFE_PROXY_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "_singleton", "type": "address"},
            {"name": "initializer", "type": "bytes"},
            {"name": "saltNonce", "type": "uint256"},
        ],
        "name": "createProxyWithNonce",
        "outputs": [{"name": "proxy", "type": "address"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "name": "proxy", "type": "address"},
            {"indexed": False, "name": "singleton", "type": "address"},
        ],
        "name": "ProxyCreation",
        "type": "event",
    },
]

SAFE_CONTRACT_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "_nonce", "type": "uint256"},
        ],
        "name": "getTransactionHash",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"},
        ],
        "name": "execTransaction",
        "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getOwners",
        "outputs": [{"name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getThreshold",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def validate_chain(chain: str) -> None:
    if chain not in settings.SUPPORTED_CHAINS:
        raise ValueError(f"Unsupported chain: {chain}")


def validate_owners(owners: list[str]) -> list[str]:
    if not owners:
        raise ValueError("At least one owner is required")

    try:
        normalized = [to_checksum_address(owner) for owner in owners]
    except Exception as exc:
        raise ValueError("Invalid Safe owner address") from exc

    if len({owner.lower() for owner in normalized}) != len(normalized):
        raise ValueError("Safe owners must be unique")

    return normalized


def validate_threshold(threshold: int, owner_count: int) -> None:
    if threshold < 1 or threshold > owner_count:
        raise ValueError(
            f"Threshold must be between 1 and {owner_count}"
        )


def get_safe_addresses(chain: str) -> tuple[str, str]:
    validate_chain(chain)

    singleton = get_safe_singleton(chain)
    factory = get_safe_proxy_factory(chain)

    if not singleton or not factory:
        raise ValueError(f"Safe contracts not configured for chain: {chain}")

    return (
        to_checksum_address(singleton),
        to_checksum_address(factory),
    )


def get_safe_contract(chain: str, safe_address: str):
    validate_chain(chain)

    from app.services.blockchain import get_web3
    web3 = get_web3(chain)
    return web3.eth.contract(
        address=to_checksum_address(safe_address),
        abi=SAFE_CONTRACT_ABI,
    )


def build_setup_calldata(
    chain: str,
    owners: list[str],
    threshold: int,
) -> bytes:
    normalized = validate_owners(owners)
    validate_threshold(threshold, len(normalized))

    singleton, _ = get_safe_addresses(chain)
    from app.services.blockchain import get_web3
    web3 = get_web3(chain)

    contract = web3.eth.contract(
        address=singleton,
        abi=SAFE_SETUP_ABI,
    )

    calldata = contract.encodeABI(
        fn_name="setup",
        args=[
            normalized,
            threshold,
            ZERO_ADDRESS,
            b"",
            ZERO_ADDRESS,
            ZERO_ADDRESS,
            0,
            ZERO_ADDRESS,
        ],
    )

    return bytes.fromhex(calldata[2:])


def get_safe_nonce(chain: str, safe_address: str) -> int:
    return get_safe_contract(chain, safe_address).functions.nonce().call()


def get_transaction_hash(
    chain: str,
    safe_address: str,
    to_address: str,
    value_wei: int,
    data: str,
    nonce: int,
    operation: int = 0,
    safe_tx_gas: int = 0,
    base_gas: int = 0,
    gas_price: int = 0,
    gas_token: str = ZERO_ADDRESS,
    refund_receiver: str = ZERO_ADDRESS,
) -> str:
    contract = get_safe_contract(chain, safe_address)

    result = contract.functions.getTransactionHash(
        to_checksum_address(to_address),
        int(value_wei),
        bytes.fromhex(data[2:] if data.startswith("0x") else data),
        int(operation),
        int(safe_tx_gas),
        int(base_gas),
        int(gas_price),
        to_checksum_address(gas_token),
        to_checksum_address(refund_receiver),
        int(nonce),
    ).call()

    return "0x" + bytes(result).hex()


def normalize_signature(signature: str) -> str:
    raw = bytes.fromhex(signature[2:] if signature.startswith("0x") else signature)

    if len(raw) != 65:
        raise ValueError("Safe signature must be exactly 65 bytes")

    v = raw[64]
    if v in (0, 1):
        v += 27

    if v not in (27, 28, 31, 32):
        raise ValueError("Unsupported Safe signature recovery value")

    return "0x" + (raw[:64] + bytes([v])).hex()


def sort_signatures(signatures: list[dict]) -> list[dict]:
    return sorted(
        signatures,
        key=lambda signature: signature["owner"].lower(),
    )


def build_exec_transaction_calldata(
    chain: str,
    safe_address: str,
    to_address: str,
    value_wei: int,
    data: str,
    signatures: list[dict],
    operation: int = 0,
    safe_tx_gas: int = 0,
    base_gas: int = 0,
    gas_price: int = 0,
    gas_token: str = ZERO_ADDRESS,
    refund_receiver: str = ZERO_ADDRESS,
) -> str:
    contract = get_safe_contract(chain, safe_address)

    normalized = [
        {
            "owner": to_checksum_address(signature["owner"]),
            "signature": normalize_signature(signature["signature"]),
        }
        for signature in sort_signatures(signatures)
    ]

    packed = "0x" + "".join(
        signature["signature"][2:] for signature in normalized
    )

    return contract.encodeABI(
        fn_name="execTransaction",
        args=[
            to_checksum_address(to_address),
            int(value_wei),
            bytes.fromhex(data[2:] if data.startswith("0x") else data),
            int(operation),
            int(safe_tx_gas),
            int(base_gas),
            int(gas_price),
            to_checksum_address(gas_token),
            to_checksum_address(refund_receiver),
            bytes.fromhex(packed[2:]),
        ],
    )

def get_safe_owners(chain: str, safe_address: str) -> list[str]:
    return [
        to_checksum_address(owner)
        for owner in get_safe_contract(
            chain, safe_address
        ).functions.getOwners().call()
    ]


def get_safe_threshold(chain: str, safe_address: str) -> int:
    return int(
        get_safe_contract(
            chain, safe_address
        ).functions.getThreshold().call()
    )

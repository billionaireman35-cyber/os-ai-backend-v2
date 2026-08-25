import os
from typing import List
from pydantic import BaseSettings

class Settings(BaseSettings):
    class Config:
        extra = "ignore"
        env_file = ".env"
        env_file_encoding = "utf-8"

    DATABASE_URL: str
    JWT_SECRET: str
    FOUNDER_KEY: str
    ALLOWED_ORIGINS: List[str] = ["*"]

    RESEND_API_KEY: str = ""
    FRONTEND_URL: str = "http://localhost:5173"

    POLYGON_RPC_URL: str = "https://polygon-rpc.com"
    ETHEREUM_RPC_URL: str = "https://eth.llamarpc.com"
    BSC_RPC_URL: str = "https://bsc-dataseed.binance.org"
    ARBITRUM_RPC_URL: str = "https://arb1.arbitrum.io/rpc"
    BASE_RPC_URL: str = "https://mainnet.base.org"
    ALCHEMY_API_KEY: str = ""
    INFURA_API_KEY: str = ""

    OPENROUTER_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-ada-002"
    MEMORY_RETRIEVAL_LIMIT: int = 5

    COINGECKO_KEY: str = ""
    COINGECKO_KEY: str = ""
    NEWS_API_KEY: str = ""
    GNEWS_API_KEY: str = ""
    SERPAPI_KEY: str = ""
    ONEINCH_API_KEY: str = ""
    ONEINCH_BASE_URL: str = "https://business.1inch.com/swap/v5.0"

    MOONPAY_SECRET_KEY: str = ""
    MOONPAY_PUBLIC_KEY: str = ""

    CLOSE_CONTRACT_ADDRESS: str
    CLOSE_STAKING_CONTRACT_ADDRESS: str = ""
    MISTRAL_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    EXA_API_KEY: str = ""
    DEPOSIT_ADDRESS: str = "0x52b6e0aeD9511A4bCD0c5D454ccBe0EcF4308B7F"
    DEPOSIT_MIN_USD_POLYGON: float = 4.0
    DEPOSIT_MIN_USD_BSC: float = 4.0
    DEPOSIT_MIN_USD_ETHEREUM: float = 15.0
    CLOSE_PER_USD: float = 100.0
    DISTRIBUTION_WALLET_ADDRESS: str
    DISTRIBUTION_WALLET_PRIVATE_KEY: str
    RELAYER_WALLET_ADDRESS: str
    RELAYER_WALLET_PRIVATE_KEY: str
    STAKING_TREASURY_ENCRYPTED_KEY: str
    STAKING_TREASURY_PASSWORD: str

    BURN_PER_MESSAGE: int = 25
    FREE_CLOSE_AMOUNT: int = 500
    SWAP_FEE_PERCENT: float = 0.75
    BRIDGE_FEE_PERCENT: float = 0.3
    YIELD_FEE_PERCENT: float = 10.0

    SAFE_TRANSACTION_SERVICE_URL: str = "https://safe-transaction-polygon.safe.global"

    ENVIRONMENT: str = "development"
    SUPPORTED_CHAINS: List[str] = ["polygon", "ethereum", "bsc", "arbitrum", "base"]  # "bitcoin" removed: no real BTC support exists yet (wrong address format, was silently querying Ethereum)

    CLOUDFLARE_ACCOUNT_ID: str = ""
    CLOUDFLARE_API_KEY: str = ""

    def get_rpc_url(self, chain: str) -> str:
        """Primary RPC URL for a chain - prefers Alchemy if configured,
        falls back to the plain public endpoint otherwise. See
        get_rpc_urls() for the full primary+fallback list used by
        get_web3(), which is what actually matters for reliability."""
        if chain == "bsc":
            return self.BSC_RPC_URL
        if not self.ALCHEMY_API_KEY:
            return getattr(self, f"{chain.upper()}_RPC_URL", "")
        alchemy_chain_map = {
            "ethereum": "eth-mainnet",
            "polygon": "polygon-mainnet",
            "arbitrum": "arb-mainnet",
            "base": "base-mainnet",
        }
        return f"https://{alchemy_chain_map.get(chain, 'eth-mainnet')}.g.alchemy.com/v2/{self.ALCHEMY_API_KEY}"

    def get_rpc_urls(self, chain: str) -> list:
        """
        Ordered list of RPC URLs to try for this chain: Alchemy first (if
        configured), then Infura (if configured), then the plain public
        endpoint as a last resort. get_web3() tries each in order and uses
        the first one that actually responds - added 2026-08-20 after a
        free public RPC (polygon-rpc.com) returned a stale/incorrect zero
        balance during a real transaction.
        """
        urls = []

        alchemy_chain_map = {
            "ethereum": "eth-mainnet",
            "polygon": "polygon-mainnet",
            "arbitrum": "arb-mainnet",
            "base": "base-mainnet",
        }
        if self.ALCHEMY_API_KEY and chain in alchemy_chain_map:
            urls.append(f"https://{alchemy_chain_map[chain]}.g.alchemy.com/v2/{self.ALCHEMY_API_KEY}")

        infura_chain_map = {
            "ethereum": "mainnet",
            "polygon": "polygon-mainnet",
            "arbitrum": "arbitrum-mainnet",
            "base": "base-mainnet",
        }
        if self.INFURA_API_KEY and chain in infura_chain_map:
            urls.append(f"https://{infura_chain_map[chain]}.infura.io/v3/{self.INFURA_API_KEY}")

        fallback = getattr(self, f"{chain.upper()}_RPC_URL", None) if chain != "bsc" else self.BSC_RPC_URL
        if fallback:
            urls.append(fallback)

        return urls

settings = Settings()

SAFE_SINGLETON_ADDRESSES = {
    "polygon": "0x3E5c63644E683549055b9Be8653de26E0B4CD36E",
    "ethereum": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
    "bsc": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
    "arbitrum": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
    "base": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
}

SAFE_PROXY_FACTORY_ADDRESSES = {
    "polygon": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
    "ethereum": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
    "bsc": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
    "arbitrum": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
    "base": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
}

def get_safe_singleton(chain: str) -> str:
    return SAFE_SINGLETON_ADDRESSES.get(chain)

def get_safe_proxy_factory(chain: str) -> str:
    return SAFE_PROXY_FACTORY_ADDRESSES.get(chain)
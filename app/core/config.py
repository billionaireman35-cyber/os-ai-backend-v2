
import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str

    # Security
    JWT_SECRET: str
    FOUNDER_KEY: str
    ALLOWED_ORIGINS: List[str] = ["*"]

    # Email
    RESEND_API_KEY: str = ""
    FRONTEND_URL: str = "http://localhost:5173"

    # Blockchain
    POLYGON_RPC_URL: str = "https://polygon-rpc.com"
    ETHEREUM_RPC_URL: str = "https://eth.llamarpc.com"
    BSC_RPC_URL: str = "https://bsc-dataseed.binance.org"
    ARBITRUM_RPC_URL: str = "https://arb1.arbitrum.io/rpc"
    BASE_RPC_URL: str = "https://mainnet.base.org"
    ALCHEMY_API_KEY: str = ""

    # AI & Embeddings
    OPENROUTER_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-ada-002"
    MEMORY_RETRIEVAL_LIMIT: int = 5

    # APIs
    COINGECKO_KEY: str = ""
    NEWS_API_KEY: str = ""
    SERPAPI_KEY: str = ""
    ONEINCH_API_KEY: str = ""
    ONEINCH_BASE_URL: str = "https://business.1inch.com/swap/v5.0"

    # CLOSE Token
    CLOSE_CONTRACT_ADDRESS: str
    DISTRIBUTION_WALLET_ADDRESS: str
    DISTRIBUTION_WALLET_PRIVATE_KEY: str

    # Economics
    BURN_PER_MESSAGE: int = 25
    FREE_CLOSE_AMOUNT: int = 500
    SWAP_FEE_PERCENT: float = 0.75
    BRIDGE_FEE_PERCENT: float = 0.3
    YIELD_FEE_PERCENT: float = 10.0

    # Gnosis Safe
    SAFE_TRANSACTION_SERVICE_URL: str = "https://safe-transaction-polygon.safe.global"

    # Environment
    ENVIRONMENT: str = "development"
    SUPPORTED_CHAINS: List[str] = ["polygon", "ethereum", "bsc", "arbitrum", "base", "bitcoin"]

    def get_rpc_url(self, chain: str) -> str:
        """Return Alchemy RPC URL for the given chain, falling back to public RPCs."""
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


settings = Settings()

# Gnosis Safe deployments (official)
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
<<<<<<< HEAD
    return SAFE_PROXY_FACTORY_ADDRESSES.get(chain)

settings = Settings()
# In Settings class
ALCHEMY_API_KEY: str = ""

def get_rpc_url(self, chain: str) -> str:
    """Return Alchemy RPC URL for the given chain."""
    if not self.ALCHEMY_API_KEY:
        # Fallback to public RPCs (already defined)
        return getattr(self, f"{chain.upper()}_RPC_URL", "")
    alchemy_chain_map = {
        "ethereum": "eth-mainnet",
        "polygon": "polygon-mainnet",
        "arbitrum": "arb-mainnet",
        "base": "base-mainnet",
        "bsc": "bsc-mainnet",  # Alchemy supports BSC?
    }
    # Alchemy does not support BSC natively; we fallback to public for BSC
    if chain == "bsc":
        return settings.BSC_RPC_URL
    return f"https://{alchemy_chain_map.get(chain, 'eth-mainnet')}.g.alchemy.com/v2/{self.ALCHEMY_API_KEY}"

# In Settings class
ONEINCH_API_KEY: str = ""
ONEINCH_BASE_URL: str = "https://business.1inch.com/swap/v5.0"  # business endpoint

# Chain ID mapping for 1inch
ONEINCH_CHAIN_IDS = {
    "polygon": 137,
    "ethereum": 1,
    "bsc": 56,
    "arbitrum": 42161,
    "base": 8453,
}
=======
    return SAFE_PROXY_FACTORY_ADDRESSES.get(chain)
>>>>>>> fc9350bd8b5eb3182df3fa3f36d37f4899ea2b6b

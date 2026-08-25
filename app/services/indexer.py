# app/services/indexer.py - Self-contained (uses only get_web3 from blockchain.py)
import asyncio
import logging
from typing import Dict, Set, Optional
from web3 import Web3
from web3.middleware import geth_poa_middleware
from app.core.config import settings
from app.core.database import get_db
from app.services.blockchain import get_web3

logger = logging.getLogger(__name__)

# Full ERC-20 ABI including Transfer event and decimals
FULL_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "amount", "type": "uint256"}],
        "name": "burn",
        "outputs": [],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"}
        ],
        "name": "Transfer",
        "type": "event"
    }
]

def to_checksum_address(address: str) -> str:
    return Web3.to_checksum_address(address)

# Token configuration for supported chains
TOKEN_CONFIG = {
    "polygon": {
        "CLOSE": {
            "address": to_checksum_address(settings.CLOSE_CONTRACT_ADDRESS),
            "decimals": 18,
            "symbol": "CLOSE"
        },
        "USDC": {
            "address": to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
            "decimals": 6,
            "symbol": "USDC"
        },
        "USDT": {
            "address": to_checksum_address("0xc2132D05D31c914a87C6611C10748AEb04B58e8F"),
            "decimals": 6,
            "symbol": "USDT"
        }
    },
    "ethereum": {
        "USDC": {
            "address": to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
            "decimals": 6,
            "symbol": "USDC"
        },
        "USDT": {
            "address": to_checksum_address("0xdAC17F958D2ee523a2206206994597C13D831ec7"),
            "decimals": 6,
            "symbol": "USDT"
        }
    },
    "bsc": {
        "USDC": {
            "address": to_checksum_address("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"),
            "decimals": 18,
            "symbol": "USDC"
        },
        "USDT": {
            "address": to_checksum_address("0x55d398326f99059fF775485246999027B3197955"),
            "decimals": 18,
            "symbol": "USDT"
        }
    },
    "arbitrum": {
        "USDC": {
            "address": to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
            "decimals": 6,
            "symbol": "USDC"
        }
    },
    "base": {
        "USDC": {
            "address": to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
            "decimals": 6,
            "symbol": "USDC"
        }
    }
}

class ChainIndexer:
    def __init__(self, chain: str):
        self.chain = chain
        self.web3 = get_web3(chain)
        if chain in ["polygon", "bsc"]:
            self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.watched_addresses: Set[str] = set()
        self.token_addresses: Set[str] = set()
        self.last_processed_block: Optional[int] = None
        self.running = False
        self.poll_interval = 12
        self.confirmations = 2

    async def start(self):
        self.running = True
        logger.info(f"Starting indexer for {self.chain}")
        await self._refresh_watched_addresses()
        self._build_token_set()
        try:
            self.last_processed_block = self.web3.eth.block_number - 1000
        except:
            self.last_processed_block = 0
        while self.running:
            try:
                await self._poll()
            except Exception as e:
                logger.error(f"Indexer error for {self.chain}: {e}")
            await asyncio.sleep(self.poll_interval)

    def _build_token_set(self):
        tokens = TOKEN_CONFIG.get(self.chain, {})
        for token in tokens.values():
            self.token_addresses.add(token["address"])

    async def _refresh_watched_addresses(self):
        addresses = set()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT wallet_address FROM users WHERE wallet_address IS NOT NULL")
                for row in cur.fetchall():
                    if row[0]:
                        addresses.add(to_checksum_address(row[0]))
        if settings.DEPOSIT_ADDRESS:
            addresses.add(to_checksum_address(settings.DEPOSIT_ADDRESS))
        self.watched_addresses = addresses
        logger.info(f"Watching {len(addresses)} addresses and {len(self.token_addresses)} tokens on {self.chain}")

    async def _poll(self):
        latest = self.web3.eth.block_number
        if latest - self.confirmations <= self.last_processed_block:
            return
        start = self.last_processed_block + 1
        end = latest - self.confirmations
        for block_num in range(start, end + 1):
            await self._process_block(block_num)
            self.last_processed_block = block_num
        if block_num % 100 == 0:
            await self._refresh_watched_addresses()

    async def _process_block(self, block_num: int):
        try:
            block = self.web3.eth.get_block(block_num, full_transactions=True)
            timestamp = block.timestamp
            # Native transfers
            for tx in block.transactions:
                await self._process_native_transfer(tx, block_num, timestamp)
            # Token transfers via logs
            await self._process_token_transfers(block_num, timestamp)
        except Exception as e:
            logger.error(f"Error processing block {block_num}: {e}")

    async def _process_native_transfer(self, tx, block_num: int, timestamp: int):
        from_addr = to_checksum_address(tx['from'])
        to_addr = to_checksum_address(tx['to']) if tx['to'] else None
        if not (to_addr in self.watched_addresses or from_addr in self.watched_addresses):
            return
        tx_hash = tx['hash'].hex()
        value_wei = tx['value']
        if value_wei == 0:
            return

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM onchain_transactions WHERE chain = %s AND tx_hash = %s AND asset_address IS NULL",
                            (self.chain, tx_hash))
                if cur.fetchone():
                    return
                user_id = self._get_user_id(cur, from_addr, to_addr)
                asset_symbol = self._get_native_symbol(self.chain)
                cur.execute("""
                    INSERT INTO onchain_transactions
                    (user_id, chain, tx_hash, from_address, to_address, asset_symbol, asset_address, value_wei, block_number, timestamp, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), 'confirmed')
                """, (user_id, self.chain, tx_hash, from_addr, to_addr, asset_symbol, None, value_wei, block_num, timestamp))
                conn.commit()
        if user_id:
            await self._update_user_balance(user_id, self.chain, asset_symbol, None, from_addr, to_addr, value_wei)

    async def _process_token_transfers(self, block_num: int, timestamp: int):
        for token_addr in self.token_addresses:
            try:
                logs = self.web3.eth.get_logs({
                    "fromBlock": block_num,
                    "toBlock": block_num,
                    "address": token_addr,
                    "topics": [Web3.keccak(text="Transfer(address,address,uint256)")]
                })
                for log in logs:
                    await self._process_token_log(log, token_addr, block_num, timestamp)
            except Exception as e:
                logger.error(f"Error fetching token logs for {token_addr} in block {block_num}: {e}")

    async def _process_token_log(self, log, token_addr: str, block_num: int, timestamp: int):
        try:
            contract = self.web3.eth.contract(address=token_addr, abi=FULL_ERC20_ABI)
            event = contract.events.Transfer().process_log(log)
            from_addr = to_checksum_address(event['args']['from'])
            to_addr = to_checksum_address(event['args']['to'])
            value = event['args']['value']
            tx_hash = log['transactionHash'].hex()
        except Exception as e:
            logger.error(f"Error decoding token log: {e}")
            return

        if not (to_addr in self.watched_addresses or from_addr in self.watched_addresses):
            return

        token_info = self._get_token_info(token_addr)
        if not token_info:
            return
        asset_symbol = token_info["symbol"]
        # value is already in wei

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM onchain_transactions WHERE chain = %s AND tx_hash = %s AND asset_address = %s",
                            (self.chain, tx_hash, token_addr))
                if cur.fetchone():
                    return
                user_id = self._get_user_id(cur, from_addr, to_addr)
                cur.execute("""
                    INSERT INTO onchain_transactions
                    (user_id, chain, tx_hash, from_address, to_address, asset_symbol, asset_address, value_wei, block_number, timestamp, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), 'confirmed')
                """, (user_id, self.chain, tx_hash, from_addr, to_addr, asset_symbol, token_addr, value, block_num, timestamp))
                conn.commit()
        if user_id:
            await self._update_user_balance(user_id, self.chain, asset_symbol, token_addr, from_addr, to_addr, value)

    def _get_user_id(self, cursor, from_addr: str, to_addr: str) -> Optional[str]:
        cursor.execute("SELECT id FROM users WHERE wallet_address = %s", (to_addr,))
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute("SELECT id FROM users WHERE wallet_address = %s", (from_addr,))
        row = cursor.fetchone()
        if row:
            return row[0]
        return None

    def _get_native_symbol(self, chain: str) -> str:
        return {
            "polygon": "POL",
            "ethereum": "ETH",
            "bsc": "BNB",
            "arbitrum": "ETH",
            "base": "ETH",
        }.get(chain, chain.upper())

    def _get_token_info(self, token_addr: str):
        for tokens in TOKEN_CONFIG.get(self.chain, {}).values():
            if tokens["address"] == token_addr:
                return tokens
        return None

    async def _update_user_balance(self, user_id: str, chain: str, asset_symbol: str, asset_address: Optional[str],
                                   from_addr: str, to_addr: str, value_wei: int):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT wallet_address FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row or not row[0]:
                    return
                user_wallet = to_checksum_address(row[0])
                if to_addr == user_wallet:
                    delta = value_wei
                elif from_addr == user_wallet:
                    delta = -value_wei
                else:
                    return
                cur.execute("""
                    INSERT INTO user_balances (user_id, chain, asset_symbol, asset_address, balance, last_updated)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id, chain, asset_symbol, asset_address)
                    DO UPDATE SET balance = user_balances.balance + %s, last_updated = NOW()
                """, (user_id, chain, asset_symbol, asset_address, delta, delta))
                conn.commit()

class IndexerManager:
    def __init__(self):
        self.indexers: Dict[str, ChainIndexer] = {}
        self.tasks = []

    async def start_all(self):
        for chain in settings.SUPPORTED_CHAINS:
            idx = ChainIndexer(chain)
            self.indexers[chain] = idx
            task = asyncio.create_task(idx.start())
            self.tasks.append(task)
        logger.info(f"Started indexers for chains: {settings.SUPPORTED_CHAINS}")

    async def stop_all(self):
        for idx in self.indexers.values():
            await idx.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("Stopped all indexers")

indexer_manager = IndexerManager()

def init_indexer():
    from app.models.onchain import init_onchain_tables
    with get_db() as conn:
        init_onchain_tables(conn)

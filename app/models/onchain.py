# app/models/onchain.py
# SQL statements to create tables (idempotent)

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS user_balances (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chain VARCHAR(20) NOT NULL,
    asset_symbol VARCHAR(20) NOT NULL,
    asset_address VARCHAR(42),  -- NULL for native currency
    balance NUMERIC(78, 0) DEFAULT 0,  -- store in wei to avoid floating point
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, chain, asset_symbol, asset_address)
);

CREATE TABLE IF NOT EXISTS onchain_transactions (
    id SERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    chain VARCHAR(20) NOT NULL,
    tx_hash VARCHAR(66) NOT NULL,
    from_address VARCHAR(42) NOT NULL,
    to_address VARCHAR(42) NOT NULL,
    asset_symbol VARCHAR(20) NOT NULL,
    asset_address VARCHAR(42),  -- NULL for native
    value_wei NUMERIC(78, 0) NOT NULL,
    block_number BIGINT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(chain, tx_hash)
);

CREATE INDEX IF NOT EXISTS idx_onchain_tx_user ON onchain_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_onchain_tx_chain_block ON onchain_transactions(chain, block_number DESC);
CREATE INDEX IF NOT EXISTS idx_user_balances_user_chain ON user_balances(user_id, chain);
"""

def init_onchain_tables(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLES_SQL)
        conn.commit()

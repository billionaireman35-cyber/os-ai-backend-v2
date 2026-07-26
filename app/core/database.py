import logging
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool
from app.core.config import settings

logger = logging.getLogger(__name__)

# Connection pool
db_pool = None

def get_db_pool():
    global db_pool
    if db_pool is None:
        db_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=settings.DATABASE_URL,
            connect_timeout=10
        )
    return db_pool

@contextmanager
def get_db():
    pool = get_db_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

def init_db():
    """Initialize database tables and apply schema updates."""
    try:
        with get_db() as conn:
            with conn.cursor() as c:
                # Enable pgvector extension
                c.execute("CREATE EXTENSION IF NOT EXISTS vector")
                
                # ========== USERS & AUTH ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id UUID PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        name TEXT,
                        close_balance BIGINT DEFAULT 0,
                        close_staked BIGINT DEFAULT 0,
                        stake_tier TEXT DEFAULT 'none',
                        wallet_address TEXT,
                        wallet_encrypted_seed TEXT,
                        is_founder BOOLEAN DEFAULT FALSE,
                        device_fingerprint TEXT,
                        fingerprint_verified BOOLEAN DEFAULT FALSE,
                        last_active TIMESTAMP DEFAULT NOW(),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        token TEXT UNIQUE NOT NULL,
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS verification_codes (
                        email TEXT NOT NULL,
                        code TEXT NOT NULL,
                        purpose TEXT DEFAULT 'verification',
                        expires_at TIMESTAMP NOT NULL,
                        attempts INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_verification_email_purpose ON verification_codes (email, purpose)")
                
                # ========== CHAT & AI MEMORY ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS chats (
                        id TEXT PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        session_id TEXT,
                        title TEXT,
                        topic_thread TEXT,
                        created TIMESTAMP DEFAULT NOW(),
                        updated TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT REFERENCES chats(id) ON DELETE CASCADE,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        session_id TEXT,
                        role TEXT,
                        content TEXT,
                        model TEXT,
                        close_burned BIGINT DEFAULT 0,
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        content TEXT,
                        query TEXT,
                        domain TEXT,
                        importance INTEGER DEFAULT 1,
                        embedding vector(1536),
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS idx_memories_user_embedding ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);")
                
                # ========== TOKEN & FINANCE ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS close_transactions (
                        id UUID PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        type TEXT,
                        amount BIGINT,
                        tx_hash TEXT,
                        chain TEXT DEFAULT 'polygon',
                        status TEXT DEFAULT 'pending',
                        reference_id UUID,
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("ALTER TABLE close_transactions ADD COLUMN IF NOT EXISTS reference_id UUID")
                c.execute("CREATE INDEX IF NOT EXISTS idx_close_transactions_reference ON close_transactions (reference_id)")
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS revenue_logs (
                        id UUID PRIMARY KEY,
                        source TEXT,
                        amount_usd REAL,
                        close_equivalent BIGINT,
                        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS admin_events (
                        id UUID PRIMARY KEY,
                        admin_id UUID REFERENCES users(id) ON DELETE SET NULL,
                        action TEXT,
                        details JSONB,
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # ========== GNOSIS SAFE ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS user_safes (
                        id UUID PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        safe_address TEXT NOT NULL,
                        chain TEXT DEFAULT 'polygon',
                        owners JSONB NOT NULL,
                        threshold INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("CREATE INDEX idx_user_safes_user_id ON user_safes (user_id);")
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS safe_transactions (
                        id UUID PRIMARY KEY,
                        safe_address TEXT NOT NULL,
                        chain TEXT DEFAULT 'polygon',
                        to_address TEXT NOT NULL,
                        value TEXT NOT NULL,
                        data TEXT,
                        safe_tx_hash TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        threshold INTEGER NOT NULL,
                        signers JSONB DEFAULT '[]',
                        signatures JSONB DEFAULT '[]',
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMP DEFAULT NOW(),
                        executed_at TIMESTAMP
                    )
                """)
                c.execute("CREATE INDEX idx_safe_transactions_safe_address ON safe_transactions (safe_address)")
                
                # ========== HUSTLE HUBS (WORKSPACES) ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS workspaces (
                        id UUID PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        room_code TEXT UNIQUE NOT NULL,
                        password_hash TEXT,
                        owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        is_public BOOLEAN DEFAULT TRUE,
                        status TEXT DEFAULT 'pending',
                        fee_paid BOOLEAN DEFAULT FALSE,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_room_code ON workspaces (room_code)")
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS workspace_members (
                        workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        role TEXT DEFAULT 'member',
                        status TEXT DEFAULT 'pending',
                        joined_at TIMESTAMP DEFAULT NOW(),
                        PRIMARY KEY (workspace_id, user_id)
                    )
                """)
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS workspace_messages (
                        id UUID PRIMARY KEY,
                        workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        content TEXT NOT NULL,
                        is_ai BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("CREATE INDEX idx_workspace_messages_workspace ON workspace_messages (workspace_id, created_at)")
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS workspace_memories (
                        id UUID PRIMARY KEY,
                        workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
                        content TEXT,
                        query TEXT,
                        embedding vector(1536),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS workspace_invites (
                        id UUID PRIMARY KEY,
                        workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
                        email TEXT,
                        invite_code TEXT UNIQUE NOT NULL,
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # ========== WALLETCONNECT SESSIONS ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS walletconnect_sessions (
                        id UUID PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        topic TEXT NOT NULL,
                        dapp_name TEXT,
                        dapp_url TEXT,
                        chain_id INTEGER,
                        accounts JSONB,
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("CREATE INDEX idx_walletconnect_sessions_user ON walletconnect_sessions (user_id)")
                
                # ========== DEVELOPER TOOLS ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS api_keys (
                        id UUID PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        key_hash TEXT NOT NULL,
                        prefix TEXT NOT NULL,
                        label TEXT DEFAULT 'Unlabelled',
                        scopes TEXT DEFAULT 'chat,research,portfolio',
                        is_active BOOLEAN DEFAULT TRUE,
                        last_used TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("CREATE INDEX idx_api_keys_user ON api_keys (user_id)")
                
                c.execute("""
                    CREATE TABLE IF NOT EXISTS webhooks (
                        id UUID PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        url TEXT NOT NULL,
                        events TEXT DEFAULT 'new_message',
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # ========== MULTI-CHAIN WALLETS ==========
                c.execute("""
                    CREATE TABLE IF NOT EXISTS os_wallets (
                        id UUID PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        chain TEXT DEFAULT 'polygon',
                        address TEXT NOT NULL,
                        encrypted_key TEXT NOT NULL,
                        label TEXT DEFAULT 'Primary',
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # ========== SAFE COLUMN ADDITIONS (if missing) ==========
                c.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT TRUE")
                c.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'")
                c.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS fee_paid BOOLEAN DEFAULT FALSE")
                c.execute("ALTER TABLE workspace_members ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'")
                
                conn.commit()
                
        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.error(f"❌ Database init error: {e}")
        raise
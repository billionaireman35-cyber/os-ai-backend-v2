import logging
import ssl
from contextlib import contextmanager
import pg8000
from urllib.parse import urlparse
from app.core.config import settings

logger = logging.getLogger(__name__)

@contextmanager
def get_db():
    """Return a PostgreSQL connection using pg8000 with SSL."""
    url = urlparse(settings.DATABASE_URL)
    dbname = url.path[1:]
    user = url.username
    password = url.password
    host = url.hostname
    port = url.port or 5432

    ssl_context = ssl.create_default_context()

    conn = pg8000.connect(
        user=user,
        password=password,
        host=host,
        port=port,
        database=dbname,
        ssl_context=ssl_context,
    )
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Initialize database tables."""
    try:
        with get_db() as conn:
            with conn.cursor() as c:
                # Advisory lock so concurrent workers/deploys can't run
                # this DDL block at the same time and deadlock each other.
                c.execute("SELECT pg_advisory_lock(918273645)")
                c.execute("CREATE EXTENSION IF NOT EXISTS vector")

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
                        created_at TIMESTAMP DEFAULT NOW(),
                        profile_picture TEXT
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
                        created TIMESTAMP DEFAULT NOW(),
                        reactions JSONB DEFAULT '{}',
                        edited_at TIMESTAMP,
                        deleted_at TIMESTAMP
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS withdrawal_requests (
                        id TEXT PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        chain TEXT NOT NULL,
                        token_symbol TEXT NOT NULL,
                        amount NUMERIC NOT NULL,
                        destination_address TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        tx_hash TEXT,
                        created TIMESTAMP DEFAULT NOW(),
                        fulfilled_at TIMESTAMP
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS crypto_deposits (
                        id TEXT PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        chain TEXT NOT NULL,
                        tx_hash TEXT UNIQUE NOT NULL,
                        token_symbol TEXT NOT NULL,
                        amount NUMERIC NOT NULL,
                        usd_value NUMERIC NOT NULL,
                        close_credited BIGINT NOT NULL,
                        status TEXT DEFAULT 'confirmed',
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS workspace_payments (
                        id TEXT PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        workspace_id TEXT,
                        tx_hash TEXT UNIQUE NOT NULL,
                        purpose TEXT NOT NULL,
                        amount NUMERIC NOT NULL,
                        status TEXT DEFAULT 'confirmed',
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS chat_topups (
                        id TEXT PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        tx_hash TEXT UNIQUE NOT NULL,
                        amount BIGINT NOT NULL,
                        status TEXT DEFAULT 'confirmed',
                        created TIMESTAMP DEFAULT NOW()
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS stake_positions (
                        id TEXT PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        amount BIGINT NOT NULL,
                        term TEXT NOT NULL,
                        apy NUMERIC NOT NULL,
                        staked_at TIMESTAMP DEFAULT NOW(),
                        unlock_at TIMESTAMP,
                        status TEXT DEFAULT 'active',
                        unstaked_at TIMESTAMP,
                        yield_claimed BIGINT DEFAULT 0,
                        stake_tx_hash TEXT UNIQUE
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS message_reports (
                        id TEXT PRIMARY KEY,
                        message_id TEXT REFERENCES chat_messages(id) ON DELETE CASCADE,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        reason TEXT NOT NULL,
                        details TEXT,
                        status TEXT DEFAULT 'open',
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
                c.execute("CREATE INDEX IF NOT EXISTS idx_close_transactions_reference ON close_transactions (reference_id)")

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
                c.execute("CREATE INDEX IF NOT EXISTS idx_workspace_messages_workspace ON workspace_messages (workspace_id, created_at)")

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
                c.execute("CREATE INDEX IF NOT EXISTS idx_safe_transactions_safe_address ON safe_transactions (safe_address)")

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
                c.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys (user_id)")

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

                c.execute("""
                    CREATE TABLE IF NOT EXISTS push_subscriptions (
                        id UUID PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        endpoint TEXT NOT NULL,
                        auth_key TEXT NOT NULL,
                        p256dh_key TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)

                # Additional columns for existing tables (safe to run)
                c.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT TRUE")
                c.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'")
                c.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS fee_paid BOOLEAN DEFAULT FALSE")
                c.execute("ALTER TABLE close_transactions ADD COLUMN IF NOT EXISTS reference_id UUID")
                c.execute("ALTER TABLE workspace_members ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'")
                c.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS reactions JSONB DEFAULT '{}'")
                c.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP")
                c.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
                c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_picture TEXT")

                c.execute("SELECT pg_advisory_unlock(918273645)")
                conn.commit()
        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.error(f"❌ Database init error: {e}")
        raise

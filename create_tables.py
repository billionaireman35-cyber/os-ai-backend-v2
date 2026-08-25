from app.core.database import get_db
from app.models.onchain import init_onchain_tables

with get_db() as conn:
    init_onchain_tables(conn)
    print("✅ Tables created successfully (or already existed).")

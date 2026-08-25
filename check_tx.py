from app.core.database import get_db

tx_hash = "0x719d9a9b579a8012cee3962a71d7904d04acc80d2e340910367c2e0d3461fc77"
with get_db() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM onchain_transactions WHERE tx_hash = %s", (tx_hash,))
        row = cur.fetchone()
        if row:
            print("Transaction found in DB!")
            print(row)
        else:
            print("Not yet indexed. Wait for the indexer to catch up or resync.")

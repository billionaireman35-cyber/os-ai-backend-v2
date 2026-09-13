import sys
import types
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

# Stub heavy blockchain/transaction dependencies before importing safe_service.
blockchain_stub = types.ModuleType("app.services.blockchain")
blockchain_stub.get_web3 = MagicMock()
blockchain_stub.get_all_balances = MagicMock()
blockchain_stub.get_token_balance = MagicMock()
blockchain_stub.send_close_from_distribution = MagicMock()
blockchain_stub.ERC20_ABI = []
sys.modules.setdefault("app.services.blockchain", blockchain_stub)

transaction_stub = types.ModuleType("app.services.transaction")
transaction_stub.sign_transaction = MagicMock()
transaction_stub.broadcast_transaction = MagicMock()
transaction_stub.sign_safe_hash = MagicMock()
sys.modules.setdefault("app.services.transaction", transaction_stub)

coingecko_stub = types.ModuleType("app.services.coingecko_service")
coingecko_stub.get_token_price = MagicMock()
coingecko_stub.get_market_data_for_ids = MagicMock()
sys.modules.setdefault("app.services.coingecko_service", coingecko_stub)

from app.services import safe_service


USER_A = "user-a"
USER_B = "user-b"
WALLET_A = "wallet-a"
WALLET_B = "wallet-b"

ADDRESS_A = "0x1111111111111111111111111111111111111111"
ADDRESS_B = "0x2222222222222222222222222222222222222222"
NON_OWNER = "0x3333333333333333333333333333333333333333"

SAFE_ID = "safe-1"
TX_ID = "tx-1"


class FakeCursor:
    def __init__(self, rows_by_query):
        self.rows_by_query = rows_by_query
        self.last_query = ""
        self.last_args = None
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, args=None):
        self.last_query = " ".join(query.split())
        self.last_args = args

        for marker, result in self.rows_by_query:
            if marker in self.last_query:
                self._result = result
                return

        self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._result or []


class FakeConnection:
    def __init__(self, rows_by_query):
        self.rows_by_query = rows_by_query
        self.cursor_obj = FakeCursor(rows_by_query)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass


def fake_db(rows_by_query):
    @contextmanager
    def manager():
        yield FakeConnection(rows_by_query)

    return manager


class SafeWalletAuthorizationTests(unittest.TestCase):

    def test_proposer_must_be_selected_safe_owner(self):
        rows = [
            (
                "SELECT chain, address, owners, threshold FROM safes",
                ("polygon", ADDRESS_A, [ADDRESS_A, ADDRESS_B], 2),
            ),
        ]

        with patch.object(safe_service, "get_db", side_effect=fake_db(rows)), \
             patch.object(
                 safe_service,
                 "require_signing_wallet",
                 return_value={
                     "id": WALLET_B,
                     "address": NON_OWNER,
                     "wallet_type": "custodial",
                 },
             ):

            with self.assertRaisesRegex(ValueError, "Only an owner"):
                safe_service.propose_safe_transaction(
                    SAFE_ID,
                    USER_B,
                    "password",
                    ADDRESS_A,
                    1,
                    wallet_id=WALLET_B,
                )

    def test_proposer_uses_selected_wallet_identity(self):
        rows = [
            (
                "SELECT chain, address, owners, threshold FROM safes",
                ("polygon", ADDRESS_A, [ADDRESS_A, ADDRESS_B], 2),
            ),
        ]

        wallet = {
            "id": WALLET_B,
            "address": ADDRESS_B,
            "wallet_type": "custodial",
        }

        with patch.object(safe_service, "get_db", side_effect=fake_db(rows)), \
             patch.object(safe_service, "require_signing_wallet", return_value=wallet) as resolve, \
             patch.object(
                 safe_service,
                 "get_user_private_key",
                 return_value="0xprivate-b",
             ) as get_key,              patch.object(safe_service, "_safe_security_gate"):

            # Stop after identity/key selection; blockchain work is outside
            # this authorization regression test.
            with patch.object(
                safe_service,
                "get_web3",
                side_effect=RuntimeError("stop-after-identity"),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop-after-identity"):
                    safe_service.propose_safe_transaction(
                        SAFE_ID,
                        USER_B,
                        "password",
                        ADDRESS_A,
                        1,
                        wallet_id=WALLET_B,
                    )

        resolve.assert_called_once_with(
            user_id=USER_B,
            wallet_id=WALLET_B,
        )
        get_key.assert_called_once_with(
            user_id=USER_B,
            password="password",
            wallet_id=WALLET_B,
        )

    def test_sign_rejects_non_owner_selected_wallet(self):
        rows = [
            (
                "SELECT st.safe_id, st.safe_tx_hash, st.signatures, st.status",
                (
                    SAFE_ID,
                    "0xhash",
                    [{"owner": ADDRESS_A, "signature": "0xsig"}],
                    "pending",
                    USER_A,
                    [ADDRESS_A, ADDRESS_B],
                    2,
                    "polygon",
                    ADDRESS_A,
                ),
            ),
        ]

        wallet = {
            "id": WALLET_B,
            "address": NON_OWNER,
            "wallet_type": "custodial",
        }

        with patch.object(safe_service, "get_db", side_effect=fake_db(rows)), \
             patch.object(
                 safe_service,
                 "require_signing_wallet",
                 return_value=wallet,
             ):

            with self.assertRaisesRegex(ValueError, "Only an owner"):
                safe_service.sign_safe_transaction(
                    TX_ID,
                    USER_B,
                    "password",
                    wallet_id=WALLET_B,
                )

    def test_sign_uses_exact_selected_wallet_key(self):
        rows = [
            (
                "SELECT st.safe_id, st.safe_tx_hash, st.signatures, st.status",
                (
                    SAFE_ID,
                    "0xhash",
                    [{"owner": ADDRESS_A, "signature": "0x1111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111"}],
                    "pending",
                    USER_A,
                    [ADDRESS_A, ADDRESS_B],
                    2,
                    "polygon",
                    ADDRESS_A,
                ),
            ),
        ]

        wallet = {
            "id": WALLET_B,
            "address": ADDRESS_B,
            "wallet_type": "custodial",
        }

        with patch.object(safe_service, "get_db", side_effect=fake_db(rows)), \
             patch.object(
                 safe_service,
                 "require_signing_wallet",
                 return_value=wallet,
             ), \
             patch.object(
                 safe_service,
                 "get_user_private_key",
                 return_value="0xprivate-b",
             ) as get_key, \
             patch.object(safe_service, "_safe_security_gate"), \
             patch.object(
                 safe_service,
                 "sign_safe_hash",
                 return_value="0x2222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222",
             ):

            result = safe_service.sign_safe_transaction(
                TX_ID,
                USER_B,
                "password",
                wallet_id=WALLET_B,
            )

        get_key.assert_called_once_with(
            user_id=USER_B,
            password="password",
            wallet_id=WALLET_B,
        )
        self.assertEqual(result["signatures_collected"], 2)
        self.assertTrue(result["ready_to_execute"])

    def test_executor_must_be_safe_owner(self):
        rows = [
            (
                "SELECT st.safe_id, st.to_address, st.value_wei, st.data, st.safe_nonce",
                (
                    SAFE_ID,
                    ADDRESS_A,
                    "1",
                    "0x",
                    0,
                    [{"owner": ADDRESS_A, "signature": "0xsig"}],
                    "pending",
                    USER_A,
                    "0xhash",
                    "polygon",
                    ADDRESS_A,
                    1,
                    [ADDRESS_A],
                ),
            ),
        ]

        wallet = {
            "id": WALLET_B,
            "address": NON_OWNER,
            "wallet_type": "custodial",
        }

        with patch.object(safe_service, "get_db", side_effect=fake_db(rows)), \
             patch.object(
                 safe_service,
                 "require_signing_wallet",
                 return_value=wallet,
             ):

            with self.assertRaisesRegex(ValueError, "Only an owner"):
                safe_service.execute_safe_transaction(
                    TX_ID,
                    USER_B,
                    "password",
                    wallet_id=WALLET_B,
                )

    def test_executor_uses_selected_wallet_as_gas_signer(self):
        rows = [
            (
                "SELECT st.safe_id, st.to_address, st.value_wei, st.data, st.safe_nonce",
                (
                    SAFE_ID,
                    ADDRESS_A,
                    "1",
                    "0x",
                    0,
                    [{"owner": ADDRESS_A, "signature": "0x1111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111"}],
                    "pending",
                    USER_A,
                    "0xhash",
                    "polygon",
                    ADDRESS_A,
                    1,
                    [ADDRESS_A, ADDRESS_B],
                ),
            ),
        ]

        wallet = {
            "id": WALLET_B,
            "address": ADDRESS_B,
            "wallet_type": "custodial",
        }

        with patch.object(safe_service, "get_db", side_effect=fake_db(rows)), \
             patch.object(
                 safe_service,
                 "require_signing_wallet",
                 return_value=wallet,
             ), \
             patch.object(
                 safe_service,
                 "get_user_private_key",
                 return_value="0xprivate-b",
             ) as get_key, \
             patch.object(safe_service, "_safe_security_gate"), \
             patch.object(
                 safe_service,
                 "get_web3",
             ) as get_web3, \
             patch.object(
                 safe_service,
                 "sign_transaction",
                 return_value="0xsigned",
             ) as sign_tx, \
             patch.object(
                 safe_service,
                 "broadcast_transaction",
                 return_value="0xexec",
             ):

            contract = get_web3.return_value.eth.contract.return_value
            contract.encodeABI.return_value = "0xexecdata"

            result = safe_service.execute_safe_transaction(
                TX_ID,
                USER_B,
                "password",
                wallet_id=WALLET_B,
            )

        get_key.assert_called_once_with(
            user_id=USER_B,
            password="password",
            wallet_id=WALLET_B,
        )

        sign_tx.assert_called_once()
        self.assertEqual(
            sign_tx.call_args.kwargs["from_address"],
            ADDRESS_B,
        )
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["exec_tx_hash"], "0xexec")


class SafePersistenceTests(unittest.TestCase):

    def test_proposal_insert_persists_proposer_wallet_id(self):
        rows = [
            (
                "SELECT chain, address, owners, threshold FROM safes",
                ("polygon", ADDRESS_A, [ADDRESS_A], 1),
            ),
        ]

        wallet = {
            "id": WALLET_A,
            "address": ADDRESS_A,
            "wallet_type": "custodial",
        }

        with patch.object(safe_service, "get_db", side_effect=fake_db(rows)), \
             patch.object(safe_service, "require_signing_wallet", return_value=wallet), \
             patch.object(safe_service, "get_user_private_key", return_value="0xprivate"), \
             patch.object(safe_service, "get_web3") as get_web3, \
             patch.object(safe_service, "sign_safe_hash", return_value="0xsig"):

            contract = get_web3.return_value.eth.contract.return_value
            contract.functions.nonce.return_value.call.return_value = 7
            contract.functions.getTransactionHash.return_value.call.return_value = b"\x01" * 32

            fake_connection = FakeConnection(rows)
            with patch.object(
                safe_service,
                "get_db",
                return_value=fake_db(rows).__enter__ if False else fake_db(rows),
            ):
                pass

        # Structural regression check: the source must retain the wallet ID
        # in the proposal INSERT, independently of database integration.
        source = Path("app/services/safe_service.py").read_text(encoding="utf-8")
        self.assertIn(
            "proposer_user_id, proposer_wallet_id, to_address",
            source,
        )
        self.assertIn(
            "proposer_wallet[\"id\"]",
            source,
        )

    def test_executor_update_persists_executor_wallet_id(self):
        source = Path("app/services/safe_service.py").read_text(encoding="utf-8")

        self.assertIn(
            "exec_tx_hash = %s, executor_wallet_id = %s, executed_at = NOW()",
            source,
        )
        self.assertIn(
            'executor_wallet["id"], tx_id',
            source,
        )


if __name__ == "__main__":
    unittest.main()

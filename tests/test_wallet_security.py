import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4


# wallet_service imports blockchain/coingecko modules at import time.
# Stub only those heavy modules so these unit tests remain dependency-free.
blockchain_stub = types.ModuleType("app.services.blockchain")
blockchain_stub.get_all_balances = MagicMock()
blockchain_stub.get_token_balance = MagicMock()
blockchain_stub.send_close_from_distribution = MagicMock()
blockchain_stub.get_web3 = MagicMock()
blockchain_stub.ERC20_ABI = []
sys.modules.setdefault("app.services.blockchain", blockchain_stub)

transaction_stub = types.ModuleType("app.services.transaction")
transaction_stub.sign_transaction = MagicMock()
transaction_stub.broadcast_transaction = MagicMock()
sys.modules.setdefault("app.services.transaction", transaction_stub)

coingecko_stub = types.ModuleType("app.services.coingecko_service")
coingecko_stub.get_token_price = MagicMock()
coingecko_stub.get_market_data_for_ids = MagicMock()
sys.modules.setdefault("app.services.coingecko_service", coingecko_stub)

from app.services import wallet_identity
from app.services import wallet_service


USER_ID = str(uuid4())
OTHER_USER_ID = str(uuid4())

PRIMARY_ID = str(uuid4())
IMPORTED_ID = str(uuid4())
CONNECTED_ID = str(uuid4())
FOREIGN_ID = str(uuid4())

PRIMARY_ADDRESS = "0x1111111111111111111111111111111111111111"
IMPORTED_ADDRESS = "0x2222222222222222222222222222222222222222"
CONNECTED_ADDRESS = "0x3333333333333333333333333333333333333333"
FOREIGN_ADDRESS = "0x4444444444444444444444444444444444444444"


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.cursor_obj = FakeCursor(row)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self, row):
        self.conn = FakeConnection(row)

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


def wallet_row(
    wallet_id,
    user_id,
    address,
    wallet_type="custodial",
    is_active=True,
    encrypted_key="encrypted-key",
    primary_address=PRIMARY_ADDRESS,
):
    return (
        wallet_id,
        user_id,
        "polygon",
        address,
        "Test Wallet",
        wallet_type,
        is_active,
        encrypted_key,
        primary_address,
    )


class WalletIdentityTests(unittest.TestCase):
    def test_primary_wallet_resolves_explicitly(self):
        row = wallet_row(
            PRIMARY_ID,
            USER_ID,
            PRIMARY_ADDRESS,
            primary_address=PRIMARY_ADDRESS,
        )

        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(row),
        ):
            result = wallet_identity.resolve_wallet_identity(USER_ID)

        self.assertEqual(result["id"], PRIMARY_ID)
        self.assertEqual(result["address"], PRIMARY_ADDRESS)
        self.assertTrue(result["is_primary"])
        self.assertTrue(result["can_sign"])

    def test_imported_wallet_resolves_by_wallet_id(self):
        row = wallet_row(
            IMPORTED_ID,
            USER_ID,
            IMPORTED_ADDRESS,
            primary_address=PRIMARY_ADDRESS,
        )

        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(row),
        ):
            result = wallet_identity.require_signing_wallet(
                USER_ID,
                wallet_id=IMPORTED_ID,
                wallet_address=IMPORTED_ADDRESS,
            )

        self.assertEqual(result["id"], IMPORTED_ID)
        self.assertEqual(result["address"], IMPORTED_ADDRESS)
        self.assertFalse(result["is_primary"])
        self.assertTrue(result["can_sign"])

    def test_wallet_id_and_address_mismatch_is_rejected(self):
        row = wallet_row(
            IMPORTED_ID,
            USER_ID,
            IMPORTED_ADDRESS,
            primary_address=PRIMARY_ADDRESS,
        )

        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(row),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Wallet ID and wallet address do not match",
            ):
                wallet_identity.require_signing_wallet(
                    USER_ID,
                    wallet_id=IMPORTED_ID,
                    wallet_address=PRIMARY_ADDRESS,
                )

    def test_foreign_wallet_id_is_rejected(self):
        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(None),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Wallet not found or not owned by this user",
            ):
                wallet_identity.require_signing_wallet(
                    USER_ID,
                    wallet_id=FOREIGN_ID,
                )

    def test_inactive_wallet_is_rejected(self):
        row = wallet_row(
            IMPORTED_ID,
            USER_ID,
            IMPORTED_ADDRESS,
            is_active=False,
            primary_address=PRIMARY_ADDRESS,
        )

        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(row),
        ):
            with self.assertRaisesRegex(ValueError, "Wallet is inactive"):
                wallet_identity.require_signing_wallet(
                    USER_ID,
                    wallet_id=IMPORTED_ID,
                )

    def test_connected_wallet_cannot_backend_sign(self):
        row = wallet_row(
            CONNECTED_ID,
            USER_ID,
            CONNECTED_ADDRESS,
            wallet_type="connected",
            encrypted_key=None,
            primary_address=PRIMARY_ADDRESS,
        )

        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(row),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Connected wallets must sign externally",
            ):
                wallet_identity.require_signing_wallet(
                    USER_ID,
                    wallet_id=CONNECTED_ID,
                    wallet_address=CONNECTED_ADDRESS,
                )

    def test_connected_wallet_with_key_material_is_blocked(self):
        row = wallet_row(
            CONNECTED_ID,
            USER_ID,
            CONNECTED_ADDRESS,
            wallet_type="connected",
            encrypted_key="unexpected-key",
            primary_address=PRIMARY_ADDRESS,
        )

        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(row),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Connected wallet has backend key material",
            ):
                wallet_identity.require_signing_wallet(
                    USER_ID,
                    wallet_id=CONNECTED_ID,
                )

    def test_custodial_wallet_without_key_cannot_sign(self):
        row = wallet_row(
            IMPORTED_ID,
            USER_ID,
            IMPORTED_ADDRESS,
            wallet_type="custodial",
            encrypted_key=None,
            primary_address=PRIMARY_ADDRESS,
        )

        with patch.object(
            wallet_identity,
            "get_db",
            return_value=FakeDB(row),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Wallet is not configured for backend signing",
            ):
                wallet_identity.require_signing_wallet(
                    USER_ID,
                    wallet_id=IMPORTED_ID,
                )


class WalletServiceSigningTests(unittest.TestCase):
    def test_private_key_lookup_uses_exact_selected_wallet_id(self):
        encrypted_key = "encrypted-selected-wallet"

        identity = {
            "id": IMPORTED_ID,
            "user_id": USER_ID,
            "chain": "polygon",
            "address": IMPORTED_ADDRESS,
            "label": "Imported",
            "wallet_type": "custodial",
            "is_active": True,
            "can_sign": True,
            "is_primary": False,
        }

        key_conn = FakeConnection((encrypted_key,))

        with patch.object(
            wallet_service,
            "require_signing_wallet",
            return_value=identity,
        ), patch.object(
            wallet_service,
            "get_db",
            return_value=key_conn,
        ), patch.object(
            wallet_service,
            "_decrypt_private_key",
            return_value="selected-private-key",
        ) as decrypt_mock:
            result = wallet_service.get_user_private_key(
                USER_ID,
                "correct-password",
                wallet_id=IMPORTED_ID,
            )

        self.assertEqual(result, "selected-private-key")
        decrypt_mock.assert_called_once_with(
            encrypted_key,
            "correct-password",
        )

        query, params = key_conn.cursor_obj.queries[0]
        self.assertIn("WHERE id = %s", query)
        self.assertEqual(params, (IMPORTED_ID, USER_ID))

    def test_send_transaction_signs_with_selected_wallet(self):
        identity = {
            "id": IMPORTED_ID,
            "user_id": USER_ID,
            "chain": "polygon",
            "address": IMPORTED_ADDRESS,
            "label": "Imported",
            "wallet_type": "custodial",
            "is_active": True,
            "can_sign": True,
            "is_primary": False,
        }

        fake_db = FakeDB(("encrypted-key",))

        with patch.object(
            wallet_service,
            "require_signing_wallet",
            return_value=identity,
        ), patch.object(
            wallet_service,
            "get_user_private_key",
            return_value="selected-private-key",
        ), patch.object(
            wallet_service,
            "get_web3",
        ) as web3_mock, patch(
            "app.services.transaction.sign_transaction",
            return_value="0xsigned",
        ) as sign_mock, patch(
            "app.services.transaction.broadcast_transaction",
            return_value="0xtxhash",
        ) as broadcast_mock, patch.object(
            wallet_service,
            "get_db",
            return_value=fake_db,
        ):
            result = wallet_service.send_transaction(
                user_id=USER_ID,
                password="correct-password",
                chain="polygon",
                to_address=FOREIGN_ADDRESS,
                amount_wei=1,
                wallet_id=IMPORTED_ID,
            )

        self.assertEqual(result, "0xtxhash")
        web3_mock.assert_not_called()

        sign_mock.assert_called_once()
        sign_kwargs = sign_mock.call_args.kwargs
        self.assertEqual(sign_kwargs["from_address"], IMPORTED_ADDRESS)
        self.assertEqual(sign_kwargs["private_key_hex"], "selected-private-key")

        broadcast_mock.assert_called_once_with("polygon", "0xsigned")

        self.assertIn(IMPORTED_ADDRESS, str(fake_db.conn.cursor_obj.queries))
        self.assertNotIn(PRIMARY_ADDRESS, str(sign_kwargs))

    def test_swap_signs_with_selected_wallet(self):
        identity = {
            "id": IMPORTED_ID,
            "user_id": USER_ID,
            "chain": "polygon",
            "address": IMPORTED_ADDRESS,
            "label": "Imported",
            "wallet_type": "custodial",
            "is_active": True,
            "can_sign": True,
            "is_primary": False,
        }

        fake_db = FakeDB(None)

        with patch.object(
            wallet_service,
            "require_signing_wallet",
            return_value=identity,
        ), patch.object(
            wallet_service,
            "get_user_private_key",
            return_value="selected-private-key",
        ), patch(
            "app.services.transaction.sign_transaction",
            return_value="0xsigned",
        ) as sign_mock, patch(
            "app.services.transaction.broadcast_transaction",
            return_value="0xswap",
        ) as broadcast_mock, patch.object(
            wallet_service,
            "get_db",
            return_value=fake_db,
        ):
            result = wallet_service.sign_and_broadcast_swap(
                user_id=USER_ID,
                password="correct-password",
                chain="polygon",
                to_address=FOREIGN_ADDRESS,
                data="0xdeadbeef",
                value_wei=123,
                wallet_id=IMPORTED_ID,
                wallet_address=IMPORTED_ADDRESS,
            )

        self.assertEqual(result, "0xswap")

        sign_kwargs = sign_mock.call_args.kwargs
        self.assertEqual(sign_kwargs["from_address"], IMPORTED_ADDRESS)
        self.assertEqual(sign_kwargs["private_key_hex"], "selected-private-key")
        broadcast_mock.assert_called_once_with("polygon", "0xsigned")


if __name__ == "__main__":
    unittest.main()

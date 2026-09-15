import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.core.security import create_token, get_current_user


class FakeCursor:
    def __init__(self, session_row, user_row):
        self.session_row = session_row
        self.user_row = user_row
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, args=None):
        self.result = (
            self.session_row
            if "FROM user_sessions" in query
            else self.user_row
        )

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, session_row, user_row):
        self.cursor_obj = FakeCursor(session_row, user_row)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj


class FakeRequest:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}


class AuthAssuranceTests(unittest.TestCase):
    def check_strength(self, base_strength, step_up_expires_at, expected):
        token = create_token("user-a")

        session_row = (
            "password",
            base_strength,
            False,
            step_up_expires_at,
        )

        user_row = (
            "user-a",
            "user@example.com",
            "User",
            0,
            0,
            "none",
            "",
            "",
            False,
            None,
            "USD",
            None,
        )

        db = FakeConnection(session_row, user_row)

        with patch("app.core.database.get_db", return_value=db):
            user = get_current_user(FakeRequest(token))

        self.assertIsNotNone(user)
        self.assertEqual(user["authentication_strength"], expected)
        self.assertFalse(user["device_trusted"])

    def test_active_step_up_elevates_to_100(self):
        expires = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.check_strength(70, expires, 100)

    def test_expired_step_up_restores_base_strength(self):
        expires = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.check_strength(70, expires, 70)

    def test_active_step_up_elevates_google_strength(self):
        expires = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.check_strength(80, expires, 100)

    def test_expired_step_up_restores_google_strength(self):
        expires = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.check_strength(80, expires, 80)


if __name__ == "__main__":
    unittest.main()

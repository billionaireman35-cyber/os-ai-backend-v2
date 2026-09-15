import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.auth import step_up_authentication
from app.security.passcode import PasscodeInvalid, PasscodeLocked


class FakeCursor:
    rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, args=None):
        pass

    def fetchone(self):
        return ("00000000-0000-0000-0000-000000000099",)


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass


def fake_request(token="session-token"):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/step-up",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 12345),
        "query_string": b"",
    }
    return Request(scope)


class FakeRate:
    allowed = True
    retry_after_seconds = 0


class FakeBlockedRate:
    allowed = False
    retry_after_seconds = 120


class StepUpPasscodeTests(unittest.TestCase):

    def user(self, auth_method="password"):
        return {
            "id": "00000000-0000-0000-0000-000000000001",
            "auth_method": auth_method,
            "device_fingerprint": "device-a",
        }

    def test_passcode_succeeds_for_password_session(self):
        request = fake_request()
        db = FakeConnection()

        with patch("app.api.v1.auth.get_current_user", return_value=self.user("password")), \
             patch("app.api.v1.auth.get_current_session_token", return_value="session-token"), \
             patch("app.security.rate_limit.check_rate_limit", return_value=FakeRate()), \
             patch("app.api.v1.auth.get_db", return_value=db), \
             patch("app.security.audit.get_db", return_value=db), \
             patch("app.security.passcode.verify_passcode") as verify, \
             patch("app.security.audit.record_security_event", create=True) as audit:

            result = asyncio.run(
                step_up_authentication(
                    {"passcode": "123456789"},
                    request,
                )
            )

        verify.assert_called_once_with("00000000-0000-0000-0000-000000000001", "123456789")
        self.assertTrue(result["success"])
        self.assertEqual(result["authentication_strength"], 100)
        self.assertIn("step_up_expires_at", result)
        audit.assert_called_once()

    def test_passcode_succeeds_for_google_session(self):
        request = fake_request()
        db = FakeConnection()

        with patch("app.api.v1.auth.get_current_user", return_value=self.user("google")), \
             patch("app.api.v1.auth.get_current_session_token", return_value="session-token"), \
             patch("app.security.rate_limit.check_rate_limit", return_value=FakeRate()), \
             patch("app.api.v1.auth.get_db", return_value=db), \
             patch("app.security.audit.get_db", return_value=db), \
             patch("app.security.passcode.verify_passcode") as verify:

            result = asyncio.run(
                step_up_authentication(
                    {"passcode": "123456789"},
                    request,
                )
            )

        verify.assert_called_once_with("00000000-0000-0000-0000-000000000001", "123456789")
        self.assertTrue(result["success"])
        self.assertEqual(result["authentication_strength"], 100)

    def test_invalid_passcode_returns_401_and_does_not_elevate(self):
        request = fake_request()
        db = FakeConnection()

        with patch("app.api.v1.auth.get_current_user", return_value=self.user()), \
             patch("app.api.v1.auth.get_current_session_token", return_value="session-token"), \
             patch("app.security.rate_limit.check_rate_limit", return_value=FakeRate()), \
             patch("app.api.v1.auth.get_db", return_value=db), \
             patch("app.security.audit.get_db", return_value=db), \
             patch(
                 "app.security.passcode.verify_passcode",
                 side_effect=PasscodeInvalid("invalid"),
             ), \
             patch("app.security.audit.record_security_event", create=True) as audit:

            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    step_up_authentication(
                        {"passcode": "000000000"},
                        request,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Invalid step-up credentials")
        audit.assert_called_once()

    def test_locked_passcode_returns_401(self):
        request = fake_request()
        db = FakeConnection()

        with patch("app.api.v1.auth.get_current_user", return_value=self.user()), \
             patch("app.api.v1.auth.get_current_session_token", return_value="session-token"), \
             patch("app.security.rate_limit.check_rate_limit", return_value=FakeRate()), \
             patch("app.api.v1.auth.get_db", return_value=db), \
             patch("app.security.audit.get_db", return_value=db), \
             patch(
                 "app.security.passcode.verify_passcode",
                 side_effect=PasscodeLocked("locked"),
             ):

            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    step_up_authentication(
                        {"passcode": "123456789"},
                        request,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 401)

    def test_step_up_rate_limit_blocks_before_passcode(self):
        request = fake_request()

        with patch("app.api.v1.auth.get_current_user", return_value=self.user()), \
             patch("app.api.v1.auth.get_current_session_token", return_value="session-token"), \
             patch("app.security.rate_limit.check_rate_limit", return_value=FakeBlockedRate()), \
             patch("app.security.audit.record_security_event", create=True), \
             patch("app.security.passcode.verify_passcode") as verify:

            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    step_up_authentication(
                        {"passcode": "123456789"},
                        request,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 429)
        verify.assert_not_called()

    def test_missing_passcode_returns_400(self):
        request = fake_request()
        db = FakeConnection()

        with patch("app.api.v1.auth.get_current_user", return_value=self.user()), \
             patch("app.api.v1.auth.get_current_session_token", return_value="session-token"), \
             patch("app.security.rate_limit.check_rate_limit", return_value=FakeRate()), \
             patch("app.api.v1.auth.get_db", return_value=db):

            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    step_up_authentication(
                        {"passcode": ""},
                        request,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

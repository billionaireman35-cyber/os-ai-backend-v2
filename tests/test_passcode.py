import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bcrypt

from app.security.passcode import (
    PasscodeAlreadyExists,
    PasscodeInvalid,
    PasscodeLocked,
    change_passcode,
    create_passcode,
    has_passcode,
    validate_passcode,
    verify_passcode,
)


class FakeCursor:
    def __init__(self, row=None, insert_id="passcode-id"):
        self.row = row
        self.insert_id = insert_id
        self.result = None
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, args=None):
        query = " ".join(query.split())

        if "INSERT INTO security_passcodes" in query:
            if "RETURNING id" in query:
                self.result = (self.insert_id,) if self.insert_id else None
            return

        if "SELECT verifier, failed_attempts, locked_until" in query:
            self.result = self.row
            return

        if "SELECT 1 FROM security_passcodes" in query:
            self.result = (1,) if self.row is not None else None
            return

        if "UPDATE security_passcodes" in query:
            self.updates.append((query, args))
            return

        self.result = None

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, row=None, insert_id="passcode-id"):
        self.cursor_obj = FakeCursor(row, insert_id)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def bcrypt_verifier(passcode):
    return bcrypt.hashpw(
        passcode.encode(),
        bcrypt.gensalt(),
    ).decode()


class PasscodeValidationTests(unittest.TestCase):
    def test_boundaries(self):
        validate_passcode("123456789")
        validate_passcode("123456789012345")

    def test_invalid_lengths_and_characters(self):
        for value in (
            "12345678",
            "1234567890123456",
            "12345678a",
            "",
        ):
            with self.assertRaises(PasscodeInvalid):
                validate_passcode(value)


class PasscodeCreateTests(unittest.TestCase):
    def test_create_passcode(self):
        db = FakeConnection()

        with patch("app.security.passcode.get_db", return_value=db):
            create_passcode("user-a", "123456789")

        self.assertEqual(db.commits, 1)
        query, args = db.cursor_obj.updates[0] if db.cursor_obj.updates else (None, None)
        self.assertIsNone(query)

    def test_create_rejects_existing(self):
        db = FakeConnection(insert_id=None)

        with patch("app.security.passcode.get_db", return_value=db):
            with self.assertRaises(PasscodeAlreadyExists):
                create_passcode("user-a", "123456789")


class PasscodeVerificationTests(unittest.TestCase):
    def test_correct_passcode(self):
        row = (
            bcrypt_verifier("123456789"),
            0,
            None,
        )
        db = FakeConnection(row=row)

        with patch("app.security.passcode.get_db", return_value=db):
            verify_passcode("user-a", "123456789")

        self.assertEqual(db.commits, 1)
        self.assertTrue(
            any("SET failed_attempts = 0" in query for query, _ in db.cursor_obj.updates)
        )

    def test_wrong_passcode_increments_failures(self):
        row = (
            bcrypt_verifier("123456789"),
            0,
            None,
        )
        db = FakeConnection(row=row)

        with patch("app.security.passcode.get_db", return_value=db):
            with self.assertRaises(PasscodeInvalid):
                verify_passcode("user-a", "111111111")

        update = db.cursor_obj.updates[-1]
        self.assertEqual(update[1][0], 1)
        self.assertIsNone(update[1][1])

    def test_fifth_failure_locks(self):
        row = (
            bcrypt_verifier("123456789"),
            4,
            None,
        )
        db = FakeConnection(row=row)

        with patch("app.security.passcode.get_db", return_value=db):
            with self.assertRaises(PasscodeLocked):
                verify_passcode("user-a", "111111111")

        update = db.cursor_obj.updates[-1]
        self.assertEqual(update[1][0], 5)
        self.assertIsNotNone(update[1][1])

    def test_locked_passcode_rejects_without_bcrypt(self):
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)

        row = (
            bcrypt_verifier("123456789"),
            5,
            locked_until,
        )
        db = FakeConnection(row=row)

        with patch("app.security.passcode.get_db", return_value=db), \
             patch("app.security.passcode.bcrypt.checkpw") as checkpw:

            with self.assertRaises(PasscodeLocked):
                verify_passcode("user-a", "123456789")

        checkpw.assert_not_called()

    def test_missing_passcode(self):
        db = FakeConnection(row=None)

        with patch("app.security.passcode.get_db", return_value=db):
            with self.assertRaises(PasscodeInvalid):
                verify_passcode("user-a", "123456789")


class PasscodeChangeTests(unittest.TestCase):
    def test_change_requires_current_passcode(self):
        row = (
            bcrypt_verifier("123456789"),
            0,
            None,
        )
        db = FakeConnection(row=row)

        with patch("app.security.passcode.get_db", return_value=db):
            change_passcode("user-a", "123456789", "987654321")

        self.assertEqual(db.commits, 1)
        self.assertTrue(
            any("SET verifier =" in query for query, _ in db.cursor_obj.updates)
        )

    def test_change_rejects_same_passcode(self):
        with self.assertRaises(PasscodeInvalid):
            change_passcode("user-a", "123456789", "123456789")

    def test_change_rejects_wrong_current_passcode(self):
        row = (
            bcrypt_verifier("123456789"),
            0,
            None,
        )
        db = FakeConnection(row=row)

        with patch("app.security.passcode.get_db", return_value=db):
            with self.assertRaises(PasscodeInvalid):
                change_passcode("user-a", "111111111", "987654321")


class PasscodeExistenceTests(unittest.TestCase):
    def test_has_passcode_true(self):
        db = FakeConnection(row=("exists",))

        with patch("app.security.passcode.get_db", return_value=db):
            self.assertTrue(has_passcode("user-a"))

    def test_has_passcode_false(self):
        db = FakeConnection(row=None)

        with patch("app.security.passcode.get_db", return_value=db):
            self.assertFalse(has_passcode("user-a"))


if __name__ == "__main__":
    unittest.main()

import bcrypt
import re
from datetime import timedelta

from app.core.database import get_db
from app.core.security import now_utc


PASSCODE_MIN_LENGTH = 9
PASSCODE_MAX_LENGTH = 15
PASSCODE_PATTERN = re.compile(r"^\d{9,15}$")

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


class PasscodeError(Exception):
    pass


class PasscodeLocked(PasscodeError):
    pass


class PasscodeInvalid(PasscodeError):
    pass


class PasscodeAlreadyExists(PasscodeError):
    pass


def validate_passcode(passcode: str) -> None:
    if (
        not isinstance(passcode, str)
        or len(passcode) < PASSCODE_MIN_LENGTH
        or len(passcode) > PASSCODE_MAX_LENGTH
        or not PASSCODE_PATTERN.fullmatch(passcode)
    ):
        raise PasscodeInvalid("Passcode must contain 9 to 15 digits")


def _hash_passcode(passcode: str) -> str:
    return bcrypt.hashpw(
        passcode.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


def create_passcode(user_id: str, passcode: str) -> None:
    validate_passcode(passcode)
    verifier = _hash_passcode(passcode)
    now = now_utc()

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO security_passcodes
                    (user_id, verifier, failed_attempts, created_at, updated_at)
                VALUES (%s, %s, 0, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
                RETURNING id
                """,
                (user_id, verifier, now, now),
            )

            if c.fetchone() is None:
                raise PasscodeAlreadyExists(
                    "Security passcode is already configured"
                )

        conn.commit()


def change_passcode(
    user_id: str,
    current_passcode: str,
    new_passcode: str,
) -> None:
    validate_passcode(current_passcode)
    validate_passcode(new_passcode)

    if current_passcode == new_passcode:
        raise PasscodeInvalid("New passcode must be different")

    now = now_utc()

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT verifier, failed_attempts, locked_until
                FROM security_passcodes
                WHERE user_id = %s
                FOR UPDATE
                """,
                (user_id,),
            )

            row = c.fetchone()

            if not row:
                raise PasscodeInvalid(
                    "Security passcode is not configured"
                )

            verifier, failed_attempts, locked_until = row

            if locked_until is not None:
                if locked_until.tzinfo is None:
                    locked_until = locked_until.replace(tzinfo=now.tzinfo)

                if locked_until > now:
                    raise PasscodeLocked(
                        "Security passcode is temporarily locked"
                    )

            if not bcrypt.checkpw(
                current_passcode.encode("utf-8"),
                verifier.encode("utf-8"),
            ):
                failures = int(failed_attempts or 0) + 1

                if failures >= MAX_FAILED_ATTEMPTS:
                    locked_until = now + timedelta(minutes=LOCK_MINUTES)
                else:
                    locked_until = None

                c.execute(
                    """
                    UPDATE security_passcodes
                    SET failed_attempts = %s,
                        locked_until = %s,
                        updated_at = %s
                    WHERE user_id = %s
                    """,
                    (failures, locked_until, now, user_id),
                )
                conn.commit()

                if failures >= MAX_FAILED_ATTEMPTS:
                    raise PasscodeLocked(
                        "Security passcode is temporarily locked"
                    )

                raise PasscodeInvalid("Invalid security passcode")

            new_verifier = _hash_passcode(new_passcode)

            c.execute(
                """
                UPDATE security_passcodes
                SET verifier = %s,
                    failed_attempts = 0,
                    locked_until = NULL,
                    updated_at = %s
                WHERE user_id = %s
                """,
                (new_verifier, now, user_id),
            )

        conn.commit()


def has_passcode(user_id: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT 1
                FROM security_passcodes
                WHERE user_id = %s
                """,
                (user_id,),
            )
            return c.fetchone() is not None


def verify_passcode(user_id: str, passcode: str) -> None:
    validate_passcode(passcode)

    now = now_utc()

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT verifier, failed_attempts, locked_until
                FROM security_passcodes
                WHERE user_id = %s
                FOR UPDATE
                """,
                (user_id,),
            )

            row = c.fetchone()

            if not row:
                raise PasscodeInvalid(
                    "Security passcode is not configured"
                )

            verifier, failed_attempts, locked_until = row

            if locked_until is not None:
                if locked_until.tzinfo is None:
                    locked_until = locked_until.replace(tzinfo=now.tzinfo)

                if locked_until > now:
                    raise PasscodeLocked(
                        "Security passcode is temporarily locked"
                    )

            valid = bcrypt.checkpw(
                passcode.encode("utf-8"),
                verifier.encode("utf-8"),
            )

            if valid:
                c.execute(
                    """
                    UPDATE security_passcodes
                    SET failed_attempts = 0,
                        locked_until = NULL,
                        last_used_at = %s,
                        updated_at = %s
                    WHERE user_id = %s
                    """,
                    (now, now, user_id),
                )
                conn.commit()
                return

            failures = int(failed_attempts or 0) + 1

            if failures >= MAX_FAILED_ATTEMPTS:
                locked_until = now + timedelta(minutes=LOCK_MINUTES)
            else:
                locked_until = None

            c.execute(
                """
                UPDATE security_passcodes
                SET failed_attempts = %s,
                    locked_until = %s,
                    updated_at = %s
                WHERE user_id = %s
                """,
                (failures, locked_until, now, user_id),
            )
            conn.commit()

            if failures >= MAX_FAILED_ATTEMPTS:
                raise PasscodeLocked(
                    "Security passcode is temporarily locked"
                )

    raise PasscodeInvalid("Invalid security passcode")

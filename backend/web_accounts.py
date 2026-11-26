"""Accounts and sessions for the hosted meeting application."""

import hashlib
import secrets
import time

from backend import analytics


SESSION_TTL = 3600
DUMMY_PASSWORD_HASH = analytics.password_hash(secrets.token_urlsafe())


def initialize():
    with analytics.connect() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS web_accounts (
                account_id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_sessions (
                token_hash TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES web_accounts(account_id),
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS web_sessions_expiry ON web_sessions(expires_at);
        """)


def register(email: str, password: str) -> int | None:
    password_hash = analytics.password_hash(password)
    with analytics.connect() as connection:
        result = connection.execute(
            """
            INSERT INTO web_accounts (email, password_hash)
            SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM admin_accounts WHERE email = ?)
            ON CONFLICT(email) DO NOTHING
            """,
            (email, password_hash, email),
        )
        return result.lastrowid if result.rowcount else None


def authenticate(email: str, password: str) -> int | None:
    with analytics.connect() as connection:
        account = connection.execute(
            "SELECT account_id, password_hash FROM web_accounts WHERE email = ?", (email,)
        ).fetchone()
    password_hash = account["password_hash"] if account else DUMMY_PASSWORD_HASH
    if analytics.verify_password(password, password_hash) and account:
        return account["account_id"]
    return None


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(account_id: int, previous_session: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with analytics.connect() as connection:
        connection.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (now,))
        if previous_session:
            connection.execute("DELETE FROM web_sessions WHERE token_hash = ?", (digest(previous_session),))
        connection.execute(
            "INSERT INTO web_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
            (digest(token), account_id, now + SESSION_TTL),
        )
    return token


def has_session(token: str | None) -> bool:
    if not token:
        return False
    with analytics.connect() as connection:
        session = connection.execute(
            """
            SELECT 1 FROM web_sessions JOIN web_accounts USING (account_id)
            WHERE token_hash = ? AND expires_at > ?
            """,
            (digest(token), int(time.time())),
        ).fetchone()
    return session is not None


def end_session(token: str | None):
    if token:
        with analytics.connect() as connection:
            connection.execute("DELETE FROM web_sessions WHERE token_hash = ?", (digest(token),))

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from backend import analytics


router = APIRouter(prefix="/api/v1")

TOKEN_TTL = 3600
INVITE_TTL = 24 * 3600

EMAIL_PATTERN = re.compile(
    r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
)


def get_secret() -> str:
    secret = os.getenv("ADMIN_TOKEN_SECRET", "")

    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_TOKEN_SECRET must have at least 32 characters",
        )

    return secret


def encode_base64(data: bytes) -> str:
    return (
        base64.urlsafe_b64encode(data)
        .decode("ascii")
        .rstrip("=")
    )


def decode_base64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def normalize_email(email: str) -> str:
    value = email.strip().lower()

    if len(value) > 254 or not EMAIL_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=422,
            detail="Enter a valid email address",
        )

    return value


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 256:
        raise HTTPException(
            status_code=422,
            detail="Password must contain 12 to 256 characters",
        )


def initialize_admin_db() -> None:
    with analytics.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_accounts (
                account_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                is_owner INTEGER NOT NULL DEFAULT 0,
                token_version INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                last_login_at REAL
            );

            CREATE TABLE IF NOT EXISTS admin_invitations (
                invitation_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                revoked_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_admin_invitations_email
            ON admin_invitations(email);
            """
        )

        count = connection.execute(
            "SELECT COUNT(*) AS total FROM admin_accounts"
        ).fetchone()["total"]

        if count == 0:
            email = os.getenv("ADMIN_EMAIL", "").strip().lower()
            password_hash = os.getenv("ADMIN_PASSWORD_HASH", "")

            if not email or not password_hash:
                raise RuntimeError(
                    "Set ADMIN_EMAIL and ADMIN_PASSWORD_HASH in .env"
                )

            if not EMAIL_PATTERN.fullmatch(email):
                raise RuntimeError("Invalid ADMIN_EMAIL in .env")

            connection.execute(
                """
                INSERT INTO admin_accounts (
                    account_id,
                    email,
                    password_hash,
                    active,
                    is_owner,
                    token_version,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    email,
                    password_hash,
                    1,
                    1,
                    0,
                    time.time(),
                ),
            )


def public_account(row) -> dict:
    return {
        "account_id": row["account_id"],
        "email": row["email"],
        "active": bool(row["active"]),
        "is_owner": bool(row["is_owner"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def create_access_token(account) -> str:
    payload_data = {
        "sub": account["account_id"],
        "version": account["token_version"],
        "exp": int(time.time()) + TOKEN_TTL,
    }

    payload = encode_base64(
        json.dumps(
            payload_data,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    signature = encode_base64(
        hmac.new(
            get_secret().encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )

    return f"{payload}.{signature}"


def require_admin(
    authorization: str = Header(default=""),
) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Super Admin login required",
        )

    try:
        token = authorization[7:]
        payload, signature = token.split(".", 1)

        expected_signature = encode_base64(
            hmac.new(
                get_secret().encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        )

        if not hmac.compare_digest(
            signature,
            expected_signature,
        ):
            raise ValueError("Invalid token signature")

        data = json.loads(decode_base64(payload))

        if not isinstance(data, dict):
            raise ValueError("Invalid token payload")

        expiration = data.get("exp")

        if (
            not isinstance(expiration, (int, float))
            or expiration <= time.time()
        ):
            raise ValueError("Token expired")

        with analytics.connect() as connection:
            account = connection.execute(
                """
                SELECT *
                FROM admin_accounts
                WHERE account_id = ?
                """,
                (data.get("sub"),),
            ).fetchone()

        if not account:
            raise ValueError("Account not found")

        if not account["active"]:
            raise ValueError("Account disabled")

        if account["token_version"] != data.get("version"):
            raise ValueError("Session revoked")

        return public_account(account)

    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
        OverflowError,
    ) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired Super Admin session",
        ) from exc


class LoginRequest(BaseModel):
    email: str
    password: str


class InviteRequest(BaseModel):
    email: str


class AcceptRequest(BaseModel):
    token: str = Field(min_length=20)
    password: str


@router.post("/auth/login")
def login(body: LoginRequest):
    email = normalize_email(body.email)

    with analytics.connect() as connection:
        account = connection.execute(
            """
            SELECT *
            FROM admin_accounts
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

        valid = bool(
            account
            and account["active"]
            and analytics.verify_password(
                body.password,
                account["password_hash"],
            )
        )

        if not valid:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password",
            )

        connection.execute(
            """
            UPDATE admin_accounts
            SET last_login_at = ?
            WHERE account_id = ?
            """,
            (
                time.time(),
                account["account_id"],
            ),
        )

    return {
        "access_token": create_access_token(account),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL,
        "account": public_account(account),
    }


@router.get("/admin/me")
def get_current_admin(
    admin=Depends(require_admin),
):
    return admin


@router.get("/admin/accounts")
def list_admin_accounts(
    admin=Depends(require_admin),
):
    with analytics.connect() as connection:
        accounts = connection.execute(
            """
            SELECT *
            FROM admin_accounts
            ORDER BY is_owner DESC, created_at
            """
        ).fetchall()

    return {
        "accounts": [
            public_account(account)
            for account in accounts
        ]
    }


@router.post("/admin/invitations")
def create_invitation(
    body: InviteRequest,
    admin=Depends(require_admin),
):
    email = normalize_email(body.email)

    invite_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(
        invite_token.encode("utf-8")
    ).hexdigest()

    invitation_id = uuid.uuid4().hex
    now = time.time()

    with analytics.connect() as connection:
        existing = connection.execute(
            """
            SELECT account_id
            FROM admin_accounts
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="An admin account with this email already exists",
            )

        connection.execute(
            """
            INSERT INTO admin_invitations (
                invitation_id,
                email,
                token_hash,
                created_by,
                created_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                invitation_id,
                email,
                token_hash,
                admin["account_id"],
                now,
                now + INVITE_TTL,
            ),
        )

    return {
        "invitation_id": invitation_id,
        "email": email,
        "invite_token": invite_token,
        "expires_at": now + INVITE_TTL,
    }


@router.get("/admin/invitations")
def list_invitations(
    admin=Depends(require_admin),
):
    with analytics.connect() as connection:
        invitations = connection.execute(
            """
            SELECT
                invitation_id,
                email,
                created_at,
                expires_at,
                consumed_at,
                revoked_at
            FROM admin_invitations
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()

    return {
        "invitations": [
            dict(invitation)
            for invitation in invitations
        ]
    }


@router.post("/admin/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: str,
    admin=Depends(require_admin),
):
    with analytics.connect() as connection:
        result = connection.execute(
            """
            UPDATE admin_invitations
            SET revoked_at = ?
            WHERE invitation_id = ?
              AND consumed_at IS NULL
              AND revoked_at IS NULL
            """,
            (
                time.time(),
                invitation_id,
            ),
        )

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Pending invitation not found",
            )

    return {"ok": True}


@router.get("/auth/invitations/validate")
def validate_invitation(
    token: str,
):
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    with analytics.connect() as connection:
        invitation = connection.execute(
            """
            SELECT *
            FROM admin_invitations
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    if (
        not invitation
        or invitation["expires_at"] <= time.time()
        or invitation["consumed_at"] is not None
        or invitation["revoked_at"] is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="Invitation is invalid or expired",
        )

    return {
        "email": invitation["email"],
        "expires_at": invitation["expires_at"],
    }


@router.post("/auth/invitations/accept")
def accept_invitation(
    body: AcceptRequest,
):
    validate_password(body.password)

    token_hash = hashlib.sha256(
        body.token.encode("utf-8")
    ).hexdigest()

    now = time.time()

    with analytics.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        invitation = connection.execute(
            """
            SELECT *
            FROM admin_invitations
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        if (
            not invitation
            or invitation["expires_at"] <= now
            or invitation["consumed_at"] is not None
            or invitation["revoked_at"] is not None
        ):
            raise HTTPException(
                status_code=400,
                detail="Invitation is invalid or expired",
            )

        existing = connection.execute(
            """
            SELECT 1
            FROM admin_accounts
            WHERE email = ?
            """,
            (invitation["email"],),
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Admin account already exists",
            )

        account_id = uuid.uuid4().hex

        connection.execute(
            """
            INSERT INTO admin_accounts (
                account_id,
                email,
                password_hash,
                active,
                is_owner,
                token_version,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                invitation["email"],
                analytics.password_hash(body.password),
                1,
                0,
                0,
                now,
            ),
        )

        connection.execute(
            """
            UPDATE admin_invitations
            SET consumed_at = ?
            WHERE invitation_id = ?
            """,
            (
                now,
                invitation["invitation_id"],
            ),
        )

    return {
        "ok": True,
        "email": invitation["email"],
    }


@router.post("/admin/accounts/{account_id}/disable")
def disable_account(
    account_id: str,
    admin=Depends(require_admin),
):
    if account_id == admin["account_id"]:
        raise HTTPException(
            status_code=400,
            detail="You cannot disable your own account",
        )

    with analytics.connect() as connection:
        account = connection.execute(
            """
            SELECT *
            FROM admin_accounts
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()

        if not account:
            raise HTTPException(
                status_code=404,
                detail="Admin account not found",
            )

        if account["is_owner"]:
            raise HTTPException(
                status_code=403,
                detail="Primary owner cannot be disabled",
            )

        connection.execute(
            """
            UPDATE admin_accounts
            SET
                active = 0,
                token_version = token_version + 1
            WHERE account_id = ?
            """,
            (account_id,),
        )

    return {"ok": True}


@router.post("/admin/accounts/{account_id}/enable")
def enable_account(
    account_id: str,
    admin=Depends(require_admin),
):
    with analytics.connect() as connection:
        result = connection.execute(
            """
            UPDATE admin_accounts
            SET
                active = 1,
                token_version = token_version + 1
            WHERE account_id = ?
            """,
            (account_id,),
        )

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Admin account not found",
            )

    return {"ok": True}


@router.post("/admin/accounts/{account_id}/revoke-sessions")
def revoke_sessions(
    account_id: str,
    admin=Depends(require_admin),
):
    if account_id == admin["account_id"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot revoke your own sessions here",
        )

    with analytics.connect() as connection:
        result = connection.execute(
            """
            UPDATE admin_accounts
            SET token_version = token_version + 1
            WHERE account_id = ?
              AND is_owner = 0
            """,
            (account_id,),
        )

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Eligible admin account not found",
            )

    return {"ok": True}

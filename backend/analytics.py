import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time

from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from livekit import api


load_dotenv(".env")

logger = logging.getLogger("mateo-analytics")

DB_PATH = Path(
    os.getenv(
        "MATEO_DB_PATH",
        "backend/mateo_analytics.sqlite3"
    )
)

router = APIRouter(prefix="/api/v1")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        timeout=10
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")

    try:
        yield connection
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def initialize_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with connect() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS meetings (
                meeting_id TEXT PRIMARY KEY,
                room_name TEXT NOT NULL UNIQUE,
                invite_code TEXT NOT NULL,
                owner_token TEXT NOT NULL,
                created_at REAL NOT NULL,
                state TEXT NOT NULL,
                dispatch_id TEXT
            );

            CREATE TABLE IF NOT EXISTS participants (
                participant_id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                joined_at REAL NOT NULL,
                FOREIGN KEY (meeting_id)
                    REFERENCES meetings(meeting_id)
            );

            CREATE TABLE IF NOT EXISTS queries (
                event_id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_queries_meeting
                ON queries(meeting_id);

            CREATE INDEX IF NOT EXISTS idx_queries_participant
                ON queries(participant_id);

            CREATE TABLE IF NOT EXISTS model_usage (
                session_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                kind TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                stt_seconds REAL NOT NULL DEFAULT 0,
                tts_characters INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (
                    session_id,
                    provider,
                    model,
                    kind
                )
            );

            CREATE TABLE IF NOT EXISTS session_room (
                session_id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL
            );
        """)

    logger.info("Analytics database initialized")


def save_meeting(meeting: dict):
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO meetings (
                meeting_id,
                room_name,
                invite_code,
                owner_token,
                created_at,
                state,
                dispatch_id
            )
            VALUES (
                :meeting_id,
                :room_name,
                :invite_code,
                :owner_token,
                :created_at,
                :state,
                :dispatch_id
            )
            """,
            meeting
        )


def get_meeting(meeting_id: str):
    with connect() as connection:
        result = connection.execute(
            """
            SELECT *
            FROM meetings
            WHERE meeting_id = ?
            """,
            (meeting_id,)
        ).fetchone()

    return dict(result) if result else None


def update_dispatch(meeting_id: str, dispatch_id: str):
    with connect() as connection:
        connection.execute(
            """
            UPDATE meetings
            SET dispatch_id = ?,
                state = ?
            WHERE meeting_id = ?
            """,
            (
                dispatch_id,
                "agent_requested",
                meeting_id
            )
        )


def add_participant(
    meeting_id: str,
    participant_id: str,
    display_name: str
):
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO participants (
                participant_id,
                meeting_id,
                display_name,
                joined_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                participant_id,
                meeting_id,
                display_name,
                time.time()
            )
        )


def password_hash(password: str):
    salt = secrets.token_bytes(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        600_000
    )

    return base64.urlsafe_b64encode(
        salt + digest
    ).decode("utf-8")


def verify_password(password: str, encoded: str):
    try:
        raw = base64.urlsafe_b64decode(encoded)

        if len(raw) != 48:
            return False

        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            raw[:16],
            600_000
        )

        return hmac.compare_digest(
            digest,
            raw[16:]
        )

    except (
        ValueError,
        TypeError,
        binascii.Error
    ):
        return False


def b64(data: bytes):
    return base64.urlsafe_b64encode(
        data
    ).decode("utf-8").rstrip("=")


def unb64(value: str):
    return base64.urlsafe_b64decode(
        value + "=" * (-len(value) % 4)
    )


def secret_env(name: str):
    value = os.getenv(name, "")

    if len(value) < 32:
        raise HTTPException(
            status_code=503,
            detail=f"{name} is not configured correctly"
        )

    return value


def create_admin_token():
    secret = secret_env("ADMIN_TOKEN_SECRET")

    payload = b64(
        json.dumps(
            {
                "sub": "super_admin",
                "exp": int(time.time()) + 3600
            },
            separators=(",", ":")
        ).encode("utf-8")
    )

    signature = b64(
        hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).digest()
    )

    return f"{payload}.{signature}"


def admin_required(
    authorization: str = Header(default="")
):
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Admin login required"
        )

    try:
        payload, signature = authorization[7:].split(".", 1)

        expected = b64(
            hmac.new(
                secret_env("ADMIN_TOKEN_SECRET").encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256
            ).digest()
        )

        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid signature")

        data = json.loads(unb64(payload))

        if not isinstance(data, dict):
            raise ValueError("Invalid payload")

        if (
            data.get("sub") != "super_admin"
            or not isinstance(data.get("exp"), (int, float))
            or data["exp"] <= time.time()
        ):
            raise ValueError("Expired token")

        return "super_admin"

    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error
    ) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired admin token"
        ) from exc


def agent_required(
    x_agent_event_key: str = Header(default="")
):
    expected = secret_env("AGENT_EVENT_KEY")

    if not hmac.compare_digest(
        expected,
        x_agent_event_key
    ):
        raise HTTPException(
            status_code=403,
            detail="Agent event forbidden"
        )


class Login(BaseModel):
    password: str


class QueryEvent(BaseModel):
    room_name: str
    participant_id: str
    event_id: str = Field(min_length=4, max_length=150)


class UsageEntry(BaseModel):
    provider: str = Field(max_length=100)
    model: str = Field(max_length=150)
    kind: str = Field(max_length=20)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    stt_seconds: float = Field(default=0, ge=0)
    tts_characters: int = Field(default=0, ge=0)


class UsageSnapshot(BaseModel):
    room_name: str
    session_id: str
    models: list[UsageEntry]


@router.post("/auth/login")
def login(body: Login):
    stored_hash = os.getenv(
        "ADMIN_PASSWORD_HASH",
        ""
    )

    if not stored_hash or not verify_password(
        body.password,
        stored_hash
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )

    return {
        "access_token": create_admin_token(),
        "token_type": "bearer",
        "expires_in": 3600
    }


def lookup_room(connection, room_name: str):
    return connection.execute(
        """
        SELECT meeting_id
        FROM meetings
        WHERE room_name = ?
        """,
        (room_name,)
    ).fetchone()


@router.post(
    "/agent/events/query",
    dependencies=[Depends(agent_required)]
)
def record_query(body: QueryEvent):
    with connect() as connection:
        meeting = lookup_room(
            connection,
            body.room_name
        )

        if meeting is None:
            raise HTTPException(
                status_code=404,
                detail="Unknown meeting room"
            )

        meeting_id = meeting["meeting_id"]

        participant = connection.execute(
            """
            SELECT participant_id
            FROM participants
            WHERE participant_id = ?
              AND meeting_id = ?
            """,
            (
                body.participant_id,
                meeting_id
            )
        ).fetchone()

        if participant is None:
            raise HTTPException(
                status_code=404,
                detail="Unknown participant"
            )

        connection.execute(
            """
            INSERT OR IGNORE INTO queries (
                event_id,
                meeting_id,
                participant_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                body.event_id,
                meeting_id,
                body.participant_id,
                time.time()
            )
        )

    return {"ok": True}


@router.post(
    "/agent/events/usage",
    dependencies=[Depends(agent_required)]
)
def record_usage(body: UsageSnapshot):
    with connect() as connection:
        meeting = lookup_room(
            connection,
            body.room_name
        )

        if meeting is None:
            raise HTTPException(
                status_code=404,
                detail="Unknown meeting room"
            )

        meeting_id = meeting["meeting_id"]

        existing = connection.execute(
            """
            SELECT meeting_id
            FROM session_room
            WHERE session_id = ?
            """,
            (body.session_id,)
        ).fetchone()

        if (
            existing is not None
            and existing["meeting_id"] != meeting_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Session belongs to another meeting"
            )

        connection.execute(
            """
            INSERT OR IGNORE INTO session_room (
                session_id,
                meeting_id
            )
            VALUES (?, ?)
            """,
            (
                body.session_id,
                meeting_id
            )
        )

        for usage in body.models:
            connection.execute(
                """
                INSERT INTO model_usage (
                    session_id,
                    provider,
                    model,
                    kind,
                    input_tokens,
                    output_tokens,
                    stt_seconds,
                    tts_characters
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT (
                    session_id,
                    provider,
                    model,
                    kind
                )

                DO UPDATE SET
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    stt_seconds = excluded.stt_seconds,
                    tts_characters = excluded.tts_characters
                """,
                (
                    body.session_id,
                    usage.provider,
                    usage.model,
                    usage.kind,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.stt_seconds,
                    usage.tts_characters
                )
            )

    return {"ok": True}


async def live_count(room_name: str):
    try:
        async with api.LiveKitAPI() as livekit:
            response = await livekit.room.list_participants(
                api.ListParticipantsRequest(
                    room=room_name
                )
            )

        humans = [
            {
                "id": participant.identity,
                "name": participant.name
            }
            for participant in response.participants
            if participant.identity.startswith("human_")
        ]

        return humans

    except Exception as exc:
        error_code = getattr(exc, "code", None)
        error_status = getattr(exc, "status", None)

        code_value = getattr(
            error_code,
            "value",
            error_code
        )

        is_missing_room = (
            str(code_value).lower() == "not_found"
            or (
                error_status == 404
                and "requested room does not exist"
                in str(exc).lower()
            )
        )

        if is_missing_room:
            logger.info(
                "Historical LiveKit room no longer exists: %s",
                room_name
            )

            return []

        logger.exception(
            "LiveKit participant lookup failed for %s",
            room_name
        )

        return None


def estimate_cost(usage_rows):
    rate_names = {
        "input": "COST_LLM_INPUT_PER_1M",
        "output": "COST_LLM_OUTPUT_PER_1M",
        "stt": "COST_STT_PER_MIN",
        "tts": "COST_TTS_PER_1M_CHARS"
    }

    rates_configured = all(
        os.getenv(name, "").strip() != ""
        for name in rate_names.values()
    )

    try:
        input_rate = float(
            os.getenv(rate_names["input"], "0") or 0
        )

        output_rate = float(
            os.getenv(rate_names["output"], "0") or 0
        )

        stt_rate = float(
            os.getenv(rate_names["stt"], "0") or 0
        )

        tts_rate = float(
            os.getenv(rate_names["tts"], "0") or 0
        )

        rates = (
            input_rate,
            output_rate,
            stt_rate,
            tts_rate
        )

        if any(
            rate < 0 or not __import__("math").isfinite(rate)
            for rate in rates
        ):
            raise ValueError("Invalid cost rate")

    except (ValueError, OverflowError):
        logger.warning(
            "Invalid cost configuration in .env"
        )

        input_rate = 0
        output_rate = 0
        stt_rate = 0
        tts_rate = 0
        rates_configured = False

    summary = {
        "llm": 0.0,
        "stt": 0.0,
        "tts": 0.0
    }

    for usage in usage_rows:
        kind = str(usage["kind"]).lower()

        if kind == "llm":
            summary["llm"] += (
                usage["input_tokens"] * input_rate / 1_000_000
                + usage["output_tokens"] * output_rate / 1_000_000
            )

        elif kind == "stt":
            summary["stt"] += (
                usage["stt_seconds"] / 60 * stt_rate
            )

        elif kind == "tts":
            summary["tts"] += (
                usage["tts_characters"] * tts_rate / 1_000_000
            )

    return {
        "llm": round(summary["llm"], 6),
        "stt": round(summary["stt"], 6),
        "tts": round(summary["tts"], 6),
        "total_estimated_usd": round(
            sum(summary.values()),
            6
        ),
        "rates_configured": rates_configured
    }


@router.get(
    "/admin/overview",
    dependencies=[Depends(admin_required)]
)
async def overview():
    with connect() as connection:
        meetings = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    meeting_id,
                    room_name,
                    created_at,
                    state
                FROM meetings
                ORDER BY created_at DESC
                """
            ).fetchall()
        ]

        query_total = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM queries
            """
        ).fetchone()["total"]

        participants_total = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM participants
            """
        ).fetchone()["total"]

        usage_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM model_usage
                """
            ).fetchall()
        ]

    live_results = await asyncio.gather(
        *[
            live_count(meeting["room_name"])
            for meeting in meetings
        ]
    )

    for meeting, humans in zip(
        meetings,
        live_results
    ):
        meeting["live_participants"] = humans

        meeting["live_count"] = (
            len(humans)
            if humans is not None
            else None
        )

    total_live = sum(
        meeting["live_count"] or 0
        for meeting in meetings
    )

    all_counts_available = all(
        meeting["live_count"] is not None
        for meeting in meetings
    )

    return {
        "meeting_count": len(meetings),
        "total_joins": participants_total,
        "query_count": query_total,
        "live_humans": total_live,
        "live_count_complete": all_counts_available,
        "estimated_cost": estimate_cost(usage_rows),
        "meetings": meetings
    }


@router.get(
    "/admin/meetings/{meeting_id}",
    dependencies=[Depends(admin_required)]
)
async def meeting_detail(meeting_id: str):
    with connect() as connection:
        meeting = connection.execute(
            """
            SELECT
                meeting_id,
                room_name,
                created_at,
                state
            FROM meetings
            WHERE meeting_id = ?
            """,
            (meeting_id,)
        ).fetchone()

        if meeting is None:
            raise HTTPException(
                status_code=404,
                detail="Meeting not found"
            )

        meeting_data = dict(meeting)

        participants = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    p.participant_id,
                    p.display_name,
                    p.joined_at,
                    COUNT(q.event_id) AS queries
                FROM participants p
                LEFT JOIN queries q
                    ON p.participant_id = q.participant_id
                WHERE p.meeting_id = ?
                GROUP BY
                    p.participant_id,
                    p.display_name,
                    p.joined_at
                ORDER BY p.joined_at
                """,
                (meeting_id,)
            ).fetchall()
        ]

        usage_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT u.*
                FROM model_usage u
                JOIN session_room s
                    ON s.session_id = u.session_id
                WHERE s.meeting_id = ?
                """,
                (meeting_id,)
            ).fetchall()
        ]

    online = await live_count(
        meeting_data["room_name"]
    )

    current_ids = {
        person["id"]
        for person in (online or [])
    }

    for participant in participants:
        participant["online"] = (
            participant["participant_id"] in current_ids
            if online is not None
            else None
        )

    return {
        "meeting": meeting_data,
        "participants": participants,
        "live_count": (
            len(online)
            if online is not None
            else None
        ),
        "model_usage": usage_rows,
        "estimated_cost": estimate_cost(usage_rows)
    }
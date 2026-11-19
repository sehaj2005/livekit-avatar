import asyncio
import logging
import os
import secrets
import time
import uuid

from backend import medicine_upload_routes
from backend import participant_documents

from backend import local_skin_rag_routes

from backend import medicine_preview_routes

from backend import medicine_dev_routes

#from backend import medicine_rag

from backend import skin_routes

from datetime import timedelta

from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    HTTPException,
    Header,
    Depends,
)

from fastapi.middleware.cors import CORSMiddleware

from pydantic import (
    BaseModel,
    Field,
)

from livekit import api

from backend import (
    analytics,
    admin_accounts,
)


load_dotenv(".env")

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("mateo-backend")

analytics.initialize_db()
admin_accounts.initialize_admin_db()

app = FastAPI(
    title="Mateo Group Meeting API",
    version="4.0.0",
)

analytics.router.routes[:] = [
    route
    for route in analytics.router.routes
    if route.path != "/api/v1/auth/login"
]

app.dependency_overrides[
    analytics.admin_required
] = admin_accounts.require_admin

app.include_router(admin_accounts.router)
app.include_router(analytics.router)
app.include_router(skin_routes.router)
#app.include_router(medicine_rag.router)
app.include_router(medicine_dev_routes.router)
app.include_router(medicine_preview_routes.router)
app.include_router(local_skin_rag_routes.router)
app.include_router(medicine_upload_routes.router)
app.include_router(participant_documents.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "FRONTEND_ORIGIN",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


AGENT_NAME = "avatar-agent"

MEETING_TTL_SECONDS = 6 * 60 * 60

dispatch_lock = asyncio.Lock()

reset_lock = asyncio.Lock()

reset_in_progress = False


class JoinRequest(BaseModel):
    display_name: str = Field(
        min_length=1,
        max_length=60,
    )

    role: str = "human"

    invite_code: str = Field(
        min_length=1,
    )


class ResetAnalyticsRequest(BaseModel):
    confirmation: str


def ensure_reset_not_running():
    if reset_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Statistics reset is in progress",
        )


def find_meeting(meeting_id: str) -> dict:
    ensure_reset_not_running()

    meeting = analytics.get_meeting(meeting_id)

    if not meeting:
        raise HTTPException(
            status_code=404,
            detail="Meeting not found",
        )

    meeting_age = (
        time.time() - meeting["created_at"]
    )

    if meeting_age > MEETING_TTL_SECONDS:
        raise HTTPException(
            status_code=404,
            detail="Meeting expired",
        )

    return meeting


def require_invite(
    meeting: dict,
    invite_code: str,
) -> None:
    valid = secrets.compare_digest(
        meeting["invite_code"],
        invite_code,
    )

    if not valid:
        raise HTTPException(
            status_code=403,
            detail="Invalid meeting invitation",
        )


def issue_join_token(
    room_name: str,
    display_name: str,
) -> tuple[str, str]:
    required_keys = (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )

    configured = all(
        os.getenv(key)
        for key in required_keys
    )

    if not configured:
        raise HTTPException(
            status_code=503,
            detail="LiveKit configuration missing",
        )

    participant_id = (
        "human_" + uuid.uuid4().hex[:16]
    )

    token = (
        api.AccessToken(
            os.environ["LIVEKIT_API_KEY"],
            os.environ["LIVEKIT_API_SECRET"],
        )
        .with_identity(participant_id)
        .with_name(display_name)
        .with_ttl(timedelta(hours=2))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .to_jwt()
    )

    return participant_id, token


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Mateo Group Meeting API",
        "version": "4.0.0",
    }


@app.get("/ready")
def ready():
    required_keys = (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )

    configured = all(
        os.getenv(key)
        for key in required_keys
    )

    return {
        "ready": configured,
    }


@app.post("/api/v1/meetings")
def create_meeting():
    ensure_reset_not_running()

    meeting_id = (
        "mtg_" + uuid.uuid4().hex[:12]
    )

    room_name = (
        "room_" + uuid.uuid4().hex[:16]
    )

    invite_code = secrets.token_urlsafe(24)
    owner_token = secrets.token_urlsafe(32)

    meeting = {
        "meeting_id": meeting_id,
        "room_name": room_name,
        "invite_code": invite_code,
        "owner_token": owner_token,
        "created_at": time.time(),
        "dispatch_id": None,
        "state": "created",
    }

    analytics.save_meeting(meeting)

    logger.info(
        "Created meeting: %s",
        meeting_id,
    )

    return {
        "meeting_id": meeting_id,
        "room_name": room_name,
        "invite_code": invite_code,
        "owner_token": owner_token,
        "state": meeting["state"],
    }


@app.post("/api/v1/meetings/{meeting_id}/join")
def join_meeting(
    meeting_id: str,
    body: JoinRequest,
):
    meeting = find_meeting(meeting_id)

    require_invite(
        meeting,
        body.invite_code,
    )

    if body.role != "human":
        raise HTTPException(
            status_code=403,
            detail="Only human participants can join",
        )

    display_name = body.display_name.strip()

    if not display_name:
        raise HTTPException(
            status_code=422,
            detail="Display name cannot be blank",
        )

    participant_id, token = issue_join_token(
        room_name=meeting["room_name"],
        display_name=display_name,
    )

    analytics.add_participant(
        meeting_id,
        participant_id,
        display_name,
    )

    logger.info(
        "Participant %s joining meeting %s",
        participant_id,
        meeting_id,
    )

    return {
        "meeting_id": meeting_id,
        "participant_id": participant_id,
        "room_name": meeting["room_name"],
        "livekit_url": os.environ["LIVEKIT_URL"],
        "access_token": token,
    }


@app.post(
    "/api/v1/meetings/{meeting_id}/agent/start"
)
async def start_agent(
    meeting_id: str,
    x_owner_token: str | None = Header(
        default=None
    ),
):
    meeting = find_meeting(meeting_id)

    if (
        not x_owner_token
        or not secrets.compare_digest(
            x_owner_token,
            meeting["owner_token"],
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the meeting creator may start Mateo",
        )

    async with dispatch_lock:
        ensure_reset_not_running()

        try:
            async with api.LiveKitAPI() as livekit:
                room_name = meeting["room_name"]
                try:
                    existing = await livekit.agent_dispatch.list_dispatch(
                        room_name
                    )
                except api.TwirpError as exc:
                    if exc.code != api.TwirpErrorCode.NOT_FOUND:
                        raise
                    # Creating the dispatch also creates the room if needed.
                    existing = []

                for item in existing:
                    if item.agent_name == AGENT_NAME:
                        analytics.update_dispatch(
                            meeting_id,
                            item.id,
                        )

                        return {
                            "status": "already_dispatched",
                            "dispatch_id": item.id,
                        }

                dispatch = (
                    await livekit.agent_dispatch.create_dispatch(
                        api.CreateAgentDispatchRequest(
                            room=room_name,
                            agent_name=AGENT_NAME,
                        )
                    )
                )

        except Exception as exc:
            logger.exception(
                "Mateo agent dispatch failed"
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not dispatch Mateo. "
                    "Check backend logs."
                ),
            ) from exc

        analytics.update_dispatch(
            meeting_id,
            dispatch.id,
        )

        logger.info(
            "Mateo dispatched to meeting %s",
            meeting_id,
        )

        return {
            "status": "requested",
            "dispatch_id": dispatch.id,
        }


@app.post("/api/v1/admin/reset-analytics")
async def reset_analytics(
    body: ResetAnalyticsRequest,
    admin=Depends(admin_accounts.require_admin),
):
    global reset_in_progress

    if not admin["is_owner"]:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only the Primary Super Admin "
                "can reset statistics"
            ),
        )

    if body.confirmation != "RESET":
        raise HTTPException(
            status_code=400,
            detail="Type RESET to confirm",
        )

    async with reset_lock:
        if reset_in_progress:
            raise HTTPException(
                status_code=409,
                detail="Reset is already in progress",
            )

        reset_in_progress = True

        try:
            async with dispatch_lock:
                with analytics.connect() as connection:
                    room_names = [
                        row["room_name"]
                        for row in connection.execute(
                            "SELECT room_name FROM meetings"
                        ).fetchall()
                    ]

                if room_names:
                    try:
                        async with api.LiveKitAPI() as livekit:
                            response = (
                                await livekit.room.list_rooms(
                                    api.ListRoomsRequest(
                                        names=room_names
                                    )
                                )
                            )

                    except Exception as exc:
                        logger.exception(
                            "Failed to verify LiveKit rooms"
                        )

                        raise HTTPException(
                            status_code=503,
                            detail=(
                                "Could not verify active "
                                "LiveKit rooms. Reset cancelled."
                            ),
                        ) from exc

                    if response.rooms:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "Active Mateo rooms still exist. "
                                "End the meetings and disconnect "
                                "Mateo before resetting statistics."
                            ),
                        )

                with analytics.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")

                    for table in (
                        "queries",
                        "model_usage",
                        "session_room",
                        "participants",
                        "meetings",
                    ):
                        connection.execute(
                            f"DELETE FROM {table}"
                        )

                logger.warning(
                    "Analytics reset by Primary Super Admin: %s",
                    admin["email"],
                )

                return {
                    "success": True,
                    "message": "Statistics reset successfully",
                    "preserved": [
                        "admin_accounts",
                        "admin_invitations",
                    ],
                }

        finally:
            reset_in_progress = False

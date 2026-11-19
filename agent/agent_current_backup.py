import asyncio
import json
import logging
import os
import uuid

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from skin_voice_bridge import register_skin_voice_bridge

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    inference,
    room_io,
)
from livekit.plugins import anam



load_dotenv(".env")

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("mateo-agent")

AGENT_NAME = "avatar-agent"
FLOOR_TOPIC = "mateo-floor"

BACKEND_URL = os.getenv(
    "MATEO_BACKEND_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

AGENT_EVENT_KEY = os.getenv(
    "AGENT_EVENT_KEY",
    "",
)


def send_analytics_sync(
    endpoint: str,
    payload: dict,
) -> None:
    """
    Send analytics to FastAPI.

    This function runs in a separate thread
    so HTTP requests do not block AI audio.
    """

    if not AGENT_EVENT_KEY:
        raise RuntimeError(
            "AGENT_EVENT_KEY is missing"
        )

    url = (
        BACKEND_URL
        + "/api/v1/agent/events/"
        + endpoint
    )

    request = Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Agent-Event-Key": AGENT_EVENT_KEY,
        },
        method="POST",
    )

    with urlopen(
        request,
        timeout=5,
    ) as response:
        response.read()


def serialize_usage(usage) -> dict | None:
    """
    Convert SDK model usage to the format
    expected by backend/analytics.py.
    """

    usage_type = getattr(
        usage,
        "type",
        "",
    )

    if usage_type == "llm_usage":
        kind = "llm"

    elif usage_type == "stt_usage":
        kind = "stt"

    elif usage_type == "tts_usage":
        kind = "tts"

    else:
        return None

    return {
        "provider": str(
            getattr(usage, "provider", "unknown")
            or "unknown"
        ),
        "model": str(
            getattr(usage, "model", "unknown")
            or "unknown"
        ),
        "kind": kind,
        "input_tokens": int(
            getattr(usage, "input_tokens", 0)
            or 0
        ),
        "output_tokens": int(
            getattr(usage, "output_tokens", 0)
            or 0
        ),
        "stt_seconds": (
            float(
                getattr(usage, "audio_duration", 0)
                or 0
            )
            if kind == "stt"
            else 0.0
        ),
        "tts_characters": (
            int(
                getattr(usage, "characters_count", 0)
                or 0
            )
            if kind == "tts"
            else 0
        ),
    }


class MateoAssistant(Agent):

    def __init__(self):

        super().__init__(
            instructions=(
                "Your name is Mateo. "

                "You are a friendly, intelligent "
                "AI video meeting assistant. "

                "You are represented by a "
                "realistic male avatar. "

                "You participate in group meetings "
                "with multiple human participants. "

                "Everyone can see and hear you. "

                "Only one selected participant is "
                "connected to your listening input "
                "at a time. "

                "Participants select Talk to Mateo "
                "to take turns speaking with you. "

                "Respond naturally and clearly. "

                "Keep your replies concise, helpful, "
                "and conversational. "

                "Maintain the shared conversation "
                "context across speaker changes. "

                "You are an AI assistant, "
                "not a human being. "

                "Do not invent information. "

                "Do not claim you are listening to "
                "everyone simultaneously."
            )
        )



# No extra idle Python worker on an 8-GB development laptop.
# The longer startup timeout is retained for Windows process creation.
server = AgentServer(
    initialize_process_timeout=60.0,
    num_idle_processes=0,
)



@server.rtc_session(
    agent_name=AGENT_NAME
)
async def avatar_agent(
    ctx: agents.JobContext,
):

    logger.info(
        "Starting Mateo for room: %s",
        ctx.room.name,
    )


    # Deepgram Flux already provides end-of-turn detection. Avoid the extra
    # inference VAD/turn-detector workload that was falling tens of seconds
    # behind and starving the LiveKit job's WebSocket heartbeat.
    # Trade-off: without VAD, interruptions are detected via STT transcripts,
    # so barge-in may react a little later than before.
    session = AgentSession(
        
        stt=inference.STT(
            model="deepgram/flux-general",
            language="en",
        ),

        llm=inference.LLM(
            model="google/gemma-4-31b-it",
        ),

        tts=inference.TTS(
            model="fishaudio/s2.1-pro",
            voice=(
                "fa4c9eb3dccc4806b382b40d61c6b10a"
            ),
        ),
    )


    session_id = uuid.uuid4().hex

    active_speaker_id = None

    session_ready = False

    speaker_lock = asyncio.Lock()

    pending_floor_requests = set()

    analytics_queue = asyncio.Queue()

    analytics_stopped = False

    last_usage_snapshot = None


    async def analytics_worker():
        """
        Process analytics events sequentially.

        HTTP failures are logged.
        Mateo continues speaking normally.
        """

        while True:

            item = await analytics_queue.get()

            try:

                if item is None:
                    return

                endpoint, payload = item

                await asyncio.to_thread(
                    send_analytics_sync,
                    endpoint,
                    payload,
                )

            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                RuntimeError,
            ):

                logger.exception(
                    "Failed to send analytics event"
                )

            except Exception:

                logger.exception(
                    "Unexpected analytics error"
                )

            finally:

                analytics_queue.task_done()

    analytics_task = asyncio.create_task(
        analytics_worker()
    )


    def queue_analytics(
        endpoint: str,
        payload: dict,
    ):

        if analytics_stopped:
            return

        analytics_queue.put_nowait(
            (endpoint, payload)
        )

    @session.on(
        "conversation_item_added"
    )
    def on_conversation_item_added(event):

        item = event.item

    
        if getattr(
            item,
            "role",
            None,
        ) != "user":
            return

        
        text = getattr(
            item,
            "text_content",
            "",
        )

        if not isinstance(text, str):
            text = ""

        if not text.strip():
            return

        linked = None

        if session.room_io is not None:
            linked = (
                session.room_io.linked_participant
            )

        if linked is None:
            return

        participant_id = linked.identity

        if not participant_id.startswith(
            "human_"
        ):
            return

        
        event_id = str(
            getattr(item, "id", None)
            or uuid.uuid4().hex
        )

        queue_analytics(
            "query",
            {
                "room_name": ctx.room.name,
                "participant_id": participant_id,
                "event_id": event_id,
            },
        )

        logger.info(
            "Recorded query for participant %s",
            participant_id,
        )


    # Diagnostic only: verify speech reaches STT after the user speaks.
    # Do not log full transcripts, which may contain sensitive meeting content.
    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event):
        logger.info(
            "Mateo STT event | final=%s | chars=%s",
            bool(getattr(event, "is_final", False)),
            len(getattr(event, "transcript", "") or ""),
        )

    @session.on(
        "session_usage_updated"
    )
    def on_session_usage_updated(event):
        """
        Track cumulative model usage.

        Avoid sending identical usage snapshots
        repeatedly to the analytics backend.
        """

        nonlocal last_usage_snapshot

        models = []

        for usage in event.usage.model_usage:

            item = serialize_usage(usage)

            if item is not None:
                models.append(item)

        if not models:
            return

        # Sort for stable snapshot comparison.
        normalized_models = sorted(
            models,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
            ),
        )

        snapshot = json.dumps(
            normalized_models,
            sort_keys=True,
        )

        # Skip unchanged usage.
        if snapshot == last_usage_snapshot:
            return

        last_usage_snapshot = snapshot

        queue_analytics(
            "usage",
            {
                "room_name": ctx.room.name,
                "session_id": session_id,
                "models": models,
            },
        )


    async def broadcast_floor(
        participant_id: str,
        display_name: str,
    ):

        message = {
            "type": "floor_changed",
            "participant_id": participant_id,
            "display_name": display_name,
        }

        payload = json.dumps(
            message
        ).encode("utf-8")

        await ctx.room.local_participant.publish_data(
            payload,
            reliable=True,
            topic=FLOOR_TOPIC,
        )

        logger.info(
            "Mateo listening to: %s",
            display_name,
        )


    async def switch_speaker(
        participant_id: str,
    ):

        nonlocal active_speaker_id

        async with speaker_lock:

            if not session_ready:

                logger.warning(
                    "Session not ready for switching"
                )

                return

            participant = (
                ctx.room.remote_participants.get(
                    participant_id
                )
            )

            if participant is None:

                logger.warning(
                    "Participant not found: %s",
                    participant_id,
                )

                return

            if not participant.identity.startswith(
                "human_"
            ):
                return

            if session.room_io is None:

                logger.error(
                    "RoomIO is not initialized"
                )

                return

            if active_speaker_id != participant_id:

                try:

                   
                    session.interrupt()

                    session.clear_user_turn()

                    
                    session.room_io.set_participant(
                        participant_id
                    )

                    active_speaker_id = participant_id

                except Exception:

                    logger.exception(
                        "Speaker switching failed"
                    )

                    return

            await broadcast_floor(
                participant_id,
                participant.name or "Participant",
            )


    def on_data_received(packet):

        if packet.topic != FLOOR_TOPIC:
            return

        sender = packet.participant

        if sender is None:
            return

        if not sender.identity.startswith(
            "human_"
        ):
            return

        try:

            message = json.loads(
                packet.data.decode("utf-8")
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return

        if not isinstance(message, dict):
            return

        if message.get("type") != "request_floor":
            return

        participant_id = sender.identity

        if participant_id in pending_floor_requests:
            return

        pending_floor_requests.add(
            participant_id
        )

        async def process_request():

            try:

                await switch_speaker(
                    participant_id
                )

            finally:

                pending_floor_requests.discard(
                    participant_id
                )

        asyncio.create_task(
            process_request()
        )


    ctx.room.on(
        "data_received",
        on_data_received,
    )

    register_skin_voice_bridge(
    room=ctx.room,
    session=session,
    session_is_ready=lambda: session_ready,
    restore_listening=lambda: None,
)
    


    async def shutdown_analytics():

        nonlocal analytics_stopped

        if analytics_stopped:
            return

        try:

            models = []

            for usage in session.usage.model_usage:

                item = serialize_usage(usage)

                if item is not None:
                    models.append(item)

            if models:

                queue_analytics(
                    "usage",
                    {
                        "room_name": ctx.room.name,
                        "session_id": session_id,
                        "models": models,
                    },
                )

        except Exception:

            logger.exception(
                "Could not collect final usage"
            )

        analytics_stopped = True

        try:

            await asyncio.wait_for(
                analytics_queue.join(),
                timeout=10,
            )

        except asyncio.TimeoutError:

            logger.warning(
                "Analytics queue did not flush"
            )

        analytics_task.cancel()

        try:

            await analytics_task

        except asyncio.CancelledError:
            pass

    ctx.add_shutdown_callback(
        shutdown_analytics
    )


    # Use one peer connection for sending and receiving streams. This is an
    # optional LiveKit transport mode, useful to test when separate publisher
    # and subscriber connections have been timing out on Windows.
    await ctx.connect(single_peer_connection=True)

    # Keep track of exactly why the avatar or the whole meeting disappears.
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        logger.warning(
            "MATEO DISCONNECT DIAGNOSTIC | participant=%s | reason=%s",
            participant.identity,
            getattr(participant, "disconnect_reason", None),
        )

    @ctx.room.on("disconnected")
    def on_room_disconnected(reason):
        logger.error("MATEO ROOM DISCONNECTED | reason=%s", reason)

    @ctx.room.on("reconnecting")
    def on_room_reconnecting():
        logger.warning("MATEO ROOM RECONNECTING")

    @ctx.room.on("reconnected")
    def on_room_reconnected():
        logger.info("MATEO ROOM RECONNECTED")

    @ctx.room.on("track_unpublished")
    def on_track_unpublished(publication, participant):
        logger.warning(
            "MATEO TRACK UNPUBLISHED | participant=%s | kind=%s",
            participant.identity,
            getattr(publication, "kind", None),
        )

    logger.info(
        "Connected to LiveKit room: %s",
        ctx.room.name,
    )


    # =================================================
    # ANAM AVATAR (replaces Simli; voice / RAG remain unchanged)
    # =================================================
    avatar_provider = os.getenv("AVATAR_PROVIDER", "anam").strip().lower()
    if avatar_provider == "anam":
        anam_api_key = os.getenv("ANAM_API_KEY", "").strip()
        anam_avatar_id = os.getenv("ANAM_AVATAR_ID", "").strip()
        anam_avatar_model = os.getenv("ANAM_AVATAR_MODEL", "").strip()

        logger.info(
            "ANAM DEBUG | enabled=True | key=%s | avatar_id=%s",
            bool(anam_api_key),
            bool(anam_avatar_id),
        )
        if not anam_api_key or not anam_avatar_id:
            raise RuntimeError("ANAM_API_KEY or ANAM_AVATAR_ID is missing in .env")

        persona_kwargs = {
            "name": "Mateo",
            "avatarId": anam_avatar_id,
        }
        if anam_avatar_model:
            persona_kwargs["avatarModel"] = anam_avatar_model

        avatar = anam.AvatarSession(
            persona_config=anam.PersonaConfig(**persona_kwargs),
            api_key=anam_api_key,
            avatar_participant_name="Mateo",
        )
        logger.info("Starting Anam avatar")
        try:
            # Must start BEFORE session.start so avatar receives speech audio.
            await avatar.start(session, room=ctx.room)
            # The start call initiates avatar provisioning; wait until Anam's
            # video track is actually published before beginning the meeting.
            await avatar.wait_for_join(timeout=30.0)
            logger.info("MATEO ANAM VIDEO READY")
        except Exception:
            logger.exception("Anam avatar startup failed")
            raise
        logger.info("Anam avatar start completed; verify its video track")
    elif avatar_provider in {"none", "off", "voice"}:
        logger.warning("Avatar disabled: voice-only mode")
    else:
        raise RuntimeError(
            f"Unsupported AVATAR_PROVIDER={avatar_provider!r}. Use 'anam' or 'voice'."
        )


    initial_participant_id = None

    for participant in (
        ctx.room.remote_participants.values()
    ):

        if participant.identity.startswith(
            "human_"
        ):

            initial_participant_id = (
                participant.identity
            )

            break


    options = room_io.RoomOptions(
        close_on_disconnect=False,
    )

    if initial_participant_id:

        options.participant_identity = (
            initial_participant_id
        )

    await session.start(
        room=ctx.room,
        agent=MateoAssistant(),
        room_options=options,
    )

    session_ready = True

    logger.info(
        "Mateo AI session started"
    )



    linked = (
        session.room_io.linked_participant
        if session.room_io
        else None
    )

    if (
        linked
        and linked.identity.startswith(
            "human_"
        )
    ):

        active_speaker_id = (
            linked.identity
        )

        await broadcast_floor(
            active_speaker_id,
            linked.name or "Participant",
        )


    await session.generate_reply(
        instructions=(
            "Introduce yourself as Mateo, "
            "the AI meeting assistant. "
            "Welcome the participants briefly. "
            "Tell them they can click "
            "Talk to Mateo before speaking. "
            "Keep your welcome natural and short."
        )
    )

    logger.info(
        "Mateo is ready for group meetings "
        "with admin analytics"
    )


if __name__ == "__main__":

    agents.cli.run_app(
        server
    )

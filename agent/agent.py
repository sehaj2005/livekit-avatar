import asyncio
import json
import logging
import os
import re
import time
import uuid

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from skin_voice_bridge import register_skin_voice_bridge

from dotenv import load_dotenv
from google.genai import types as gemini_types

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    APIConnectionError,
    APIConnectOptions,
    SessionConnectOptions,
    StopResponse,
    function_tool,
    inference,
    llm,
    room_io,
    tokenize,
    tts,
)
from livekit.plugins import google, openai, silero, simli
from windows_speech import WindowsSpeechTTS



load_dotenv(".env")

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("mateo-agent")

AGENT_NAME = "avatar-agent"
FLOOR_TOPIC = "mateo-floor"
OLLAMA_URL = "http://127.0.0.1:11434"

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
    document_instructions = ""

    def __init__(self, room=None):
        self._meeting_room = room

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

                "Begin immediately with a short sentence that answers the question. "
                "Then develop the answer. For explanations and descriptions, explain how it "
                "works, why it matters in the situation, and give one concrete example. "
                "If you give a definition, use it as the opening to the explanation. "
                "For how-to questions, give concrete steps in order and explain what to expect. "
                "For troubleshooting, give a likely cause, a check, and a fix. "
                "For comparisons, explain the tradeoffs and recommend an option for the stated situation. "
                "Answer every part of a multipart question. Most explanations take four to six "
                "short spoken sentences. Give more detail if asked. A simple fact or calculation "
                "can be shorter. Use conversational language without headings, Markdown, or filler. "
                "Check arithmetic, give exact units, and state important assumptions. "
                "Ask one focused question if essential details are missing. "

                "Maintain the shared conversation "
                "context across speaker changes. "

                "Answer general questions using your own knowledge, "
                "including when the conversation moves on from Skin Check. "

                "You are an AI assistant, "
                "not a human being. "

                "Do not invent information. "

                "Do not claim you are listening to "
                "everyone simultaneously. " + self.document_instructions
            )
        )

    async def _answer_from_documents(self, text: str, *, explicit: bool = False) -> bool:
        if self._meeting_room is None or self.session.room_io is None:
            return False
        linked = self.session.room_io.linked_participant
        if linked is None or not linked.identity.startswith("human_"):
            return True
        participant_id = linked.identity
        text = text.strip()
        if not text:
            return False
        if not explicit and not re.search(
            r"\b(?:(?:my|our|the|this|that|these|those|uploaded)\s+"
            r"(?:uploaded\s+)?(?:documents?|files?|pdfs?)|"
            r"(?:documents?|files?|pdfs?)\s+(?:I|we)\s+uploaded)\b",
            text,
            re.IGNORECASE,
        ):
            return False

        def query():
            request = Request(
                BACKEND_URL + "/api/v1/agent/documents/query",
                data=json.dumps({"room_name": self._meeting_room.name,
                                 "participant_id": participant_id, "query": text[:2000]}).encode(),
                headers={"Content-Type": "application/json", "X-Agent-Event-Key": AGENT_EVENT_KEY},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())

        try:
            result = await asyncio.to_thread(query)
            answer = result["answer"]
        except Exception:
            logger.exception("Document lookup failed")
            answer = "I cannot access your documents right now. Please try again shortly."
        current = self.session.room_io.linked_participant
        if current is None or current.identity != participant_id:
            return True
        if answer:
            self.session.say(answer, allow_interruptions=True, add_to_chat_ctx=False)
            return True
        return False

    async def on_user_turn_completed(self, turn_ctx, new_message):
        if await self._answer_from_documents(new_message.text_content or ""):
            raise StopResponse()

    async def on_user_turn_exceeded(self, ev):
        if not await self._answer_from_documents(ev.transcript):
            await super().on_user_turn_exceeded(ev)


class RealtimeMateoAssistant(MateoAssistant):
    document_instructions = (
        "For questions about uploaded meeting documents, call answer_uploaded_document "
        "before speaking. The application will speak the document answer."
    )

    @function_tool()
    async def answer_uploaded_document(self, question: str) -> None:
        """Ask the meeting app to read the answer from the selected speaker's uploaded documents."""
        if not await self._answer_from_documents(question, explicit=True):
            self.session.say(
                "I couldn't find an answer in your uploaded documents.",
                allow_interruptions=True,
                add_to_chat_ctx=False,
            )



# No extra idle Python worker on an 8-GB development laptop.
# The longer startup timeout is retained for Windows process creation.
server = AgentServer(
    initialize_process_timeout=60.0,
    num_idle_processes=0,
)


def warm_ollama(model: str, instructions: str) -> None:
    request = Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": "Ready?"},
            ],
            "options": {"num_predict": 1, "temperature": 0},
            "stream": False,
            "keep_alive": "15m",
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            response.read()
    except OSError:
        logger.warning("Could not preload the local Ollama reply model")


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


    google_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    use_gemini_live = bool(google_api_key) and (
        os.getenv("MATEO_GEMINI_LIVE", "").strip().lower() == "true"
    )
    ollama_model = "" if use_gemini_live else os.getenv("MATEO_OLLAMA_MODEL", "").strip()
    if google_api_key:
        # Gemini speech synthesis uses fields available on the Developer API beta endpoint.
        os.environ.setdefault("GOOGLE_GENAI_API_VERSION", "v1beta")
    if use_gemini_live:
        reply_llm = google.realtime.RealtimeModel(
            model="gemini-3.8-live",
            voice="Charon",
            api_key=google_api_key,
            vertexai=False,
            realtime_input_config=gemini_types.RealtimeInputConfig(
                automatic_activity_detection=gemini_types.AutomaticActivityDetection(
                    silence_duration_ms=450, prefix_padding_ms=180
                )
            ),
            tool_behavior=gemini_types.Behavior.NON_BLOCKING,
            tool_response_scheduling=gemini_types.FunctionResponseScheduling.SILENT,
        )
        ctx.add_shutdown_callback(reply_llm.aclose)
        logger.info("Mateo replies use Gemini Live native audio")
    elif google_api_key:
        reply_llm = google.LLM(
            model="gemini-3.5-flash-lite", api_key=google_api_key, vertexai=False
        )
    else:
        reply_llm = inference.LLM(model="google/gemma-4-31b-it")

    if ollama_model:
        reply_llm = llm.FallbackAdapter(
            [
                openai.LLM.with_ollama(
                    model=ollama_model, base_url=f"{OLLAMA_URL}/v1", temperature=0
                ),
                reply_llm,
            ],
            attempt_timeout=20,
            max_retry_per_llm=0,
        )
        logger.info("Mateo replies use local Ollama model %s with cloud fallback", ollama_model)

    windows_voice = None
    use_windows_voice = (
        os.name == "nt" and os.getenv("MATEO_USE_WINDOWS_VOICE", "").strip().lower() == "true"
    )
    if google_api_key:
        reply_tts = google.beta.GeminiTTS(
            model="gemini-3.1-flash-tts-preview",
            voice_name="Charon",
            api_key=google_api_key,
            vertexai=False,
        )
        if os.name == "nt":
            windows_voice = WindowsSpeechTTS()
            ctx.add_shutdown_callback(windows_voice.aclose)
            voices = [windows_voice, reply_tts] if use_windows_voice else [reply_tts, windows_voice]
            reply_tts = tts.FallbackAdapter(
                voices, max_retry_per_tts=0
            )
            if use_windows_voice:
                reply_tts = tts.StreamAdapter(
                    tts=reply_tts,
                    sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(
                        min_sentence_len=8,
                        retain_format=True,
                    ),
                )
                logger.info("Mateo speech synthesis prefers the local Windows voice")
        session = AgentSession(
            vad=silero.VAD.load(),
            turn_handling={"turn_detection": "realtime_llm" if use_gemini_live else "vad"},
            stt=None if use_gemini_live else google.beta.GeminiSTT(
                api_key=google_api_key, vertexai=False
            ),
            llm=reply_llm,
            tts=reply_tts,
            conn_options=SessionConnectOptions(
                llm_conn_options=APIConnectOptions(timeout=25),
                tts_conn_options=APIConnectOptions(timeout=25, max_retry=0),
            ),
        )
        if not use_gemini_live:
            logger.info("Mateo transcription uses direct Gemini STT")
    else:
        # Deepgram Flux supplies end-of-turn detection for LiveKit Inference.
        session = AgentSession(
            turn_handling={
                "turn_detection": "stt",
                "endpointing": {"min_delay": 0.0},
            },
            stt=inference.STT(model="deepgram/flux-general", language="en"),
            llm=reply_llm,
            tts=inference.TTS(
                model="fishaudio/s2.1-pro",
                voice="fa4c9eb3dccc4806b382b40d61c6b10a",
            ),
        )


    session_id = uuid.uuid4().hex

    active_speaker_id = None
    manual_floor_until = 0.0
    active_human_speakers = ()

    session_ready = False
    session_ready_event = asyncio.Event()

    speaker_lock = asyncio.Lock()

    pending_floor_requests = set()
    voice_failure_reported = False

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
        role = getattr(item, "role", None)

        if role == "assistant":
            timings = {
                key: round(value, 3)
                for key in (
                    "e2e_latency",
                    "llm_node_ttft",
                    "llm_node_ttfs",
                    "tts_node_ttfb",
                    "playback_latency",
                )
                if isinstance(value := item.metrics.get(key), (int, float))
            }
            if timings:
                logger.info("Mateo reply timings (seconds): %s", timings)
            return

    
        if role != "user":
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

    async def publish_voice_failure(status: str):
        nonlocal voice_failure_reported
        try:
            await ctx.room.local_participant.set_attributes(
                {"mateo.voice_status": status}
            )
        except Exception:
            voice_failure_reported = False
            logger.exception("Could not publish Mateo voice status")

    @session.on("error")
    def on_voice_error(event):
        nonlocal voice_failure_reported
        recoverable = getattr(event.error, "recoverable", None)
        logger.warning(
            "Mateo voice error | source=%s | model=%s | recoverable=%s",
            type(event.source).__name__,
            getattr(event.source, "model", None),
            recoverable,
        )
        if (
            recoverable is not False
            or voice_failure_reported
            or getattr(event.error, "type", None) not in {"stt_error", "llm_error", "tts_error"}
        ):
            return

        status = "unavailable"
        error = getattr(event.error, "error", None)
        for _ in range(8):
            if error is None:
                break
            if 429 in (getattr(error, "status_code", None), getattr(error, "status", None)):
                status = "rate_limited"
                break
            error = error.__cause__

        voice_failure_reported = True
        asyncio.create_task(publish_voice_failure(status))

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
        *,
        automatic: bool = False,
    ):

        nonlocal active_speaker_id, manual_floor_until

        # A user can request the floor while the avatar/session is starting.
        # Keep that request until RoomIO can actually attach their microphone.
        await session_ready_event.wait()

        async with speaker_lock:

            if automatic and (
                time.monotonic() < manual_floor_until
                or active_speaker_id in active_human_speakers
                or not active_human_speakers
                or participant_id != active_human_speakers[0]
                or session.agent_state in {"speaking", "thinking"}
            ):
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

            linked = session.room_io.linked_participant
            if linked is None or linked.identity != participant_id:

                try:
                    if not automatic:
                        session.interrupt()
                    session.clear_user_turn()
                    session.room_io.set_participant(
                        participant_id
                    )

                except Exception:

                    logger.exception(
                        "Speaker switching failed"
                    )

                    return

            active_speaker_id = participant_id
            if not automatic:
                # Give the selected participant time to begin after the acknowledgement.
                manual_floor_until = time.monotonic() + 2.0

            await broadcast_floor(
                participant_id,
                participant.name or "Participant",
            )


    def on_active_speakers_changed(speakers):
        nonlocal active_human_speakers

        active_human_speakers = tuple(
            speaker.identity for speaker in speakers if speaker.identity.startswith("human_")
        )
        if (
            session_ready_event.is_set()
            and active_human_speakers
            and active_speaker_id not in active_human_speakers
        ):
            asyncio.create_task(switch_speaker(active_human_speakers[0], automatic=True))


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

            except Exception:
                logger.exception("Could not process speaker request")

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
    ctx.room.on("active_speakers_changed", on_active_speakers_changed)

    register_skin_voice_bridge(
        room=ctx.room,
        session=session,
        session_is_ready=lambda: session_ready,
        speaker_lock=speaker_lock,
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
    # Preload while LiveKit and the avatar connect, before the first question.
    assistant_type = RealtimeMateoAssistant if use_gemini_live else MateoAssistant
    assistant = assistant_type(room=ctx.room)
    ollama_warmup = (
        asyncio.create_task(asyncio.to_thread(warm_ollama, ollama_model, assistant.instructions))
        if ollama_model
        else None
    )
    windows_warmup = (
        asyncio.create_task(windows_voice.warm_up())
        if windows_voice is not None and use_windows_voice
        else None
    )
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


    enable_simli = os.getenv("ENABLE_SIMLI", "false").strip().lower() == "true"
    if enable_simli:
        simli_api_key = os.getenv("SIMLI_API_KEY", "").strip()
        simli_face_id = os.getenv("SIMLI_FACE_ID", "").strip()
        if not simli_api_key or not simli_face_id:
            raise RuntimeError("SIMLI_API_KEY and SIMLI_FACE_ID must be configured in .env")

        avatar = simli.AvatarSession(
            simli_config=simli.SimliConfig(
                api_key=simli_api_key,
                face_id=simli_face_id,
                max_idle_time=int(os.getenv("SIMLI_MAX_IDLE_TIME", "1800")),
                max_session_length=int(os.getenv("SIMLI_MAX_SESSION_LENGTH", "3600")),
            ),
            avatar_participant_name="Mateo",
        )
        logger.info("Starting Simli avatar")
        try:
            # Start before session.start so the avatar receives the agent's audio.
            await avatar.start(session, room=ctx.room)
            await avatar.wait_for_join(timeout=30.0)
        except Exception:
            logger.exception("Simli avatar startup failed")
            raise
        logger.info("Mateo Simli avatar joined")
    else:
        logger.info("Simli disabled: voice-only mode")


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
        agent=assistant,
        room_options=options,
    )

    if ollama_warmup is not None:
        await ollama_warmup
    if windows_warmup is not None:
        try:
            await windows_warmup
        except APIConnectionError:
            logger.warning("Could not warm up Windows speech synthesis")
    session_ready = True
    try:
        await ctx.room.local_participant.set_attributes({"mateo.skin_voice": "ready"})
    except Exception:
        logger.warning("Could not advertise Skin Check playback readiness", exc_info=True)

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

    session_ready_event.set()


    await session.say(
        "Hi, I'm Mateo, your meeting assistant. Click Talk to Mateo when you're ready."
    )

    logger.info(
        "Mateo is ready for group meetings "
        "with admin analytics"
    )


if __name__ == "__main__":

    agents.cli.run_app(
        server
    )

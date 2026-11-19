"""Speak signed Skin Check summaries in the meeting after participant consent."""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.skin_voice import (
    VOICE_TOPIC,
    build_skin_voice_summary,
    verify_skin_voice_grant,
)

logger = logging.getLogger("mateo-skin-voice")


def register_skin_voice_bridge(room, session, session_is_ready, speaker_lock):
    played_nonces: dict[str, int] = {}
    speech_busy = False

    async def send_status(participant_id, request_id, status, reason=None):
        if request_id is None:
            return
        message = {"request_id": request_id, "status": status}
        if reason is not None:
            message["reason"] = reason
        try:
            await room.local_participant.publish_data(
                json.dumps(message),
                reliable=True,
                destination_identities=[participant_id],
                topic=f"{VOICE_TOPIC}-status",
            )
        except Exception:
            logger.warning("Skin Check playback status could not be sent", exc_info=True)

    async def handle_request(participant_id, incoming, request_id):
        nonlocal speech_busy

        if not session_is_ready():
            await send_status(participant_id, request_id, "failed", "not_ready")
            return
        try:
            verified = verify_skin_voice_grant(
                incoming.get("voice_grant"), room_name=room.name, participant_id=participant_id
            )
        except Exception:
            logger.exception("Could not verify Skin Check voice authorization")
            await send_status(participant_id, request_id, "failed", "unavailable")
            return
        if verified is None:
            await send_status(participant_id, request_id, "failed", "invalid_grant")
            return

        now = int(time.time())
        for nonce, expiry in list(played_nonces.items()):
            if expiry < now:
                del played_nonces[nonce]
        nonce = verified["nonce"]
        if nonce in played_nonces:
            await send_status(participant_id, request_id, "played")
            return
        if speech_busy:
            await send_status(participant_id, request_id, "failed", "busy")
            return

        speech_busy = True
        try:
            await send_status(participant_id, request_id, "accepted")
            async with speaker_lock:
                if participant_id not in room.remote_participants:
                    return
                was_enabled = session.input.audio_enabled
                try:
                    # Keep echo and speaker handoffs from interrupting the shared summary.
                    session.input.set_audio_enabled(False)
                    handle = session.say(
                        build_skin_voice_summary(verified),
                        allow_interruptions=False,
                        add_to_chat_ctx=False,
                    )
                    await handle.wait_for_playout()
                    playback_error = handle.exception()
                    if playback_error is not None:
                        raise playback_error
                    if handle.interrupted:
                        await send_status(participant_id, request_id, "failed", "interrupted")
                        return
                    played_nonces[nonce] = verified["expires_at"]
                finally:
                    session.input.set_audio_enabled(was_enabled)
            logger.info("Skin Check summary finished playing")
            await send_status(participant_id, request_id, "played")
        except Exception:
            logger.exception("Skin Check summary playback failed")
            await send_status(participant_id, request_id, "failed", "unavailable")
        finally:
            speech_busy = False

    def on_data_received(packet):
        if packet.topic != VOICE_TOPIC or not packet.participant or len(packet.data) > 8192:
            return
        participant_id = packet.participant.identity
        if not participant_id.startswith("human_") or participant_id not in room.remote_participants:
            return
        try:
            incoming = json.loads(packet.data)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(incoming, dict):
            return
        request_id = incoming.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
            request_id = None
        asyncio.create_task(handle_request(participant_id, incoming, request_id))

    room.on("data_received", on_data_received)

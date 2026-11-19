"""Authenticated, consent-based skin image test and optional AI observations."""
import io
import logging
import os

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from livekit import api
from PIL import Image, UnidentifiedImageError

from backend import analytics
from backend.skin_analyzer import analyze_image

load_dotenv('.env')
logger = logging.getLogger('mateo-skin')
router = APIRouter(prefix='/api/v1/skin', tags=['Skin Analysis'])
security = HTTPBearer()
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 12_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def verify_participant_token(token: str):
    key = os.getenv('LIVEKIT_API_KEY')
    secret = os.getenv('LIVEKIT_API_SECRET')
    if not key or not secret:
        raise HTTPException(503, 'LiveKit authentication is not configured.')
    try:
        claims = api.TokenVerifier(api_key=key, api_secret=secret).verify(token)
    except Exception as exc:
        raise HTTPException(401, 'Invalid or expired LiveKit participant token.') from exc
    if not claims.identity or not claims.identity.startswith('human_'):
        raise HTTPException(403, 'Only authenticated human participants may use Skin Check.')
    if not claims.video or not claims.video.room_join or not claims.video.room:
        raise HTTPException(403, 'LiveKit meeting permissions are missing.')
    return claims


def verify_meeting(meeting_id: str, room_name: str):
    with analytics.connect() as db:
        meeting = db.execute(
            'SELECT room_name FROM meetings WHERE meeting_id = ?',
            (meeting_id,),
        ).fetchone()
    if not meeting:
        raise HTTPException(404, 'Meeting not found.')
    if meeting['room_name'] != room_name:
        raise HTTPException(403, 'Token does not belong to this meeting.')


async def verify_connected(room_name: str, identity: str):
    try:
        async with api.LiveKitAPI() as livekit:
            result = await livekit.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
    except Exception as exc:
        logger.exception('Unable to check connected participant')
        raise HTTPException(503, 'Cannot verify your active meeting right now.') from exc
    if not any(p.identity == identity for p in result.participants):
        raise HTTPException(403, 'You must be connected to the meeting.')


async def valid_image(upload: UploadFile) -> tuple[bytes, str]:
    allowed = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}
    raw = await upload.read(MAX_IMAGE_BYTES + 1)
    if not raw:
        raise HTTPException(400, 'Image is empty.')
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, 'Image must be at most 5 MB.')
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise HTTPException(400, 'Image resolution is too large.')
            img.load()
            image_format = img.format
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, 'Invalid image.') from exc
    if image_format not in allowed:
        raise HTTPException(400, 'Supported formats are JPEG, PNG and WebP.')
    return raw, allowed[image_format]


async def authorize_and_validate(
    meeting_id: str, token: str, image: UploadFile
) -> tuple[bytes, str, str]:
    claims = verify_participant_token(token)
    verify_meeting(meeting_id, claims.video.room)
    await verify_connected(claims.video.room, claims.identity)
    data, mime = await valid_image(image)
    return data, mime, claims.identity


@router.post('/analyze')
async def capture_test(
    meeting_id: str = Form(...),
    consent: bool = Form(...),
    image: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    if not consent:
        raise HTTPException(400, 'Participant consent is required.')
    _, _, identity = await authorize_and_validate(
        meeting_id, credentials.credentials, image
    )
    return {
        'success': True,
        'status': 'capture_verified',
        'meeting_id': meeting_id,
        'participant_identity': identity,
        'message': 'Image capture verified. No image was stored.',
    }


@router.post('/vision')
async def vision_check(
    meeting_id: str = Form(...),
    consent: bool = Form(...),
    external_processing_consent: bool = Form(...),
    image: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    if not consent or not external_processing_consent:
        raise HTTPException(
            400, 'Consent for this snapshot and external AI processing is required.'
        )
    data, mime, identity = await authorize_and_validate(
        meeting_id, credentials.credentials, image
    )
    assessment = await analyze_image(data, mime)
    return {
        'success': True,
        'status': 'visual_observations',
        'meeting_id': meeting_id,
        'participant_identity': identity,
        'assessment': assessment.model_dump(),
        'disclaimer': (
            'This is experimental visual information, not a medical diagnosis. '
            'A photo cannot confirm acne, allergy, infection, or another condition. '
            'No medicines are recommended. A clinician should assess concerning findings.'
        ),
    }

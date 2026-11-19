
"""
Mateo AI - Local Skin RAG API

Meeting-authorized endpoint with:
- LiveKit participant authentication
- Meeting validation
- Camera image validation
- Detailed error classification
- Local Ollama + Medicine RAG execution

No clinical treatment recommendations.
"""

import asyncio
import logging
import time

from backend.skin_voice import create_skin_voice_grant
from backend.skin_routes import verify_participant_token

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from backend.local_skin_rag import (
    analyze_skin_and_retrieve,
)

from backend.skin_access import reserve_analysis, finish_analysis
from backend.participant_documents import retrieve_documents, document_answer

from backend.skin_routes import (
    authorize_and_validate,
)


logger = logging.getLogger(
    "mateo-local-skin-rag-api"
)

router = APIRouter(
    prefix="/api/v1/skin",
    tags=["Skin Analysis"],
)

security = HTTPBearer()


def process_analysis(image_data: bytes, meeting_id: str, identity: str) -> dict:
    # Keep the reservation until both blocking model calls finish, even if the
    # browser disconnects and cancels its awaiting HTTP request.
    request_id = reserve_analysis(meeting_id, identity)
    started = time.monotonic()
    try:
        result = analyze_skin_and_retrieve(
            image_data,
            reference_retriever=lambda features, limit=5: retrieve_documents(
                meeting_id, identity, " ".join(features)
            ),
        )
        explanation_started = time.monotonic()
        try:
            explanation = document_answer(result["medicine_rag"])
        except Exception:
            logger.exception("Medical RAG explanation generation failed")
            explanation = (
                "The medical-reference explanation is temporarily unavailable. "
                "Please consult the retrieved documents or a healthcare professional."
            )
        result["medical_explanation"] = explanation
        result.setdefault("timings", {})["explanation_seconds"] = round(
            time.monotonic() - explanation_started, 2
        )
        result["processing_time_seconds"] = round(time.monotonic() - started, 1)
        return result
    finally:
        finish_analysis(request_id)


@router.post("/local-rag")
async def local_rag(
    meeting_id: str = Form(...),
    consent: bool = Form(...),
    processing_consent: bool = Form(False),
    image: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(
        security
    ),
):

    if not consent or not processing_consent:
        raise HTTPException(
            status_code=400,
            detail="Consent to capture and process this snapshot is required.",
        )

    # Existing LiveKit validation.
    # Authentication failures should retain
    # their original HTTP status codes.
    image_data, mime, identity = (
        await authorize_and_validate(
            meeting_id,
            credentials.credentials,
            image,
        )
    )

    logger.info(
        "Starting authorized local skin analysis "
        "for participant %s, image bytes=%s",
        identity,
        len(image_data),
    )

    try:
        result = await asyncio.to_thread(
            process_analysis,
            image_data,
            meeting_id,
            identity,
        )

    except HTTPException:
        raise

    except ValueError as exc:
        logger.warning(
            "Camera image validation failed: %s",
            exc,
        )

        raise HTTPException(
            status_code=422,
            detail=(
                "Camera image was rejected. "
                "Check your lighting, make sure your "
                "face is visible, and capture a new frame."
            ),
        ) from exc

    except RuntimeError as exc:
        message = str(exc).lower()

        logger.exception(
            "Local Ollama / Medicine RAG failure"
        )

        if (
            "token budget" in message
            or "empty" in message
            or "invalid json" in message
            or "no final" in message
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Ollama did not produce a valid "
                    "analysis response. You can retry "
                    "without refreshing Mateo."
                ),
            ) from exc

        if "timeout" in message or "timed out" in message:
            raise HTTPException(
                status_code=504,
                detail=(
                    "Local Ollama inference timed out. "
                    "Confirm Ollama is running, then retry."
                ),
            ) from exc

        if "index" in message or "rag" in message:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Medicine RAG retrieval failed. "
                    "Check the backend logs and index."
                ),
            ) from exc

        raise HTTPException(
            status_code=503,
            detail=(
                "Local analysis failed. "
                "Check the FastAPI terminal for details."
            ),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Unexpected local Skin RAG failure"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Unexpected server error during "
                "local Skin RAG processing."
            ),
        ) from exc

    logger.info("Local skin analysis completed successfully.")

    # The existing LiveKit token has already been validated
    # by authorize_and_validate().
    claims = verify_participant_token(
        credentials.credentials
    )

    voice_grant = create_skin_voice_grant(
        room_name=claims.video.room,
        participant_id=identity,
        observations=result["skin_observations"],
        medical_explanation=result["medical_explanation"],
    )

    return {
        **result,
        "voice_grant": voice_grant,
    }


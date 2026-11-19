
import asyncio
import base64
import logging
import os
import time

from dotenv import load_dotenv
from fastapi import HTTPException
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError


load_dotenv(".env", override=True)

logger = logging.getLogger("mateo-skin-vision")

MODEL_NAME = os.getenv(
    "SKIN_VISION_MODEL",
    "gemini-3.8-flash",
)

MAX_IMAGE_SIZE = 5 * 1024 * 1024

GEMINI_REQUEST_TIMEOUT = 60
BACKEND_TIMEOUT = 75

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


class SkinObservation(BaseModel):
    feature: str
    location: str
    description: str


class VisionAssessment(BaseModel):
    image_usable: bool
    image_quality_note: str

    observations: list[SkinObservation] = Field(
        default_factory=list
    )

    general_care: list[str] = Field(
        default_factory=list
    )

    needs_clinician_review: bool
    referral_note: str


SKIN_PROMPT = """
You are Mateo AI's experimental facial skin
visual observation assistant.

Examine the submitted camera image.

Your job is to describe visible skin features
without diagnosing any medical condition.

You may describe:

- Acne-like visible bumps
- Areas of redness
- Spots
- Uneven skin texture
- Flaking
- Visible irritation
- Pigmentation variation

STRICT RULES:

1. Describe only what is actually visible.

2. Do not identify the person.

3. Do not infer ethnicity, age, gender,
   or other personal characteristics.

4. Do not diagnose acne, allergies,
   infections, eczema, rosacea, or any
   other disease.

5. Do not recommend medicines,
   prescriptions, antibiotics,
   steroids, or dosages.

6. Do not provide clinical severity
   grades or confidence percentages.

7. If image lighting, framing, or
   quality is insufficient, set
   image_usable to false.

8. If image_usable is false,
   observations must be empty.

9. General care suggestions must be
   non-medicated and low-risk.

10. Recommend professional assessment
    for concerning, painful, persistent,
    or worsening skin symptoms.

11. Do not claim that a skin disease
    has been ruled out.

12. Keep the assessment concise.

Return JSON with the following fields:

image_usable: boolean
image_quality_note: string

observations: list of:
    feature: string
    location: string
    description: string

general_care: list of strings

needs_clinician_review: boolean
referral_note: string

Do not include markdown code fences.
"""


def validate_image(
    image_bytes: bytes,
    mime_type: str,
) -> None:

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail="No camera image received.",
        )

    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Image exceeds the 5 MB limit.",
        )

    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported camera image format.",
        )


def create_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "GEMINI_API_KEY is missing "
                "from the project .env file."
            ),
        )

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_REQUEST_TIMEOUT * 1000,
            retry_options=types.HttpRetryOptions(
                attempts=1,
            ),
        ),
    )


def get_error_status(exc: Exception):
    status = getattr(exc, "status_code", None)

    if status is None:
        status = getattr(exc, "code", None)

    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)

    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def process_vision_request(
    image_bytes: bytes,
    mime_type: str,
) -> VisionAssessment:

    client = create_client()

    model_name = os.getenv(
        "SKIN_VISION_MODEL",
        MODEL_NAME,
    )

    image_base64 = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    start_time = time.monotonic()

    logger.info(
        "Sending skin image to Gemini: %s",
        model_name,
    )

    try:
        response = client.interactions.create(
            model=model_name,
            input=[
                {
                    "type": "text",
                    "text": SKIN_PROMPT,
                },
                {
                    "type": "image",
                    "data": image_base64,
                    "mime_type": mime_type,
                },
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": (
                    VisionAssessment.model_json_schema()
                ),
            },
            store=False,
            automatic_continuation=False,
            timeout=GEMINI_REQUEST_TIMEOUT,
        )

        output_text = response.output_text

        if not output_text:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Gemini returned an empty response. "
                    "Please retry."
                ),
            )

        try:
            assessment = (
                VisionAssessment.model_validate_json(
                    output_text
                )
            )

        except ValidationError as exc:
            logger.error(
                "Invalid Gemini JSON response: %s",
                str(exc)[:500],
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Gemini returned an invalid "
                    "skin analysis response."
                ),
            ) from exc

        if not assessment.image_usable:
            assessment.observations = []

        assessment.observations = (
            assessment.observations[:8]
        )

        assessment.general_care = (
            assessment.general_care[:5]
        )

        logger.info(
            "Skin analysis completed in %.2f seconds",
            time.monotonic() - start_time,
        )

        return assessment

    except HTTPException:
        raise

    except Exception as exc:
        status = get_error_status(exc)

        logger.exception(
            "Gemini request failed, status=%s",
            status,
        )

        if status == 503:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Gemini is temporarily unavailable "
                    "or overloaded. Please try again later."
                ),
            ) from exc

        if status == 429:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Gemini rate limit reached. "
                    "Please try again later."
                ),
            ) from exc

        if status == 404:
            raise HTTPException(
                status_code=502,
                detail=(
                    "The configured Gemini model "
                    "is unavailable. Check "
                    "SKIN_VISION_MODEL in .env."
                ),
            ) from exc

        if status in (401, 403):
            raise HTTPException(
                status_code=502,
                detail=(
                    "Gemini rejected the API key "
                    "or model access."
                ),
            ) from exc

        error_text = str(exc).lower()

        if (
            "timeout" in error_text
            or "timed out" in error_text
        ):
            raise HTTPException(
                status_code=504,
                detail=(
                    "Gemini request timed out. "
                    "Please try again."
                ),
            ) from exc

        raise HTTPException(
            status_code=502,
            detail=(
                "Gemini skin analysis failed. "
                "Check the backend terminal."
            ),
        ) from exc

    finally:
        client.close()


async def analyze_image(
    image_bytes: bytes,
    mime_type: str,
) -> VisionAssessment:

    validate_image(
        image_bytes=image_bytes,
        mime_type=mime_type,
    )

    logger.info(
        "Starting skin vision analysis using model %s",
        os.getenv(
            "SKIN_VISION_MODEL",
            MODEL_NAME,
        ),
    )

    try:
        assessment = await asyncio.wait_for(
            asyncio.to_thread(
                process_vision_request,
                image_bytes,
                mime_type,
            ),
            timeout=BACKEND_TIMEOUT,
        )

        return assessment

    except asyncio.TimeoutError as exc:
        logger.error(
            "Skin analysis exceeded %s seconds.",
            BACKEND_TIMEOUT,
        )

        raise HTTPException(
            status_code=504,
            detail=(
                "Skin analysis timed out. "
                "Gemini did not respond in time."
            ),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected skin analysis error."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Skin analysis could not be completed."
            ),
        ) from exc

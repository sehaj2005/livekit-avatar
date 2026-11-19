
"""
Mateo AI - Development Medicine RAG API

Development use only.
Provides source-linked excerpts from unreviewed documents.
Does not generate diagnoses or treatment recommendations.
"""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.medicine_dev_index import (
    DB_PATH,
    search_documents,
)


router = APIRouter(
    prefix="/api/v1/medicine",
    tags=["Medicine RAG Development"],
)


class MedicineSearchRequest(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=300,
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=10,
    )


def verify_dev_access(
    request: Request,
    supplied_token: str | None,
):
    enabled = (
        os.getenv(
            "MATEO_MEDICINE_DEV_SEARCH_ENABLED",
            "false",
        ).lower()
        == "true"
    )

    if not enabled:
        raise HTTPException(
            status_code=404,
            detail="Development search is disabled.",
        )

    client_ip = (
        request.client.host
        if request.client
        else None
    )

    if client_ip not in ("127.0.0.1", "::1"):
        raise HTTPException(
            status_code=403,
            detail="Local development access only.",
        )

    expected_token = os.getenv(
        "MATEO_MEDICINE_DEV_TOKEN",
        "",
    )

    if len(expected_token) < 32:
        raise HTTPException(
            status_code=503,
            detail="Development token is not configured.",
        )

    if not supplied_token or not hmac.compare_digest(
        supplied_token,
        expected_token,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid development token.",
        )


@router.post("/dev-search")
def medicine_dev_search(
    payload: MedicineSearchRequest,
    request: Request,
    x_dev_token: str | None = Header(
        default=None,
        alias="X-Dev-Token",
    ),
):
    verify_dev_access(
        request,
        x_dev_token,
    )

    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "Medicine development index not found. "
                "Run: python -m backend.medicine_dev_index index"
            ),
        )

    try:
        matches = search_documents(
            payload.query,
            payload.limit,
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Medicine retrieval failed.",
        )

    results = []

    for filename, page_number, excerpt, score in matches:
        results.append(
            {
                "source": filename,
                "page": page_number,
                "excerpt": excerpt,
                "bm25_score": score,
                "review_status": "draft_unreviewed",
            }
        )

    return {
        "query": payload.query,
        "status": "draft_unreviewed",
        "development_only": True,
        "total_results": len(results),
        "results": results,
        "notice": (
            "Unreviewed reference excerpts only. "
            "Not for diagnosis, prescriptions, or "
            "patient-specific treatment recommendations."
        ),
    }


# -----------------------------------------------
# Mateo Skin Analysis -> Medicine RAG
# DEVELOPMENT ONLY
# -----------------------------------------------

ALLOWED_SKIN_TERMS = {
    "redness",
    "dryness",
    "irritation",
    "peeling",
    "itching",
    "acne",
    "bumps",
    "rash",
    "sensitivity",
}


class SkinContextRequest(BaseModel):
    observations: list[str] = Field(
        min_length=1,
        max_length=6,
    )
    limit: int = Field(default=5, ge=1, le=10)


@router.post("/dev-skin-context")
def medicine_skin_context(
    payload: SkinContextRequest,
    request: Request,
    x_dev_token: str | None = Header(
        default=None,
        alias="X-Dev-Token",
    ),
):
    # Reuse the existing development security checks.
    verify_dev_access(request, x_dev_token)

    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Medicine development index not found.",
        )

    terms = []

    for value in payload.observations:
        term = value.strip().lower()

        if term not in ALLOWED_SKIN_TERMS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unsupported observation: {term}. "
                    "Use predefined skin-related terms only."
                ),
            )

        if term not in terms:
            terms.append(term)

    query = " ".join(terms)

    try:
        matches = search_documents(
            query,
            payload.limit,
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Medicine context retrieval failed.",
        )

    results = []

    for filename, page, excerpt, score in matches:
        results.append({
            "source": filename,
            "page": page,
            "excerpt": excerpt,
            "bm25_score": score,
            "review_status": "draft_unreviewed",
        })

    return {
        "observations": terms,
        "retrieval_query": query,
        "status": "draft_unreviewed",
        "development_only": True,
        "total_results": len(results),
        "results": results,
        "notice": (
            "These are unreviewed educational excerpts. "
            "The observations are not a diagnosis. "
            "Do not use these excerpts for individual "
            "medicine or treatment recommendations."
        ),
    }


# ------------------------------------------------
# Mateo Skin Analysis -> Medicine RAG Bridge
# Development-only reference retrieval
# ------------------------------------------------

from backend.skin_analyzer import VisionAssessment


@router.post("/dev-vision-context")
def medicine_vision_context(
    assessment: VisionAssessment,
    request: Request,
    x_dev_token: str | None = Header(
        default=None,
        alias="X-Dev-Token",
    ),
):
    verify_dev_access(request, x_dev_token)

    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Medicine development index not found.",
        )

    if not assessment.image_usable:
        return {
            "status": "image_unusable",
            "development_only": True,
            "results": [],
            "notice": "No retrieval performed.",
        }

    matched_terms = []

    for observation in assessment.observations:
        feature = observation.feature.lower()

        for term in ALLOWED_SKIN_TERMS:
            if term in feature and term not in matched_terms:
                matched_terms.append(term)

    if not matched_terms:
        return {
            "status": "no_supported_observations",
            "development_only": True,
            "results": [],
            "notice": (
                "No supported skin observation terms "
                "were found for retrieval."
            ),
        }

    query = " ".join(matched_terms)

    try:
        matches = search_documents(query, 5)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Medicine reference retrieval failed.",
        )

    results = [
        {
            "source": filename,
            "page": page,
            "excerpt": excerpt,
            "bm25_score": score,
            "review_status": "draft_unreviewed",
        }
        for filename, page, excerpt, score in matches
    ]

    return {
        "status": "draft_unreviewed",
        "development_only": True,
        "matched_observations": matched_terms,
        "retrieval_query": query,
        "total_results": len(results),
        "results": results,
        "notice": (
            "Unreviewed educational reference excerpts only. "
            "Visual observations do not establish a diagnosis. "
            "No medicine or patient-specific treatment "
            "recommendations are provided."
        ),
    }


# ====================================================
# MATEO: REAL SKIN IMAGE -> GEMINI -> MEDICINE RAG
# LOCAL DEVELOPMENT ONLY
# ====================================================

import re

from fastapi import Depends, File, Form, UploadFile
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from backend.skin_analyzer import analyze_image
from backend.skin_routes import authorize_and_validate


livekit_security = HTTPBearer()


def get_skin_search_terms(assessment):
    """
    Extract supported visible skin features
    from Gemini's structured response.
    """

    matched_terms = []

    for observation in assessment.observations:
        feature = observation.feature.lower()

        for term in sorted(ALLOWED_SKIN_TERMS):
            pattern = rf"\b{re.escape(term)}\b"

            if re.search(pattern, feature):
                if term not in matched_terms:
                    matched_terms.append(term)

    return matched_terms[:6]


@router.post("/dev-vision-rag")
async def automatic_skin_medicine_rag(
    request: Request,
    meeting_id: str = Form(...),
    consent: bool = Form(...),
    external_processing_consent: bool = Form(...),
    image: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(
        livekit_security
    ),
    x_dev_token: str | None = Header(
        default=None,
        alias="X-Dev-Token",
    ),
):
    # 1. Restrict access to the local developer.
    verify_dev_access(request, x_dev_token)

    # 2. Require image and external AI consent.
    if not consent or not external_processing_consent:
        raise HTTPException(
            status_code=400,
            detail=(
                "Consent for the image and external "
                "AI processing is required."
            ),
        )

    # 3. Check that the development RAG index exists.
    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Medicine development index not found.",
        )

    # 4. Reuse existing LiveKit authentication,
    # meeting verification, and image validation.
    data, mime, identity = await authorize_and_validate(
        meeting_id,
        credentials.credentials,
        image,
    )

    # 5. Run your existing Gemini Skin Analysis.
    assessment = await analyze_image(data, mime)

    # 6. Extract supported visual observations.
    matched_terms = []

    if assessment.image_usable:
        matched_terms = get_skin_search_terms(assessment)

    # 7. Retrieve relevant reference excerpts.
    results = []

    if matched_terms:
        query = " ".join(matched_terms)

        try:
            matches = search_documents(query, 5)

            for filename, page, excerpt, score in matches:
                results.append({
                    "source": filename,
                    "page": page,
                    "excerpt": excerpt,
                    "bm25_score": score,
                    "review_status": "draft_unreviewed",
                })

        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Medicine retrieval failed.",
            ) from exc

    # 8. Return the combined development result.
    return {
        "success": True,
        "status": "development_vision_rag",
        "development_only": True,

        "meeting_id": meeting_id,
        "participant_identity": identity,

        "skin_assessment": assessment.model_dump(),

        "medicine_rag": {
            "status": "draft_unreviewed",
            "matched_observations": matched_terms,
            "retrieval_query": " ".join(matched_terms),
            "total_results": len(results),
            "results": results,
        },

        "disclaimer": (
            "Experimental visual observations are not "
            "a medical diagnosis. Medicine references "
            "are unreviewed development material only. "
            "No individual medicine recommendations "
            "or prescriptions are provided."
        ),
    }




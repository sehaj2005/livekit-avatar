"""Authenticated, meeting-scoped medicine reference retrieval API.

Only returns excerpts from reviewed local reference documents with source labels.
Does not generate prescriptions, diagnose skin diseases or choose medicines.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from backend import medicine_store
from backend.skin_routes import (
    verify_live_participant,
    verify_meeting_membership,
    verify_participant_token,
)

logger = logging.getLogger("mateo-medicine-rag")
router = APIRouter(prefix="/api/v1/medicine", tags=["Medicine RAG"])
security = HTTPBearer()


class SearchRequest(BaseModel):
    meeting_id: str = Field(min_length=3, max_length=128)
    question: str = Field(min_length=3, max_length=400)
    top_k: int = Field(default=5, ge=1, le=8)


@router.post("/search")
async def search_medicine_references(
    body: SearchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    # Reuse the exact authentication and room-membership checks from Skin Check.
    claims = verify_participant_token(credentials.credentials)
    room_name = verify_meeting_membership(body.meeting_id, claims.video.room)
    await verify_live_participant(room_name, claims.identity)

    try:
        references = await asyncio.to_thread(
            medicine_store.search, body.question, body.top_k
        )
    except Exception:
        logger.exception("Medicine reference retrieval failed")
        raise HTTPException(status_code=503, detail="Reference search is temporarily unavailable")

    return {
        "meeting_id": body.meeting_id,
        "participant_identity": claims.identity,
        "question": body.question,
        "results": references,
        "answer": None,
        "status": "sources_found" if references else "no_verified_sources_found",
        "notice": (
            "Reference excerpts only. Relevance scores indicate text similarity, "
            "not whether a medicine is safe or appropriate for you. "
            "Skin photographs cannot establish a diagnosis. "
            "Do not start medication based on these search results. "
            "Review treatment options with a qualified clinician or pharmacist."
        ),
    }

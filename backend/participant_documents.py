"""Participant-owned document retrieval; never writes to the shared medicine corpus."""
import asyncio
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from backend import analytics
from backend.medicine_upload_routes import extract_document, MAX_FILE_SIZE
from backend.medicine_store import chunk_text
from backend.skin_routes import verify_participant_token, verify_meeting, verify_connected

router = APIRouter(prefix="/api/v1", tags=["Participant Documents"])
security = HTTPBearer()
NO_MATCH = "I couldn't find relevant information in your uploaded documents. Please upload a relevant file or ask a more specific question."


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS participant_documents (
        document_id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
        participant_id TEXT NOT NULL, filename TEXT NOT NULL, expires_at REAL NOT NULL
    )""")
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS participant_document_chunks
        USING fts5(document_id UNINDEXED, content)""")


def save_document(meeting_id, identity, filename, data):
    text = extract_document(filename, data).strip()
    if not text:
        raise ValueError("No readable text found. Scanned PDFs need OCR first.")
    if len(text) > 100_000:
        raise ValueError("Document exceeds the 100,000-character text limit.")
    with analytics.connect() as db:
        initialize(db)
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM participant_document_chunks WHERE document_id IN (SELECT document_id FROM participant_documents WHERE expires_at <= ?)", (time.time(),))
        db.execute("DELETE FROM participant_documents WHERE expires_at <= ?", (time.time(),))
        total = db.execute("SELECT COUNT(*) FROM participant_documents WHERE meeting_id = ?", (meeting_id,)).fetchone()[0]
        owned = db.execute("SELECT COUNT(*) FROM participant_documents WHERE meeting_id = ? AND participant_id = ?", (meeting_id, identity)).fetchone()[0]
        if owned >= 5 or total >= 20:
            raise HTTPException(429, "Document limit reached (5 per participant, 20 per meeting).")
        document_id = uuid.uuid4().hex
        db.execute("INSERT INTO participant_documents VALUES (?, ?, ?, ?, ?)",
                   (document_id, meeting_id, identity, filename, time.time() + 86400))
        db.executemany("INSERT INTO participant_document_chunks (document_id, content) VALUES (?, ?)",
                       [(document_id, part) for part in chunk_text(text)])
    return {"success": True, "document_id": document_id, "original_filename": filename,
            "characters_indexed": len(text), "review_status": "user_uploaded_unreviewed"}


def retrieve_documents(meeting_id: str, identity: str, query: str) -> dict:
    stopwords = {"the", "and", "for", "are", "can", "how", "summary", "summarize", "summarise", "overview", "describe", "read", "what", "when", "where", "which", "does", "this", "that", "with", "from", "have", "about", "your", "please", "tell", "could", "would", "should", "document", "documents", "uploaded", "file", "files", "give", "details", "information"}
    terms = list(dict.fromkeys(word for word in re.findall(r"[a-z0-9]{3,}", query.lower()) if word not in stopwords))[:20]
    with analytics.connect() as db:
        initialize(db)
        owned = db.execute("SELECT filename FROM participant_documents WHERE meeting_id = ? AND participant_id = ? AND expires_at > ?",
                           (meeting_id, identity, time.time())).fetchall()
        results = []
        if terms:
            rows = db.execute("""SELECT d.filename, c.content
                FROM participant_document_chunks c JOIN participant_documents d ON d.document_id = c.document_id
                WHERE participant_document_chunks MATCH ? AND d.meeting_id = ?
                AND d.participant_id = ? AND d.expires_at > ?
                ORDER BY bm25(participant_document_chunks) LIMIT 3""",
                (" OR ".join(f'"{term}"' for term in terms), meeting_id, identity, time.time())).fetchall()
            results = [{"source": row["filename"], "page": None, "excerpt": row["content"],
                        "review_status": "user_uploaded_unreviewed"} for row in rows]
        elif owned and re.search(r"\b(summar\w*|overview|details|describe|read)\b", query, re.I):
            rows = db.execute("""SELECT d.filename, c.content FROM participant_document_chunks c
                JOIN participant_documents d ON d.document_id = c.document_id
                WHERE d.meeting_id = ? AND d.participant_id = ? AND d.expires_at > ?
                ORDER BY c.rowid LIMIT 3""", (meeting_id, identity, time.time())).fetchall()
            results = [{"source": row["filename"], "page": None, "excerpt": row["content"],
                        "review_status": "user_uploaded_unreviewed"} for row in rows]
    return {"status": "user_uploaded_unreviewed", "has_documents": bool(owned),
            "retrieval_query": query, "total_results": len(results), "results": results,
            "document_names": [row["filename"] for row in owned]}


def document_answer(rag: dict) -> str:
    # Read attributed excerpts directly, rather than asking an LLM to invent a
    # paraphrase. Uploaded instructions are text to quote, never instructions to execute.
    if not rag.get("has_documents"):
        return "Please upload a reference document first. I will use its matching passages rather than invent an explanation."
    if not rag.get("results"):
        return NO_MATCH
    parts = []
    for item in rag["results"][:2]:
        excerpt = item["excerpt"][:650]
        if len(item["excerpt"]) > 650:
            excerpt = excerpt.rsplit(" ", 1)[0] + "…"
        parts.append(f'From {item["source"]}: “{excerpt}”')
    return "I found these passages in your uploaded documents. " + " ".join(parts) + " These documents are unreviewed reference material, not a medical diagnosis or personalized treatment advice."


@router.post("/skin/documents")
async def upload_document(meeting_id: str = Form(...), file: UploadFile = File(...),
                          credentials: HTTPAuthorizationCredentials = Depends(security)):
    claims = verify_participant_token(credentials.credentials)
    verify_meeting(meeting_id, claims.video.room)
    await verify_connected(claims.video.room, claims.identity)
    with analytics.connect() as db:
        if not db.execute("SELECT 1 FROM participants WHERE meeting_id = ? AND participant_id = ?", (meeting_id, claims.identity)).fetchone():
            raise HTTPException(403, "Participant is not registered in this meeting.")
    filename = Path((file.filename or "").replace("\\", "/")).name[:160]
    if Path(filename).suffix.lower() not in {".txt", ".md", ".pdf"}:
        raise HTTPException(400, "Upload a TXT, MD, or PDF document.")
    data = await file.read(MAX_FILE_SIZE + 1)
    await file.close()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "Maximum document size is 10 MB.")
    try:
        return await asyncio.to_thread(save_document, meeting_id, claims.identity, filename, data)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc


class AgentDocumentQuery(BaseModel):
    room_name: str = Field(max_length=200)
    participant_id: str = Field(max_length=200)
    query: str = Field(min_length=1, max_length=2000)


@router.post("/agent/documents/query", dependencies=[Depends(analytics.agent_required)])
def agent_document_query(body: AgentDocumentQuery):
    with analytics.connect() as db:
        row = db.execute("""SELECT m.meeting_id FROM meetings m JOIN participants p ON p.meeting_id = m.meeting_id
            WHERE m.room_name = ? AND p.participant_id = ?""", (body.room_name, body.participant_id)).fetchone()
    if not row:
        raise HTTPException(403, "Participant does not belong to this meeting.")
    rag = retrieve_documents(row["meeting_id"], body.participant_id, body.query)
    return {
        "has_documents": rag["has_documents"],
        "has_matches": bool(rag["results"]),
        "answer": document_answer(rag),
    }

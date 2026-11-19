"""HTTPS tunnel origin for meeting participants."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.local_skin_rag_routes import local_rag
from backend.main import join_meeting
from backend.participant_documents import upload_document
from backend.skin_routes import capture_test


build_dir = Path(__file__).resolve().parents[1] / "frontend" / "dist"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=build_dir / "assets"), name="assets")
app.add_api_route("/api/v1/meetings/{meeting_id}/join", join_meeting, methods=["POST"])
app.add_api_route("/api/v1/skin/analyze", capture_test, methods=["POST"])
app.add_api_route("/api/v1/skin/local-rag", local_rag, methods=["POST"])
app.add_api_route("/api/v1/skin/documents", upload_document, methods=["POST"])


@app.get("/join")
def guest_join_page():
    return FileResponse(
        build_dir / "index.html",
        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
    )

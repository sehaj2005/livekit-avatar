
"""
Mateo AI - Development Medicine RAG document uploads.

Accepts TXT, MD, and PDF files.
PDF text is extracted locally.
Uploaded content is indexed as unreviewed material.

Never makes an uploaded document clinically approved.
"""

import asyncio
import re
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
)

from backend.admin_accounts import require_admin

router = APIRouter(
    prefix="/api/v1/medicine",
    tags=["Medicine RAG Development"],
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_DIR = PROJECT_ROOT / "medicine_data"

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PDF_PAGES = 100

INDEX_LOCK = asyncio.Lock()


def extract_document(filename: str, data: bytes) -> str:
    extension = Path(filename).suffix.lower()

    if extension in (".txt", ".md"):
        if b"\x00" in data:
            raise ValueError("Binary files are not supported.")

        return data.decode("utf-8-sig")

    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF file.")

        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError(
                "PyMuPDF is required for PDF uploads. "
                "Install it using: pip install pymupdf"
            ) from exc

        parts = []

        with fitz.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise ValueError(
                    "Password-protected PDFs are not supported."
                )

            if len(pdf) > MAX_PDF_PAGES:
                raise ValueError(
                    "PDF exceeds the 100-page limit."
                )

            for page_number, page in enumerate(pdf, 1):
                text = page.get_text("text").strip()

                if text:
                    parts.append(
                        f"\n[Page {page_number}]\n{text}"
                    )

        return "\n".join(parts)

    raise ValueError(
        "Only TXT, MD, and PDF files are supported."
    )


def save_and_index(
    original_name: str,
    data: bytes,
) -> dict:
    text = extract_document(original_name, data).strip()

    if not text:
        raise ValueError(
            "No extractable text found. "
            "Scanned PDFs require OCR first."
        )

    DOCUMENT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_name = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        Path(original_name).stem,
    )[:60].strip("_") or "document"

    stored_name = (
        f"uploaded_{safe_name}_{uuid.uuid4().hex[:10]}.txt"
    )

    stored_path = DOCUMENT_DIR / stored_name

    stored_path.write_text(
        text,
        encoding="utf-8",
    )

    # Rebuild the existing development Medicine RAG index.
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.medicine_dev_index",
                "index",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if process.returncode != 0:
            raise RuntimeError(
                process.stderr[-1500:]
                or "Medicine indexing failed."
            )

    except Exception:
        # Avoid keeping an unindexed upload.
        stored_path.unlink(missing_ok=True)
        raise

    return {
        "success": True,
        "original_filename": original_name,
        "stored_filename": stored_name,
        "characters_indexed": len(text),
        "review_status": "draft_unreviewed",
        "message": "Document uploaded and Medicine RAG index rebuilt.",
    }


@router.post("/upload")
async def upload_medicine_document(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):

    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()

    if extension not in {".txt", ".md", ".pdf"}:
        raise HTTPException(
            status_code=400,
            detail="Upload a TXT, MD, or PDF document.",
        )

    data = await file.read(MAX_FILE_SIZE + 1)
    await file.close()

    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Maximum document size is 10 MB.",
        )

    if not data:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    async with INDEX_LOCK:
        try:
            return await asyncio.to_thread(
                save_and_index,
                filename,
                data,
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=504,
                detail="Medicine indexing timed out.",
            ) from exc

        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Document indexing failed. Check backend logs.",
            ) from exc
